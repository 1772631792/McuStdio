"""ELF metadata, persisted configuration, and backend utilities."""

from __future__ import annotations

import os
import sqlite3
import struct
import subprocess
import sys
from typing import Any, Dict

try:
    from elftools.dwarf.descriptions import describe_form_class
    from elftools.dwarf.dwarf_expr import DWARFExprParser
    from elftools.elf.elffile import ELFFile
except ImportError as exc:
    ELFFile = None
    DWARFExprParser = None
    describe_form_class = None
    ELFTOOLS_IMPORT_ERROR = exc
else:
    ELFTOOLS_IMPORT_ERROR = None

MAX_ARRAY_ELEMENTS = 4096
MAX_TYPE_DEPTH = 24
TCL_TERMINATOR = b"\x1a"


def receive_tcl_response(sock, chunk_size=4096):
    """Read one complete OpenOCD TCL response, including fragmented packets."""
    chunks = bytearray()
    while TCL_TERMINATOR not in chunks:
        block = sock.recv(chunk_size)
        if not block:
            raise ConnectionError("OpenOCD TCL connection closed")
        chunks.extend(block)
    return bytes(chunks).split(TCL_TERMINATOR, 1)[0].decode("utf-8", errors="replace").strip()


def launch_openocd(config):
    """Start only the configured OpenOCD instance and retain its process handle."""
    startupinfo = subprocess.STARTUPINFO()
    creationflags = 0
    if config.show_log_window:
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    else:
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    config.openocd_process = subprocess.Popen(
        [config.openocd_exe] + config.get_openocd_args(),
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    return config.openocd_process


def stop_managed_openocd(config):
    process = getattr(config, "openocd_process", None)
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)
    finally:
        config.openocd_process = None


def decode_sample_value(raw_value, channel):
    width = channel["width"]
    if channel["is_float"]:
        if width == 8:
            return struct.unpack("<d", int(raw_value).to_bytes(8, "little"))[0]
        return struct.unpack("<f", struct.pack("<I", int(raw_value) & 0xFFFFFFFF))[0]
    bits = width * 8
    value = int(raw_value) & ((1 << bits) - 1)
    if channel["is_signed"] and value & (1 << (bits - 1)):
        value -= 1 << bits
    return float(value)


def type_label(type_info: Dict[str, Any]) -> str:
    return str(type_info.get("name") or type_info.get("kind", "<unknown>"))


def scalar_size(type_info: Dict[str, Any]) -> int:
    if type_info.get("kind") not in ("scalar", "enum", "pointer"):
        return 0
    return int(type_info.get("size", 0) or 0)


class ElfDatabase:
    """Direct ELF symbol/DWARF reader. No source-project scan is required."""

    def __init__(self):
        self.path = ""
        self.symbols = {}
        self.types = {}
        self._type_cache = {}
        self._expr_parser = None

    @staticmethod
    def _text(value, default=""):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (str(value) if value is not None else default)

    def _member_offset(self, attr):
        if attr is None: return 0
        if describe_form_class(attr.form) == "constant": return int(attr.value)
        if describe_form_class(attr.form) in ("exprloc", "block") and self._expr_parser:
            try:
                ops = self._expr_parser.parse_expr(attr.value)
                if len(ops) == 1 and ops[0].op_name == "DW_OP_plus_uconst": return int(ops[0].args[0])
            except Exception: pass
        return 0

    def _parse_type(self, die):
        if die is None: return {"kind": "unknown", "name": "<unknown>", "size": 0}
        if die.offset in self._type_cache: return self._type_cache[die.offset]
        info = {"kind": "unknown", "name": "<unknown>", "size": 0}
        self._type_cache[die.offset] = info
        attrs = die.attributes
        name_attr = attrs.get("DW_AT_name")
        name = self._text(name_attr.value) if name_attr else ""
        size = int(attrs["DW_AT_byte_size"].value) if "DW_AT_byte_size" in attrs else 0

        def referenced():
            return self._parse_type(die.get_DIE_from_attribute("DW_AT_type")) if "DW_AT_type" in attrs else {"kind": "unknown", "name": "<unknown>", "size": 0}

        if die.tag == "DW_TAG_base_type":
            parsed = {"kind": "scalar", "name": name or "<scalar>", "size": size,
                      "encoding": int(attrs["DW_AT_encoding"].value) if "DW_AT_encoding" in attrs else 0}
        elif die.tag == "DW_TAG_enumeration_type":
            values = {}
            for child in die.iter_children():
                if child.tag == "DW_TAG_enumerator":
                    n, v = child.attributes.get("DW_AT_name"), child.attributes.get("DW_AT_const_value")
                    if n is not None and v is not None: values[int(v.value)] = self._text(n.value)
            parsed = {"kind": "enum", "name": name or "<enum>", "size": size or 4, "values": values}
        elif die.tag == "DW_TAG_pointer_type":
            target = referenced()
            parsed = {"kind": "pointer", "name": f"{type_label(target)} *", "size": size or 4, "target": target}
        elif die.tag == "DW_TAG_array_type":
            parsed = referenced()
            dimensions = []
            for child in die.iter_children():
                if child.tag != "DW_TAG_subrange_type": continue
                if "DW_AT_count" in child.attributes: dimensions.append(int(child.attributes["DW_AT_count"].value))
                elif "DW_AT_upper_bound" in child.attributes: dimensions.append(int(child.attributes["DW_AT_upper_bound"].value) + 1)
            for count in reversed(dimensions or [0]):
                parsed = {"kind": "array", "name": f"{type_label(parsed)}[{count or '?'}]",
                          "size": int(parsed.get("size", 0)) * count, "count": count, "element": parsed}
        elif die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            kind = "struct" if die.tag == "DW_TAG_structure_type" else "union"
            members = []
            for child in die.iter_children():
                if child.tag != "DW_TAG_member" or "DW_AT_type" not in child.attributes: continue
                n = child.attributes.get("DW_AT_name")
                members.append({"name": self._text(n.value, "<anonymous>") if n else "<anonymous>",
                                "offset": 0 if kind == "union" else self._member_offset(child.attributes.get("DW_AT_data_member_location")),
                                "type": self._parse_type(child.get_DIE_from_attribute("DW_AT_type"))})
            parsed = {"kind": kind, "name": name or f"<anonymous {kind}>", "size": size, "members": members}
        elif die.tag == "DW_TAG_typedef":
            parsed = dict(referenced()); parsed["name"] = name or type_label(parsed)
        elif die.tag in ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type", "DW_TAG_atomic_type"):
            parsed = referenced()
        else:
            parsed = {"kind": "unknown", "name": name or die.tag.replace("DW_TAG_", ""), "size": size}
        if parsed is not info: info.clear(); info.update(parsed)
        return info

    def load(self, path):
        if ELFFile is None: raise RuntimeError(f"缺少 pyelftools：{ELFTOOLS_IMPORT_ERROR}\n请执行 pip install pyelftools")
        symbols, types = {}, {}
        with open(path, "rb") as stream:
            elf = ELFFile(stream)
            allocated_ranges = [
                (int(section.header.sh_addr), int(section.header.sh_addr) + int(section.header.sh_size))
                for section in elf.iter_sections()
                if int(section.header.sh_flags) & 0x2 and int(section.header.sh_size) > 0
            ]

            def address_is_allocated(address):
                return any(start <= int(address) < end for start, end in allocated_ranges)

            for section in elf.iter_sections():
                if section.header.sh_type not in ("SHT_SYMTAB", "SHT_DYNSYM"): continue
                for symbol in section.iter_symbols():
                    entry = symbol.entry
                    if (entry["st_info"]["type"] == "STT_OBJECT" and symbol.name and
                            entry["st_shndx"] != "SHN_UNDEF" and address_is_allocated(entry["st_value"])):
                        symbols.setdefault(symbol.name, int(entry["st_value"]))
            if elf.has_dwarf_info():
                dwarf = elf.get_dwarf_info(); self._expr_parser = DWARFExprParser(dwarf.structs); self._type_cache = {}

                def variable_address(variable_die):
                    location = variable_die.attributes.get("DW_AT_location")
                    if location is None: return None
                    try:
                        if describe_form_class(location.form) not in ("exprloc", "block"): return None
                        operations = self._expr_parser.parse_expr(location.value)
                        address_op = next((op for op in operations if op.op_name == "DW_OP_addr"), None)
                        return int(address_op.args[0]) if address_op and address_op.args else None
                    except Exception:
                        return None

                def unique_storage_alias(variable_name, source_file, address):
                    stem = os.path.splitext(os.path.basename(source_file or "static"))[0]
                    stem = re.sub(r"\W+", "_", stem).strip("_") or "static"
                    if stem[0].isdigit(): stem = f"file_{stem}"
                    candidate = f"{stem}__{variable_name}"
                    suffix = 2
                    while candidate in symbols and symbols[candidate] != address:
                        candidate = f"{stem}__{variable_name}_{suffix}"
                        suffix += 1
                    return candidate

                for cu in dwarf.iter_CUs():
                    top_die = cu.get_top_DIE()
                    source_attr = top_die.attributes.get("DW_AT_name")
                    source_file = os.path.basename(self._text(source_attr.value)) if source_attr else ""
                    for die in cu.iter_DIEs():
                        if die.tag != "DW_TAG_variable": continue
                        if die.attributes.get("DW_AT_declaration") and die.attributes["DW_AT_declaration"].value: continue
                        declaration = die.get_DIE_from_attribute("DW_AT_specification") if "DW_AT_specification" in die.attributes else None
                        n = die.attributes.get("DW_AT_name") or (declaration.attributes.get("DW_AT_name") if declaration else None)
                        if not n: continue
                        variable_name = self._text(n.value)
                        type_die = die if "DW_AT_type" in die.attributes else declaration
                        if type_die is None or "DW_AT_type" not in type_die.attributes: continue
                        address = variable_address(die)
                        if address is None: address = symbols.get(variable_name)
                        if address is None or not address_is_allocated(address): continue
                        external = die.attributes.get("DW_AT_external") or (declaration.attributes.get("DW_AT_external") if declaration else None)
                        is_static = not bool(external and external.value)
                        storage_name = variable_name
                        if variable_name in types and symbols.get(variable_name) != address:
                            existing_type = types[variable_name]
                            if not is_static and existing_type.get("is_static"):
                                existing_alias = unique_storage_alias(
                                    variable_name, existing_type.get("source_file", ""), symbols[variable_name]
                                )
                                symbols[existing_alias] = symbols[variable_name]
                                types[existing_alias] = types.pop(variable_name)
                            else:
                                storage_name = unique_storage_alias(variable_name, source_file, address)
                        variable_type = dict(self._parse_type(type_die.get_DIE_from_attribute("DW_AT_type")))
                        variable_type.update({"is_static": is_static, "source_file": source_file, "original_name": variable_name})
                        symbols[storage_name] = int(address)
                        types[storage_name] = variable_type
        if not symbols: raise RuntimeError("ELF 中没有可用的全局数据符号")
        self.path, self.symbols, self.types = os.path.abspath(path), symbols, types

    def resolve(self, expression):
        match = re.match(r"^([A-Za-z_]\w*)", expression.strip())
        if not match: raise ValueError("无效变量表达式")
        root = match.group(1)
        if root not in self.symbols: raise KeyError(f"找不到变量：{root}")
        if root not in self.types: raise KeyError(f"变量 {root} 没有 DWARF 类型信息，请使用带 -g 的 ELF")
        address, info, pos = self.symbols[root], self.types[root], match.end()
        while pos < len(expression):
            rest = expression[pos:]
            index_match = re.match(r"^\[(\d+)\]", rest)
            if index_match:
                if info.get("kind") != "array": raise ValueError(f"{expression[:pos]} 不是数组")
                index, count = int(index_match.group(1)), int(info.get("count", 0))
                if not 0 <= index < count: raise IndexError("数组下标越界")
                element = info["element"]; address += index * int(element.get("size", 0)); info = element; pos += index_match.end(); continue
            member_match = re.match(r"^\.([A-Za-z_]\w*)", rest)
            if member_match:
                if info.get("kind") not in ("struct", "union"): raise ValueError(f"{expression[:pos]} 不是结构体/联合体")
                member = next((m for m in info.get("members", []) if m["name"] == member_match.group(1)), None)
                if member is None: raise KeyError(f"找不到成员：{member_match.group(1)}")
                address += int(member["offset"]); info = member["type"]; pos += member_match.end(); continue
            raise ValueError(f"无法解析：{expression[pos:]}")
        return address, info

    def flatten(self, root):
        address, root_info = self.resolve(root)
        records = []
        def visit(path, addr, info, depth, visited):
            records.append({"path": path, "type": type_label(info), "offset": addr - address, "info": info})
            if depth >= MAX_TYPE_DEPTH or id(info) in visited: return
            visited = set(visited); visited.add(id(info))
            if info.get("kind") == "array":
                element = info.get("element", {}); element_size = int(element.get("size", 0) or 0)
                for i in range(min(int(info.get("count", 0) or 0), MAX_ARRAY_ELEMENTS)):
                    visit(f"{path}[{i}]", addr + i * element_size, element, depth + 1, visited)
            elif info.get("kind") in ("struct", "union"):
                for member in info.get("members", []):
                    visit(f"{path}.{member['name']}", addr + int(member["offset"]), member["type"], depth + 1, visited)
        visit(root, address, root_info, 0, set())
        return records


class ProjectConfig:
    def __init__(self):
        self.elf_path = ""
        self.nm_path = ""
        self.backend_mode = "JLINK" 
        self.openocd_exe = ""
        self.interface = ""  
        self.target = ""     
        self.jlink_dll_dir = ""        
        self.jlink_device = "Cortex-M0+" 
        self.jlink_connect_device = "Cortex-M0+"
        self.svd_path = ""             
        self.openocd_host = "127.0.0.1"
        self.tcl_port = 6666
        self.show_log_window = False  
        self.openocd_process = None
        self.theme_mode = "DARK"

    def get_openocd_args(self):
        exe_dir = os.path.dirname(self.openocd_exe) if self.openocd_exe else ""
        scripts_path = os.path.abspath(os.path.join(exe_dir, "../share/openocd/scripts"))
        if not os.path.exists(scripts_path): scripts_path = os.path.abspath(os.path.join(exe_dir, "scripts"))
        
        args = ["-s", scripts_path, "-c", f"bindto {self.openocd_host}", "-c", f"tcl_port {self.tcl_port}", "-c", "gdb_port 3333"]
        if ":" in self.interface or "/" in self.interface or "\\" in self.interface:
            args += ["-f", self.interface]
        else:
            args += ["-f", f"interface/{self.interface}"]

        if ":" in self.target or "/" in self.target or "\\" in self.target:
            args += ["-f", self.target]
        else:
            args += ["-f", f"target/{self.target}"]
        return args

class LiveWatchDB:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.conn = sqlite3.connect(os.path.join(base_dir, "live_watch_history.db"))
        self.cursor = self.conn.cursor()
        try:
            self.cursor.execute("SELECT offset, base_var FROM variables LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("DROP TABLE IF EXISTS variables")

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS variables (
                name TEXT, type TEXT, offset INTEGER, base_var TEXT, tab_idx INTEGER DEFAULT 0,
                PRIMARY KEY (name, tab_idx)
            )''')
        self.cursor.execute('CREATE TABLE IF NOT EXISTS config_cache (key TEXT PRIMARY KEY, value TEXT)')
        self.cursor.execute('CREATE TABLE IF NOT EXISTS c_code_cache (id INTEGER PRIMARY KEY, code TEXT)')
        self.conn.commit()

    def save_var(self, name, var_type, offset=0, base_var="", tab_idx=0):
        self.cursor.execute("INSERT OR REPLACE INTO variables (name, type, offset, base_var, tab_idx) VALUES (?, ?, ?, ?, ?)", (name, var_type, offset, base_var, tab_idx))
        self.conn.commit()

    def delete_var(self, name, tab_idx=0):
        self.cursor.execute("DELETE FROM variables WHERE name=? AND tab_idx=?", (name, tab_idx))
        self.conn.commit()

    def get_all_vars(self):
        self.cursor.execute("SELECT name, type, offset, base_var, tab_idx FROM variables")
        return self.cursor.fetchall()

    def save_config(self, key, value):
        self.cursor.execute("INSERT OR REPLACE INTO config_cache (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def get_config(self, key, default=""):
        self.cursor.execute("SELECT value FROM config_cache WHERE key=?", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def save_c_code(self, code):
        self.cursor.execute("INSERT OR REPLACE INTO c_code_cache (id, code) VALUES (1, ?)", (code,))
        self.conn.commit()

    def get_c_code(self):
        self.cursor.execute("SELECT code FROM c_code_cache WHERE id=1")
        row = self.cursor.fetchone()
        return row[0] if row else ""
