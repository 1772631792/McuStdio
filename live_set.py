# live_set.py
import sys
import socket
import subprocess
import re
import time
import struct
import sqlite3
import os
import xml.etree.ElementTree as ET
import tempfile
import bisect
import platform  
import numpy as np 
from typing import Any, Dict

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QPushButton, QComboBox, QLabel, QLineEdit, 
                             QHeaderView, QMenu, QMessageBox, QDialog, QFileDialog, 
                             QFormLayout, QCheckBox, QSplitter, QTextEdit, QRadioButton, QButtonGroup,
                             QTabWidget, QTabBar)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QVariantAnimation, QMutex, QMutexLocker
from PyQt6.QtGui import QFont, QKeyEvent, QIntValidator, QDoubleValidator, QColor, QAction, QPen

import pyqtgraph as pg

pg.setConfigOptions(useOpenGL=False, antialias=True)

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

try:
    import pylink
except ImportError:
    pylink = None

DARK_STYLE = """
    QWidget { background-color: #1a1a1a; color: #e0e0e0; font-family: "Microsoft YaHei", "Consolas"; font-size: 12px; }
    QDialog, QMainWindow { background-color: #121212; }
    QLabel { background-color: transparent; }
    QLineEdit, QComboBox, QCheckBox, QTextEdit { background-color: #242424; border: 1px solid #3f3f46; padding: 5px; color: #ffffff; border-radius: 6px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #42a5f5; background-color: #2a2a2a; }
    QPushButton { background-color: #2d2d2d; border: 1px solid #4b5563; padding: 5px 12px; color: #ffffff; border-radius: 6px; font-weight: 500; }
    QPushButton:hover { background-color: #3d3d3d; border: 1px solid #60a5fa; }
    QPushButton:pressed { background-color: #1e3a5f; }
    QPushButton:disabled { background-color: #1f1f1f; color: #666666; border: 1px solid #2d2d2d; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #1a1a1a; color: #666666; border: 1px solid #2d2d2d; }
    QTreeWidget { background-color: #161616; border: 1px solid #2d2d2d; border-radius: 8px; alternate-background-color: #1c1c1c; }
    QTreeWidget::item { height: 26px; border-bottom: 1px solid #222222; }
    QTreeWidget::item:hover { background-color: #262626; }
    QTreeWidget::item:selected { background-color: #0d47a1; color: #ffffff; }
    QHeaderView::section { background-color: #222222; color: #b0bec5; padding: 6px; border: 1px solid #121212; font-weight: bold; }
    QSplitter::handle { background-color: #303030; }
    QTreeWidget QLineEdit { background-color: #2c2c2c !important; border: 2px solid #42a5f5 !important; color: #ffffff !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 3px; }
    QTabWidget::pane { border: 1px solid #2d2d2d; background-color: #161616; border-radius: 8px; top: -1px; }
    QTabBar::tab { background-color: #222222; color: #a3a3a3; border: 1px solid #2d2d2d; padding: 6px 15px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; margin-right: 2px; }
    QTabBar::tab:selected { background-color: #161616; color: #64b5f6; border-bottom-color: #161616; border-top: 2px solid #42a5f5; }
    QToolTip { background-color: #2d2d2d; color: #ffffff; border: 1px solid #64748b; padding: 4px; }
"""

LIGHT_STYLE = """
    QWidget { background-color: #f7f9fc; color: #1f2937; font-family: "Microsoft YaHei", "Consolas"; font-size: 12px; }
    QDialog, QMainWindow { background-color: #ffffff; }
    QLabel { background-color: transparent; }
    QLineEdit, QComboBox, QCheckBox, QTextEdit { background-color: #ffffff; border: 1px solid #cbd5e1; padding: 5px; color: #111827; border-radius: 6px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #1976d2; background-color: #ffffff; }
    QPushButton { background-color: #ffffff; border: 1px solid #cbd5e1; padding: 5px 12px; color: #1f2937; border-radius: 6px; font-weight: 500; }
    QPushButton:hover { background-color: #eef6ff; border: 1px solid #60a5fa; }
    QPushButton:pressed { background-color: #dbeafe; }
    QPushButton:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QTreeWidget { background-color: #ffffff; border: 1px solid #dbe3ec; border-radius: 8px; alternate-background-color: #f8fafc; }
    QTreeWidget::item { height: 26px; border-bottom: 1px solid #edf2f7; }
    QTreeWidget::item:hover { background-color: #eff6ff; }
    QTreeWidget::item:selected { background-color: #dbeafe; color: #0f3f73; }
    QHeaderView::section { background-color: #eef2f7; color: #475569; padding: 6px; border: 1px solid #dbe3ec; font-weight: bold; }
    QSplitter::handle { background-color: #dbe3ec; }
    QTreeWidget QLineEdit { background-color: #ffffff !important; border: 2px solid #1976d2 !important; color: #111827 !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 3px; }
    QTabWidget::pane { border: 1px solid #dbe3ec; background-color: #ffffff; border-radius: 8px; top: -1px; }
    QTabBar::tab { background-color: #eef2f7; color: #64748b; border: 1px solid #dbe3ec; padding: 6px 15px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; margin-right: 2px; }
    QTabBar::tab:selected { background-color: #ffffff; color: #1565c0; border-bottom-color: #ffffff; border-top: 2px solid #1976d2; }
    QToolTip { background-color: #ffffff; color: #111827; border: 1px solid #94a3b8; padding: 4px; }
"""

POPUP_MENU_STYLE = """
    QMenu { background-color: #ffffff; color: #1f2937; border: 1px solid #cbd5e1; padding: 6px; font-family: "Microsoft YaHei", "Consolas"; font-size: 13px; font-weight: bold; }
    QMenu::item { padding: 6px 28px 6px 16px; border-radius: 4px; background-color: transparent; color: #1f2937; }
    QMenu::item:selected { background-color: #dbeafe; color: #0f3f73; }
    QMenu::separator { height: 1px; background-color: #dbe3ec; margin: 4px 0px; }
"""

DARK_POPUP_MENU_STYLE = """
    QMenu { background-color: #222222; color: #ffffff; border: 1px solid #4b5563; padding: 6px; font-family: "Microsoft YaHei", "Consolas"; font-size: 13px; font-weight: bold; }
    QMenu::item { padding: 6px 28px 6px 16px; border-radius: 4px; background-color: transparent; color: #ffffff; }
    QMenu::item:selected { background-color: #0d47a1; color: #ffffff; }
    QMenu::separator { height: 1px; background-color: #4b5563; margin: 4px 0px; }
"""


def application_theme_stylesheet(theme_mode):
    return LIGHT_STYLE if str(theme_mode).upper() == "LIGHT" else DARK_STYLE

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


class ElfVariableDialog(QDialog):
    symbol_selected_signal = pyqtSignal(str)
    reload_requested_signal = pyqtSignal()
    def __init__(self, elf_db, parent=None):
        super().__init__(parent); self.elf_db = elf_db
        self.setWindowTitle("ELF 变量浏览器（符号表 + DWARF）"); self.resize(780, 620)
        layout = QVBoxLayout(self); self.search = QLineEdit(); self.search.setPlaceholderText("搜索 ELF 全局变量…"); layout.addWidget(self.search)
        self.elf_status = QLabel(); layout.addWidget(self.elf_status)
        self.tree = QTreeWidget(); self.tree.setColumnCount(4); self.tree.setHeaderLabels(["变量", "DWARF 类型", "地址", "存储属性"]); self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        reload_button = QPushButton("🔄 重新提取当前 ELF")
        reload_button.setToolTip("重新读取当前 ELF 的符号表和 DWARF，并刷新已有监视变量的地址与结构")
        reload_button.clicked.connect(lambda: self.reload_requested_signal.emit())
        buttons.addWidget(reload_button); buttons.addStretch()
        add = QPushButton("添加选中变量到监视"); add.clicked.connect(self._accept_item); buttons.addWidget(add); layout.addLayout(buttons)
        self.search.textChanged.connect(self._fill); self.tree.itemDoubleClicked.connect(lambda *_: self._accept_item()); self._fill()
    def _fill(self):
        keyword = self.search.text().strip().lower(); self.tree.clear()
        for name in sorted(self.elf_db.symbols):
            info = self.elf_db.types.get(name)
            storage = "static" if info and info.get("is_static") else "global"
            source_file = info.get("source_file", "") if info else ""
            storage_text = f"{storage} · {source_file}" if source_file else storage
            if info is None or (keyword and keyword not in name.lower() and keyword not in type_label(info).lower() and keyword not in storage_text.lower()): continue
            item = QTreeWidgetItem([name, type_label(info), f"0x{self.elf_db.symbols[name]:08X}", storage_text]); item.setData(0, Qt.ItemDataRole.UserRole, name); self.tree.addTopLevelItem(item)
        self.elf_status.setText(
            f"ELF：{self.elf_db.path or '未加载'}  |  "
            f"符号 {len(self.elf_db.symbols)} 个，DWARF 变量 {len(self.elf_db.types)} 个"
        )
    def replace_elf_database(self, elf_db):
        self.elf_db = elf_db
        self._fill()
    def _accept_item(self):
        item = self.tree.currentItem()
        if item: self.symbol_selected_signal.emit(item.data(0, Qt.ItemDataRole.UserRole)); self.close()

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


class JLinkDeviceDialog(QDialog):
    """Device selector backed primarily by JFlash.exe's JLinkDevices.xml."""

    device_selected = pyqtSignal(str, str)

    def __init__(self, dll_dir, current_device="", parent=None):
        super().__init__(parent)
        self.dll_dir = os.path.dirname(dll_dir) if os.path.isfile(dll_dir) else dll_dir
        self.devices = []
        self.load_warnings = []
        self.setWindowTitle("SEGGER J-Link 芯片选择")
        self.resize(820, 650)

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.vendor_combo = QComboBox()
        self.vendor_combo.setMinimumWidth(190)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索芯片型号，例如 STM32F103 / PY32F030…")
        filters.addWidget(QLabel("厂商："))
        filters.addWidget(self.vendor_combo)
        filters.addWidget(QLabel("型号："))
        filters.addWidget(self.search_edit, 1)
        layout.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["厂商", "J-Link 芯片型号", "Flash 起始", "Flash 大小", "RAM 大小", "来源"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

        self.status = QLabel("正在读取 SEGGER 器件库…")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        select = QPushButton("使用选中芯片")
        cancel.clicked.connect(self.reject)
        select.clicked.connect(self._choose)
        buttons.addWidget(cancel)
        buttons.addWidget(select)
        layout.addLayout(buttons)

        self.vendor_combo.currentTextChanged.connect(self._filter)
        self.search_edit.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(lambda *_: self._choose())
        try:
            self._load_devices()
            # Generic core names are valid J-Link fallbacks, but they are not
            # actual entries users normally want to search in the MCU list.
            if current_device and not current_device.upper().startswith("CORTEX-"):
                self.search_edit.setText(current_device)
        except Exception as exc:
            self.status.setText(f"读取 J-Link 芯片库失败：{exc}")

    @staticmethod
    def _size_text(size):
        size = int(size or 0)
        if not size: return "-"
        if size % (1024 * 1024) == 0: return f"{size // (1024 * 1024)} MB"
        if size % 1024 == 0: return f"{size // 1024} KB"
        return f"{size} B"

    def _load_devices(self):
        records = {}
        xml_path = os.path.join(self.dll_dir, "JLinkDevices.xml")
        if os.path.exists(xml_path):
            try:
                root = ET.parse(xml_path).getroot()
                last_name = None
                for element in root.iter():
                    if element.tag == "ChipInfo":
                        name = element.get("Name", "").strip()
                        if not name: continue
                        core = self._core_name(element.get("Core", ""))
                        records[name.lower()] = [element.get("Vendor", "Unspecified"), name, 0, 0,
                                                 self._parse_int(element.get("WorkRAMSize")), "JLinkDevices.xml", core]
                        last_name = name.lower()
                    elif element.tag == "FlashBankInfo" and last_name in records:
                        record = records[last_name]
                        if not record[2]: record[2] = self._parse_int(element.get("BaseAddr"))
                        record[3] += self._parse_int(element.get("MaxSize"))
            except Exception as exc:
                self.load_warnings.append(f"XML: {exc}")
        else:
            self.load_warnings.append(f"未找到 {xml_path}")

        # The DLL list supplements devices that are not present in the XML.
        if pylink is not None:
            is_64bit = "64" in platform.architecture()[0]
            candidates = ["JLink_x64.dll", "JLinkARM.dll"] if is_64bit else ["JLinkARM.dll", "JLink_x64.dll"]
            dll_file = next((os.path.join(self.dll_dir, name) for name in candidates if os.path.exists(os.path.join(self.dll_dir, name))), None)
            if dll_file:
                try:
                    link = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                    for index in range(link.num_supported_devices()):
                        info = link.supported_device(index)
                        key = info.name.lower()
                        if key not in records:
                            records[key] = [info.manufacturer or "Unspecified", info.name, int(info.FlashAddr),
                                            int(info.FlashSize), int(info.RAMSize), "J-Link DLL", info.name]
                        else:
                            # Exact DLL support is preferred for connecting; XML-only
                            # extension devices fall back to their declared CPU core.
                            records[key][5] = "JLinkDevices.xml + DLL"
                            records[key][6] = info.name
                except Exception as exc:
                    self.load_warnings.append(f"DLL: {exc}")
            else:
                self.load_warnings.append("当前目录没有 JLink_x64.dll/JLinkARM.dll")
        if not records:
            raise RuntimeError(f"未找到器件数据库：{xml_path}")
        self.devices = list(records.values())
        vendors = sorted({record[0] for record in self.devices}, key=str.lower)
        self.vendor_combo.blockSignals(True)
        self.vendor_combo.addItem("全部厂商")
        self.vendor_combo.addItems(vendors)
        self.vendor_combo.blockSignals(False)
        self._filter()

    @staticmethod
    def _parse_int(value):
        try: return int(value or "0", 0)
        except (TypeError, ValueError): return 0

    @staticmethod
    def _core_name(value):
        core = (value or "").replace("JLINK_CORE_", "").replace("_", "-")
        aliases = {"CORTEX-M0": "Cortex-M0", "CORTEX-M0+": "Cortex-M0+", "CORTEX-M1": "Cortex-M1",
                   "CORTEX-M3": "Cortex-M3", "CORTEX-M4": "Cortex-M4", "CORTEX-M7": "Cortex-M7",
                   "CORTEX-M23": "Cortex-M23", "CORTEX-M33": "Cortex-M33", "CORTEX-M55": "Cortex-M55"}
        return aliases.get(core, core.title() if core else "")

    def _filter(self):
        vendor = self.vendor_combo.currentText()
        keyword = self.search_edit.text().strip().lower()
        self.tree.clear()
        shown = 0
        for manufacturer, name, flash_addr, flash_size, ram_size, source, connect_name in self.devices:
            if vendor and vendor != "全部厂商" and manufacturer != vendor: continue
            if keyword and keyword not in name.lower(): continue
            item = QTreeWidgetItem([manufacturer, name, f"0x{flash_addr:08X}" if flash_addr else "-",
                                    self._size_text(flash_size), self._size_text(ram_size), source])
            item.setData(0, Qt.ItemDataRole.UserRole, (name, connect_name or name))
            self.tree.addTopLevelItem(item)
            shown += 1
        xml_count = sum(record[5].startswith("JLinkDevices.xml") for record in self.devices)
        xml_path = os.path.join(self.dll_dir, "JLinkDevices.xml")
        warning = f" | 警告: {'; '.join(self.load_warnings)}" if self.load_warnings else ""
        self.status.setText(f"共 {len(self.devices)} 个型号（XML {xml_count} 个），当前显示 {shown} 个 | {xml_path}{warning}")

    def _choose(self):
        item = self.tree.currentItem()
        if not item: return
        name, connect_name = item.data(0, Qt.ItemDataRole.UserRole)
        self.device_selected.emit(name, connect_name)
        self.accept()

class LoginConfigDialog(QDialog):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.config = ProjectConfig()
        self.is_connected = False
        self.setWindowTitle("MCU Studio - 启动配置工作站")
        self.resize(700, 590)
        self.init_ui()
        self.load_saved_config()
        self.toggle_engine_fields()

        self.detect_timer = QTimer(self)
        self.detect_timer.timeout.connect(self.static_env_check)
        self.detect_timer.start(1000)

    def init_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["🌙 夜间模式（默认）", "☀️ 白天模式"])
        self.theme_combo.currentIndexChanged.connect(self.apply_selected_theme)
        form.addRow("🎨 界面主题:", self.theme_combo)
        
        self.elf_edit = QLineEdit()
        btn_elf = QPushButton("...")
        btn_elf.clicked.connect(lambda: self.select_file(self.elf_edit, "选择 ELF 固件", "ELF Files (*.elf)"))
        h1 = QHBoxLayout(); h1.addWidget(self.elf_edit); h1.addWidget(btn_elf)
        form.addRow("🔄 固件编译文件 (.elf):", h1)

        form.addRow(QLabel("<hr/><b>🛠️ 调试器驱动内核主选择：</b>"))

        self.driver_mode_combo = QComboBox()
        self.driver_mode_combo.addItems(["J-Link 原生内核驱动 (无需OpenOCD/无需cfg)", "CMSIS-DAP / ST-Link (基于 OpenOCD)"])
        self.driver_mode_combo.currentIndexChanged.connect(self.toggle_engine_fields)
        form.addRow("🔌 调试驱动架构派系:", self.driver_mode_combo)

        self.lbl_jl_dir = QLabel("📂 J-Link 驱动安装根路径:")
        self.jlink_dir_edit = QLineEdit()
        self.btn_jl_dir = QPushButton("...")
        self.btn_jl_dir.clicked.connect(self.select_jlink_dir)
        self.h_jl_dir = QHBoxLayout(); self.h_jl_dir.addWidget(self.jlink_dir_edit); self.h_jl_dir.addWidget(self.btn_jl_dir)

        self.lbl_jl_dev = QLabel("🎯 芯片特定型号 (Device):")
        self.jlink_device_edit = QLineEdit("Cortex-M0+")
        self.jlink_device_edit.textEdited.connect(lambda text: self.jlink_device_edit.setProperty("connect_device", text.strip()))
        self.btn_jl_device = QPushButton("选择芯片…")
        self.btn_jl_device.clicked.connect(self.open_jlink_device_selector)
        self.h_jl_device = QHBoxLayout(); self.h_jl_device.addWidget(self.jlink_device_edit); self.h_jl_device.addWidget(self.btn_jl_device)

        self.lbl_jl_svd = QLabel("📋 SVD（可选，仅用于外设寄存器名称）:")
        self.svd_edit = QLineEdit()
        self.svd_edit.setPlaceholderText("可留空：不影响 ELF 烧录、DWARF 变量或波形调试")
        self.svd_edit.setToolTip("SVD 只描述外设寄存器名称/位域；J-Link Flash 算法来自所选 DLL/XML，与 SVD 无关。")
        self.btn_svd = QPushButton("...")
        self.btn_svd.clicked.connect(lambda: self.select_file(self.svd_edit, "选择外设 SVD 描述文件", "SVD Files (*.svd *.txt)"))
        self.btn_clear_svd = QPushButton("清空")
        self.btn_clear_svd.setToolTip("清除上一颗芯片遗留的 SVD；不影响烧录和调试")
        self.btn_clear_svd.clicked.connect(self.svd_edit.clear)
        self.h_jl_svd = QHBoxLayout(); self.h_jl_svd.addWidget(self.svd_edit); self.h_jl_svd.addWidget(self.btn_svd); self.h_jl_svd.addWidget(self.btn_clear_svd)

        form.addRow(self.lbl_jl_dir, self.h_jl_dir)
        form.addRow(self.lbl_jl_dev, self.h_jl_device)
        form.addRow(self.lbl_jl_svd, self.h_jl_svd)

        self.lbl_ocd_exe = QLabel("🚀 OpenOCD 路径 (openocd.exe):")
        self.openocd_edit = QLineEdit()
        self.btn_ocd = QPushButton("...")
        self.btn_ocd.clicked.connect(lambda: self.select_file(self.openocd_edit, "选择 openocd.exe", "Executable (*.exe)"))
        self.h_ocd_exe = QHBoxLayout(); self.h_ocd_exe.addWidget(self.openocd_edit); self.h_ocd_exe.addWidget(self.btn_ocd)

        self.lbl_ocd_intf = QLabel("🔌 硬件调试器种类 (.cfg):")
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.interface_combo.addItems(["cmsis-dap.cfg", "stlink.cfg"])
        self.btn_browse_intf = QPushButton("...")
        self.btn_browse_intf.clicked.connect(lambda: self.select_cfg_file(self.interface_combo, "interface"))
        self.h_ocd_intf = QHBoxLayout(); self.h_ocd_intf.addWidget(self.interface_combo); self.h_ocd_intf.addWidget(self.btn_browse_intf)

        self.lbl_ocd_targ = QLabel("🎯 目标芯片内核型号 (.cfg):")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.addItems(["stm32f1x.cfg", "stm32f4x.cfg", "py32f0xx.cfg", "stm32f0x.cfg"])
        self.btn_browse_target = QPushButton("...")
        self.btn_browse_target.clicked.connect(lambda: self.select_cfg_file(self.target_combo, "target"))
        self.h_ocd_targ = QHBoxLayout(); self.h_ocd_targ.addWidget(self.target_combo); self.h_ocd_targ.addWidget(self.btn_browse_target)

        form.addRow(self.lbl_ocd_exe, self.h_ocd_exe)
        form.addRow(self.lbl_ocd_intf, self.h_ocd_intf)
        form.addRow(self.lbl_ocd_targ, self.h_ocd_targ)

        self.log_checkbox = QCheckBox("显示硬件调试日志 to 独立黑框 (仅限OpenOCD)")
        form.addRow("📋 调试黑框开关:", self.log_checkbox)

        layout.addLayout(form)
        layout.addSpacing(10)
        
        self.lbl_hardware_status = QLabel("🔍 准备就绪。")
        self.lbl_hardware_status.setStyleSheet("color: #b45309; font-weight: bold; padding: 5px;")
        layout.addWidget(self.lbl_hardware_status)
        
        btns = QHBoxLayout()
        self.btn_connect = QPushButton("⚡ 初始化硬件并进入工作站")
        self.btn_connect.setStyleSheet("background-color: #1e88e5; font-weight: bold; padding: 10px 24px; color: white; font-size: 13px;")
        self.btn_connect.clicked.connect(self.handle_connect)
        btns.addStretch()
        btns.addWidget(self.btn_connect)
        layout.addLayout(btns)
        self.setLayout(layout)

    def toggle_engine_fields(self):
        is_jlink = (self.driver_mode_combo.currentIndex() == 0)
        self.jlink_dir_edit.setEnabled(is_jlink)
        self.btn_jl_dir.setEnabled(is_jlink)
        self.jlink_device_edit.setEnabled(is_jlink)
        self.btn_jl_device.setEnabled(is_jlink)
        self.svd_edit.setEnabled(is_jlink)
        self.btn_svd.setEnabled(is_jlink)
        self.btn_clear_svd.setEnabled(is_jlink)

        self.openocd_edit.setEnabled(not is_jlink)
        self.btn_ocd.setEnabled(not is_jlink)
        self.interface_combo.setEnabled(not is_jlink)
        self.btn_browse_intf.setEnabled(not is_jlink)
        self.target_combo.setEnabled(not is_jlink)
        self.btn_browse_target.setEnabled(not is_jlink)
        self.log_checkbox.setEnabled(not is_jlink)
        self.static_env_check()

    def apply_selected_theme(self):
        theme_mode = "LIGHT" if self.theme_combo.currentIndex() == 1 else "DARK"
        self.config.theme_mode = theme_mode
        app = QApplication.instance()
        if app: app.setStyleSheet(application_theme_stylesheet(theme_mode))
        self.db.save_config("theme_mode", theme_mode)
        if hasattr(self, 'lbl_hardware_status'): self.static_env_check()

    def static_env_check(self):
        is_jlink_mode = (self.driver_mode_combo.currentIndex() == 0)
        is_dark = self.config.theme_mode == "DARK"
        error_color = "#ff6b6b" if is_dark else "#c62828"
        success_color = "#69db7c" if is_dark else "#2e7d32"
        info_color = "#64b5f6" if is_dark else "#0277bd"
        warning_color = "#ffd166" if is_dark else "#b45309"
        if is_jlink_mode:
            if pylink is None:
                self.lbl_hardware_status.setText("❌ 未检测到 pylink 库，请执行 pip install pylink-square")
                self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")
            else:
                self.lbl_hardware_status.setText("🟢 J-Link 原生运行环境就绪。")
                self.lbl_hardware_status.setStyleSheet(f"color: {success_color}; font-weight: bold;")
        else:
            exe = self.openocd_edit.text().strip()
            if exe and os.path.exists(exe):
                self.lbl_hardware_status.setText("🟡 OpenOCD 工具路径就绪。")
                self.lbl_hardware_status.setStyleSheet(f"color: {info_color}; font-weight: bold;")
            else:
                self.lbl_hardware_status.setText("⚠️ OpenOCD 路径待完善，可直接进入面板。")
                self.lbl_hardware_status.setStyleSheet(f"color: {warning_color}; font-weight: bold;")

    def select_file(self, edit, title, flt):
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if path: edit.setText(os.path.normpath(path))

    def select_jlink_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 SEGGER J-Link 安装根目录")
        if path: 
            self.jlink_dir_edit.setText(os.path.normpath(path))
            self.static_env_check()

    def open_jlink_device_selector(self):
        dll_dir = self.jlink_dir_edit.text().strip()
        dialog = JLinkDeviceDialog(dll_dir, self.jlink_device_edit.text().strip(), self)
        dialog.device_selected.connect(self.apply_jlink_device_selection)
        dialog.exec()

    def apply_jlink_device_selection(self, display_name, connect_name):
        previous_device = self.jlink_device_edit.text().strip()
        self.jlink_device_edit.setText(display_name)
        self.jlink_device_edit.setProperty("connect_device", connect_name)
        if connect_name != display_name:
            self.jlink_device_edit.setToolTip(f"XML 扩展器件；实时调试按内核 {connect_name} 连接")
        else:
            self.jlink_device_edit.setToolTip("")
        if previous_device and previous_device.casefold() != display_name.casefold() and self.svd_edit.text().strip():
            self.svd_edit.clear()
            self.svd_edit.setPlaceholderText(f"已从 {previous_device} 切换到 {display_name}；旧 SVD 已清除（SVD 不影响烧录）")

    def select_cfg_file(self, combo, subfolder):
        exe_path = self.openocd_edit.text().strip()
        initial_dir = ""
        if exe_path and os.path.exists(exe_path):
            scripts = os.path.abspath(os.path.join(os.path.dirname(exe_path), f"../share/openocd/scripts/{subfolder}"))
            if os.path.exists(scripts): initial_dir = scripts
        path, _ = QFileDialog.getOpenFileName(self, f"选择自定义 {subfolder} 配置文件", initial_dir, "Config Files (*.cfg)")
        if path: combo.setEditText(os.path.normpath(path))

    def load_saved_config(self):
        self.elf_edit.setText(self.db.get_config("elf_path"))
        self.openocd_edit.setText(self.db.get_config("openocd_exe"))
        self.jlink_dir_edit.setText(self.db.get_config("jlink_dll_dir", "C:\\Program Files\\SEGGER\\JLink"))
        self.jlink_device_edit.setText(self.db.get_config("jlink_device", "Cortex-M0+"))
        self.jlink_device_edit.setProperty("connect_device", self.db.get_config("jlink_connect_device", self.jlink_device_edit.text()))
        self.svd_edit.setText(self.db.get_config("svd_path"))
        saved_theme = self.db.get_config("theme_mode", "DARK").upper()
        self.theme_combo.setCurrentIndex(1 if saved_theme == "LIGHT" else 0)
        self.apply_selected_theme()
        
        saved_mode = self.db.get_config("backend_mode", "JLINK")
        self.driver_mode_combo.setCurrentIndex(0 if saved_mode == "JLINK" else 1)
        
        saved_intf = self.db.get_config("last_interface_cfg")
        if saved_intf: self.interface_combo.setEditText(saved_intf)
        saved_targ = self.db.get_config("last_target_cfg")
        if saved_targ: self.target_combo.setEditText(saved_targ)

    def handle_connect(self):
        if hasattr(self, 'detect_timer'): self.detect_timer.stop()
        self.config.elf_path = self.elf_edit.text().strip()
        self.config.backend_mode = "JLINK" if self.driver_mode_combo.currentIndex() == 0 else "OPENOCD"
        self.config.theme_mode = "LIGHT" if self.theme_combo.currentIndex() == 1 else "DARK"
        
        self.db.save_config("elf_path", self.config.elf_path)
        self.db.save_config("backend_mode", self.config.backend_mode)
        self.db.save_config("theme_mode", self.config.theme_mode)

        if self.config.backend_mode == "OPENOCD":
            self.config.openocd_exe = self.openocd_edit.text().strip()
            self.config.interface = self.interface_combo.currentText().strip()
            self.config.target = self.target_combo.currentText().strip()
            self.config.show_log_window = self.log_checkbox.isChecked()

            self.db.save_config("openocd_exe", self.config.openocd_exe)
            self.db.save_config("last_interface_cfg", self.config.interface)
            self.db.save_config("last_target_cfg", self.config.target)

            if self.config.openocd_exe and os.path.exists(self.config.openocd_exe):
                stop_managed_openocd(self.config)
                launch_openocd(self.config)
                time.sleep(0.5)
        else: 
            self.config.jlink_dll_dir = self.jlink_dir_edit.text().strip()
            self.config.jlink_device = self.jlink_device_edit.text().strip()
            self.config.jlink_connect_device = self.jlink_device_edit.property("connect_device") or self.config.jlink_device
            self.config.svd_path = self.svd_edit.text().strip()
            self.db.save_config("jlink_dll_dir", self.config.jlink_dll_dir)
            self.db.save_config("jlink_device", self.config.jlink_device)
            self.db.save_config("jlink_connect_device", self.config.jlink_connect_device)
            self.db.save_config("svd_path", self.config.svd_path)

        self.is_connected = True
        self.accept()


class InPlaceEditLineEdit(QLineEdit):
    editing_finished_signal = pyqtSignal(str)

    def __init__(self, parent, initial_text, val_type, type_info=None):
        super().__init__(parent)
        self.setText(initial_text)
        if type_info and type_info.get("kind") == "enum":
            self.setPlaceholderText("可输入枚举名或数值")
        elif type_info and (type_info.get("kind") == "pointer" or int(type_info.get("encoding", 0) or 0) == 0x02):
            self.setPlaceholderText("可输入 true/false 或 0x 数值")
        elif (type_info and int(type_info.get("encoding", 0) or 0) == 0x04) or "float" in val_type or "double" in val_type:
            self.setValidator(QDoubleValidator(self))
        else:
            self.setValidator(QIntValidator(-2147483648, 2147483647, self))
        self.selectAll()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editing_finished_signal.emit(self.text().strip())
        elif event.key() == Qt.Key.Key_Escape:
            self.editing_finished_signal.emit("")  
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.editing_finished_signal.emit(self.text().strip())
        super().focusOutEvent(event)


class ElfFlashWorker(QThread):
    """Run J-Link Commander and reconnect without blocking the Qt GUI."""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, commander, jlink_dir, elf_path, flash_device, connect_device, loader_config=None, parent=None):
        super().__init__(parent)
        self.commander = commander
        self.jlink_dir = jlink_dir
        self.elf_path = elf_path
        self.flash_device = flash_device
        self.connect_device = connect_device
        self.loader_config = loader_config

    def _reconnect_without_reset(self):
        if pylink is None: return None
        is_64bit = "64" in platform.architecture()[0]
        candidates = ["JLink_x64.dll", "JLinkARM.dll"] if is_64bit else ["JLinkARM.dll", "JLink_x64.dll"]
        dll_file = next((os.path.join(self.jlink_dir, name) for name in candidates
                         if os.path.isfile(os.path.join(self.jlink_dir, name))), None)
        link = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file)) if dll_file else pylink.JLink()
        link.open()
        link.set_tif(pylink.enums.JLinkInterfaces.SWD)
        link.connect(self.connect_device, speed=4000)
        return link

    def run(self):
        script_path = ""
        run_commander = self.commander
        command_device = self.flash_device
        success = False
        message = ""
        link = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".jlink", encoding="utf-8", newline="\n", delete=False
            ) as script:
                script_path = script.name
                # Reset commands are permitted only because this worker is
                # created by an explicitly confirmed Flash button action.
                script.write("r\n")
                script.write(f'loadfile "{self.elf_path}"\n')
                script.write("r\n")
                script.write("qc\n")
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            process_env = os.environ.copy()
            process_env["PATH"] = self.jlink_dir + os.pathsep + process_env.get("PATH", "")
            command = [run_commander, "-device", command_device]
            command.extend([
                "-if", "SWD",
                "-speed", "4000",
                "-autoconnect", "1",
                "-ExitOnError", "1",
                "-CommanderScript", script_path,
            ])
            result = subprocess.run(
                command,
                cwd=self.jlink_dir,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=180,
                creationflags=creationflags,
            )
            output = result.stdout or ""
            failed = result.returncode != 0 or any(
                token in output.lower() for token in ("error:", "failed to", "cannot connect")
            )
            if failed:
                message = output[-4000:] or f"JLink.exe 退出码：{result.returncode}"
            else:
                success = True
                message = f"ELF 已烧录并复位运行：\n{self.elf_path}"
        except subprocess.TimeoutExpired:
            message = "J-Link 烧录超过 180 秒，已停止等待。"
        except Exception as exc:
            message = str(exc)
        finally:
            if script_path:
                try: os.remove(script_path)
                except OSError: pass
            try:
                link = self._reconnect_without_reset()
            except Exception as exc:
                message += f"\n\n烧录后非复位重连失败：{exc}"
            self.finished_signal.emit(success, message, link)


class BackendCloseWorker(QThread):
    """Release a native probe handle without ever blocking the Qt GUI thread."""

    finished_signal = pyqtSignal(str)

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link

    def run(self):
        error = ""
        try:
            if self.link:
                self.link.close()
        except Exception as exc:
            error = str(exc)
        finally:
            self.link = None
            self.finished_signal.emit(error)


class AdvanceCParserDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("高级 C 语言复杂嵌套结构体智能提炼工作台")
        self.resize(750, 550)
        
        layout = QVBoxLayout()
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("例如：\ntypedef struct {\n    float Kp;\n    float Ki;\n} PID_t;\n\nPID_t motor_pid;")
        self.editor.setFont(QFont("Consolas", 11))
        layout.addWidget(self.editor)
        
        btns = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_run = QPushButton("🔍 智能树化解析并同步至全局主表")
        btn_run.setStyleSheet("background-color: #1976d2; color: white; font-weight: bold; padding: 6px 16px;")
        btn_run.clicked.connect(self.do_parse)
        
        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_run)
        layout.addLayout(btns)
        self.setLayout(layout)
        self.editor.setText(self.db.get_c_code())

    def do_parse(self):
        code = self.editor.toPlainText().strip()
        if not code: return
        self.db.save_c_code(code)
        self.accept()


class MCUStudioWindow(QMainWindow):
    def __init__(self, config, db):
        super().__init__()
        self.config = config
        self.db = db
        self.is_dark_theme = getattr(config, 'theme_mode', 'DARK').upper() == 'DARK'
        self.theme_colors = ({
            'plot_bg': '#111111', 'axis': '#b0bec5', 'axis_text': '#e0e0e0',
            'crosshair': '#90a4ae', 'hover_pen': '#ffffff', 'hover_text': '#ffffff',
            'hover_fill': '#263238', 'splitter': '#303030', 'panel': '#161616',
            'panel_border': '#303030', 'neutral_button': '#2d2d2d',
            'neutral_text': '#ffffff', 'status_ok': '#69db7c',
            'channels': ['#00E676', '#FFD600', '#00E5FF'],
        } if self.is_dark_theme else {
            'plot_bg': '#ffffff', 'axis': '#64748b', 'axis_text': '#374151',
            'crosshair': '#64748b', 'hover_pen': '#1f2937', 'hover_text': '#111827',
            'hover_fill': '#f1f5f9', 'splitter': '#dbe3ec', 'panel': '#ffffff',
            'panel_border': '#dbe3ec', 'neutral_button': '#ffffff',
            'neutral_text': '#1f2937', 'status_ok': '#2e7d32',
            'channels': ['#008a3b', '#d97706', '#0284c7'],
        })
        self.popup_menu_style = DARK_POPUP_MENU_STYLE if self.is_dark_theme else POPUP_MENU_STYLE
        self.setWindowTitle(f"MCU Live Watch Studio - [{'原生J-Link独占' if config.backend_mode=='JLINK' else 'OpenOCD驱动'}]")
        self.resize(1450, 850)
        
        self.elf_db = ElfDatabase()
        self.symbol_cache = {}
        self.worker = None 
        self.retired_workers = []
        self.backend_close_workers = []
        self.backend_suspended = False
        self.flash_sequence_active = False
        self.elf_reload_in_progress = False
        self.jlink_native_instance = None 
        self.openocd_link_alive = False  
        
        self.ch_paths = [None, None, None]     
        self.ch_types = [None, None, None]     
        self.ch_addrs = [None, None, None]     
        self.ch_buffers = [[], [], []]         
        self.ch_time_stamps = [[], [], []]     
        
        self.buffer_mutex = QMutex()
        self.layout_switching = False
        self.current_layout_id = 1
        
        self.plot_active_paths = [[], [], []] 
        self.plot_curves_dict = [{}, {}, {}] 
        
        self.start_time = time.time()          
        self.max_display_duration_ms = 5000    
        self.is_frozen = False
        self.canvas_adapt_start_ms = None
        self.canvas_fixed_ranges = [None, None, None]
        self.tcl_socket = None
        self.heartbeat_state = False

        self.init_global_link_backend()
        self.load_elf_symbols()  
        self.init_ui()
        
        self.restore_layout_and_type_configs()

        self.timer_table = QTimer()
        self.timer_table.timeout.connect(self.low_speed_tree_refresh)
        self.timer_table.start(200)

        self.timer_plot = QTimer()
        self.timer_plot.timeout.connect(self.update_plot_canvas)
        self.timer_plot.start(30) 

        self.timer_heartbeat = QTimer()
        self.timer_heartbeat.timeout.connect(self.update_hardware_heartbeat)
        self.timer_heartbeat.start(300)

        self.load_history_from_db()

        # Watch the selected ELF itself (not its project directory).  Requiring
        # the same changed signature twice avoids parsing while the linker is
        # still replacing/writing the file.
        self.elf_last_signature = self._elf_file_signature()
        self.elf_pending_signature = None
        self.elf_pending_stable_ticks = 0
        self.elf_watch_timer = QTimer(self)
        self.elf_watch_timer.timeout.connect(self.check_elf_file_changed)
        self.elf_watch_timer.start(1000)

    def init_global_link_backend(self):
        if self.backend_suspended:
            return
        if self.config.backend_mode == "OPENOCD":
            try:
                self.tcl_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcl_socket.settimeout(0.3)
                self.tcl_socket.connect((self.config.openocd_host, self.config.tcl_port))
                self.openocd_link_alive = True
                print("⚡ 主控 TCL 管道建立成功。")
            except Exception as e:
                self.openocd_link_alive = False
                self.tcl_socket = None
                print(f"⚠️ 管道初始连接未就绪: {e}")
        else:
            try:
                if pylink is None: return
                is_64bit_python = "64" in platform.architecture()[0]
                dll_name = "JLink_x64.dll" if is_64bit_python else "JLinkARM.dll"
                dll_file = os.path.join(self.config.jlink_dll_dir, dll_name)
                
                if not os.path.exists(dll_file):
                    fallback_name = "JLinkARM.dll" if is_64bit_python else "JLink_x64.dll"
                    dll_file = os.path.join(self.config.jlink_dll_dir, fallback_name)
                
                if os.path.exists(dll_file):
                    os.environ['PATH'] = self.config.jlink_dll_dir + os.pathsep + os.environ.get('PATH', '')
                    self.jlink_native_instance = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                else:
                    self.jlink_native_instance = pylink.JLink()
                    
                self.jlink_native_instance.open()
                self.jlink_native_instance.set_tif(pylink.enums.JLinkInterfaces.SWD)
                self.jlink_native_instance.connect(self.config.jlink_connect_device, speed=4000)
                print("⚡ 原生 J-Link 内核握手成功！")
            except Exception as e:
                self.jlink_native_instance = None
                print(f"❌ 原生 J-Link 内核打开失败: {e}")

    def load_elf_symbols(self):  
        try:
            if self.config.elf_path and os.path.exists(self.config.elf_path):
                self.elf_db.load(self.config.elf_path)
                self.symbol_cache = dict(self.elf_db.symbols)
                print(f"ELF loaded: {len(self.symbol_cache)} symbols, {len(self.elf_db.types)} DWARF variables")
                return
            print(f"⚡ ELF 符号表加载成功，共包含 {len(self.symbol_cache)} 个有效全局变量。")
        except Exception as e:
            print(f"❌ ELF 符号表加载异常: {e}")

    def reload_elf_symbols(self, show_message=True, source="manual"):
        """Atomically reload ELF/DWARF and refresh saved trees and channel addresses."""
        elf_path = os.path.abspath(self.config.elf_path) if self.config.elf_path else ""
        if not os.path.isfile(elf_path):
            error = f"找不到 ELF：\n{elf_path or '(未设置)'}"
            if show_message: QMessageBox.warning(self, "重新提取 ELF 失败", error)
            return False, error

        try:
            refreshed = ElfDatabase()
            refreshed.load(elf_path)
        except Exception as exc:
            error = f"ELF 解析失败，已保留原来的变量数据：\n{exc}"
            if show_message: QMessageBox.critical(self, "重新提取 ELF 失败", error)
            return False, error

        self.stop_sampling_thread()

        self.elf_reload_in_progress = True
        try:
            self.elf_db = refreshed
            self.symbol_cache = dict(refreshed.symbols)

            # Symbols removed by the new firmware must not keep stale addresses.
            for path, _v_type, _offset, base_var, tab_idx in list(self.db.get_all_vars()):
                if not base_var.startswith(("0x", "0X")) and base_var not in self.symbol_cache:
                    self.db.delete_var(path, tab_idx)

            for tab_index in range(self.tab_widget.count()):
                tree = self.tab_widget.widget(tab_index)
                if tree: tree.clear()
            self.load_history_from_db()

            current_rows = {row[0]: row for row in self.db.get_all_vars()}
            for ch_idx, path in enumerate(list(self.ch_paths)):
                row = current_rows.get(path) if path else None
                if row is None:
                    if path:
                        for active in self.plot_active_paths:
                            if path in active: active.remove(path)
                        for plot_idx, curves in enumerate(self.plot_curves_dict):
                            curve = curves.pop(path, None)
                            if curve is not None:
                                try: self.plot_widgets[plot_idx].removeItem(curve)
                                except Exception: pass
                    self.ch_paths[ch_idx] = self.ch_types[ch_idx] = self.ch_addrs[ch_idx] = None
                else:
                    _name, v_type, offset, base_var, _tab_idx = row
                    base_address = int(base_var, 16) if base_var.startswith(("0x", "0X")) else self.symbol_cache.get(base_var)
                    if base_address is None:
                        self.ch_paths[ch_idx] = self.ch_types[ch_idx] = self.ch_addrs[ch_idx] = None
                    else:
                        self.ch_types[ch_idx] = v_type
                        self.ch_addrs[ch_idx] = hex(base_address + offset)
                self.ch_buffers[ch_idx] = []
                self.ch_time_stamps[ch_idx] = []

            if hasattr(self, 'adv_parse_dialog') and self.adv_parse_dialog:
                try: self.adv_parse_dialog.replace_elf_database(refreshed)
                except RuntimeError: self.adv_parse_dialog = None
            self.refresh_ui_channel_bar()
            self.reset_canvas_adapt_ranges()
            self.elf_last_signature = self._elf_file_signature()
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
        finally:
            self.elf_reload_in_progress = False

        self.restart_sampling_thread()
        summary = f"已重新提取 ELF：{len(refreshed.symbols)} 个符号，{len(refreshed.types)} 个 DWARF 变量。"
        self.lbl_status_text.setText("ELF 已自动重新提取" if source == "flash" else "ELF 已重新提取")
        if show_message: QMessageBox.information(self, "重新提取 ELF 完成", summary)
        return True, summary

    def _elf_file_signature(self):
        try:
            stat = os.stat(os.path.abspath(self.config.elf_path))
            return stat.st_mtime_ns, stat.st_size
        except (OSError, TypeError, ValueError):
            return None

    def check_elf_file_changed(self):
        if self.elf_reload_in_progress or self.flash_sequence_active:
            return
        signature = self._elf_file_signature()
        if signature is None or signature == self.elf_last_signature:
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            return
        if signature != self.elf_pending_signature:
            self.elf_pending_signature = signature
            self.elf_pending_stable_ticks = 1
            self.lbl_status_text.setText("检测到 ELF 更新，等待文件写入完成…")
            return
        self.elf_pending_stable_ticks += 1
        if self.elf_pending_stable_ticks < 2:
            return
        ok, message = self.reload_elf_symbols(show_message=False, source="watch")
        if ok:
            self.lbl_status_text.setText("ELF 变化已自动同步到调试窗口")
        else:
            # Keep the old database and retry only after a short, stable delay.
            self.elf_pending_stable_ticks = 0
            self.lbl_status_text.setText(f"ELF 自动提取失败，将重试：{message.splitlines()[0]}")

    def type_info_for_path(self, path):
        if path.startswith(("0x", "0X")): return None
        try: return self.elf_db.resolve(path)[1]
        except Exception: return None

    @staticmethod
    def decode_value(data, info):
        size, encoding, kind = len(data), int(info.get("encoding", 0) or 0), info.get("kind")
        if kind == "pointer": return f"0x{int.from_bytes(data, 'little'):0{size * 2}X}"
        if encoding == 0x04 and size in (4, 8): return struct.unpack("<f" if size == 4 else "<d", data)[0]
        number = int.from_bytes(data, "little", signed=encoding in (0x05, 0x06))
        if kind == "enum":
            enum_name = info.get("values", {}).get(number)
            return f"{enum_name} ({number})" if enum_name else number
        if encoding == 0x02: return bool(number)
        return number

    @staticmethod
    def encode_value(text, info):
        size, encoding = scalar_size(info), int(info.get("encoding", 0) or 0)
        if not size: raise ValueError("该节点不是可写标量")
        if info.get("kind") == "enum":
            reverse = {name: value for value, name in info.get("values", {}).items()}
            number = reverse[text] if text in reverse else int(text, 0)
            return int(number).to_bytes(size, "little", signed=number < 0)
        if encoding == 0x04 and size in (4, 8): return struct.pack("<f" if size == 4 else "<d", float(text))
        if encoding == 0x02 and text.lower() in ("true", "false"): number = 1 if text.lower() == "true" else 0
        else: number = int(text, 0)
        return number.to_bytes(size, "little", signed=encoding in (0x05, 0x06))

    def read_memory_bytes(self, address, size):
        if self.backend_suspended: raise RuntimeError("通信后端已断开")
        if self.config.backend_mode == "JLINK":
            if not self.jlink_native_instance: raise RuntimeError("链路断开")
            return bytes(self.jlink_native_instance.memory_read8(address, size))
        result = bytearray()
        for offset in range(size):
            res = self.send_tcl_cmd(f"mdb {hex(address + offset)}")
            if "ERROR" in res or ":" not in res: raise RuntimeError("链路断开")
            result.append(int(res.split(":")[-1].strip().split()[0], 16) & 0xFF)
        return bytes(result)

    def write_memory_bytes(self, address, data):
        if self.backend_suspended: raise RuntimeError("通信后端已断开")
        if self.config.backend_mode == "JLINK":
            if not self.jlink_native_instance: raise RuntimeError("链路断开")
            self.jlink_native_instance.memory_write8(address, list(data)); return
        for offset, value in enumerate(data): self.send_tcl_cmd(f"mwb {hex(address + offset)} {value}")

    def send_write_command(self, addr_hex, val_int, width_bytes, is_float=False):
        if self.backend_suspended:
            return
        try:
            addr = int(addr_hex, 16)
            if self.config.backend_mode == "OPENOCD":
                if not self.openocd_link_alive: return
                if is_float:
                    self.send_tcl_cmd(f"mww {addr_hex} {val_int}")
                elif width_bytes == 1:
                    self.send_tcl_cmd(f"mwb {addr_hex} {val_int & 0xFF}")
                elif width_bytes == 2:
                    self.send_tcl_cmd(f"mwh {addr_hex} {val_int & 0xFFFF}")
                else:
                    self.send_tcl_cmd(f"mww {addr_hex} {val_int & 0xFFFFFFFF}")
            else:
                if self.jlink_native_instance:
                    if width_bytes == 1: self.jlink_native_instance.memory_write8(addr, [val_int & 0xFF])
                    elif width_bytes == 2: self.jlink_native_instance.memory_write16(addr, [val_int & 0xFFFF])
                    else: self.jlink_native_instance.memory_write32(addr, [val_int & 0xFFFFFFFF])
        except Exception:
            pass

    def send_tcl_cmd(self, cmd):
        if self.backend_suspended or not self.tcl_socket or not self.openocd_link_alive: return "ERROR"
        try:
            self.tcl_socket.sendall(f"{cmd}\x1a".encode('utf-8'))
            res = receive_tcl_response(self.tcl_socket)
            if "ERROR" in res: raise Exception()
            return res
        except: 
            self.openocd_link_alive = False 
            return "ERROR"

    def get_current_tree(self):
        return self.tab_widget.currentWidget()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Delete:
            tree = self.get_current_tree()
            if tree:
                item = tree.currentItem()
                if item:
                    root_item = item
                    while root_item.parent(): root_item = root_item.parent()
                    self.remove_variable_root(root_item)
        elif event.key() == Qt.Key.Key_Space:
            self.toggle_freeze()
        else:
            super().keyPressEvent(event)

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 6) 
        
        top = QHBoxLayout()
        self.wave_hz_combo = QComboBox()
        self.wave_hz_combo.addItems([
            "Custom (自定义频率)", "500 Hz 均衡流", "1000 Hz 高速流", 
            "2000 Hz 极限级🚀", "4000 Hz 超极限⚡", "8000 Hz 毁灭级🔥"
        ])
        self.wave_hz_combo.currentIndexChanged.connect(self.restart_sampling_thread)
        
        self.custom_hz_edit = QLineEdit("30")
        self.custom_hz_edit.setFixedWidth(55)
        self.custom_hz_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_hz_edit.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.custom_hz_edit.setValidator(QIntValidator(1, 8000, self))
        self.custom_hz_edit.editingFinished.connect(self.restart_sampling_thread)
        
        self.btn_freeze = QPushButton("❄️ 锁定滚动")
        self.btn_freeze.setToolTip("锁定/恢复波形时间轴滚动（空格键）")
        self.btn_freeze.clicked.connect(self.toggle_freeze)

        self.btn_canvas_adapt = QPushButton("🎯 自适应画布")
        self.btn_canvas_adapt.setToolTip("采集 2 秒数据，按最大/最小值将 X、Y 显示跨度扩展为 2 倍")
        self.btn_canvas_adapt.clicked.connect(self.start_canvas_auto_adapt)
        self.canvas_adapt_timer = QTimer(self)
        self.canvas_adapt_timer.setSingleShot(True)
        self.canvas_adapt_timer.timeout.connect(self.finish_canvas_auto_adapt)
        
        self.btn_disconnect = QPushButton("⏏ 断开后端")
        self.btn_disconnect.setToolTip("立即停止软件读写并在后台释放通信后端；拔掉实际线路卡住时界面不会等待")
        self.btn_disconnect.clicked.connect(self.disconnect_backend)

        self.btn_reconnect = QPushButton("🔌 通信重连")
        self.btn_reconnect.setToolTip("重新启动并连接当前配置的通信后端，不复位 MCU")
        self.btn_reconnect.clicked.connect(self.force_reconnect_engine)
        
        self.btn_mcu_reset = QPushButton("♻️ 复位")
        self.btn_mcu_reset.setStyleSheet(
            "background-color: #4a1515; color: #ff8a80; border: 1px solid #7f1d1d;" if self.is_dark_theme
            else "background-color: #ffebee; color: #c62828; border: 1px solid #ef9a9a;"
        )
        self.btn_mcu_reset.clicked.connect(self.trigger_manual_mcu_reset)

        self.btn_flash = QPushButton("⚡ 烧录")
        self.btn_flash.setStyleSheet(
            "background-color: #5d3b00; color: #ffd54f; border: 1px solid #8a5a00;" if self.is_dark_theme
            else "background-color: #fff3e0; color: #b45309; border: 1px solid #ffcc80;"
        )
        self.btn_flash.setToolTip("只有点击并确认后才会擦写 Flash/复位；普通调试不复位 MCU")
        self.btn_flash.clicked.connect(self.flash_elf_by_user)
        self.btn_flash.setEnabled(self.config.backend_mode == "JLINK")

        top.addWidget(QLabel("📊 采样速率:"))
        top.addWidget(self.wave_hz_combo)
        top.addWidget(self.custom_hz_edit)
        top.addWidget(self.btn_freeze)
        top.addWidget(self.btn_canvas_adapt)
        
        top.addSpacing(10) 
        top.addWidget(self.btn_disconnect)
        top.addWidget(self.btn_reconnect) 
        top.addWidget(self.btn_flash)
        top.addWidget(self.btn_mcu_reset) 
        top.addStretch()
        
        top.addWidget(QLabel("<b>🖼️ 画布布局:</b>"))
        self.layout_group = QButtonGroup(self)
        self.rad_map1 = QRadioButton("单图")
        self.rad_map2 = QRadioButton("双图")
        self.rad_map3 = QRadioButton("三图")
        self.rad_map1.setToolTip("单图（1 行独占）")
        self.rad_map2.setToolTip("双图（2 行上下均匀）")
        self.rad_map3.setToolTip("三图（3 行上下均匀）")
        self.layout_group.addButton(self.rad_map1, 1)
        self.layout_group.addButton(self.rad_map2, 2)
        self.layout_group.addButton(self.rad_map3, 3)
        self.rad_map1.setChecked(True)
        top.addWidget(self.rad_map1)
        top.addWidget(self.rad_map2)
        top.addWidget(self.rad_map3)
        self.layout_group.idClicked.connect(self.handle_layout_changed)
        layout.addLayout(top)

        quick_input_bar = QHBoxLayout()
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("全局变量名或十六进制物理地址(如0x40012440)")
        self.input_name.setFixedWidth(280)
        
        self.input_type = QComboBox()
        self.input_type.addItems(["uint8", "int8", "uint16", "int16", "uint32", "int32", "int", "float"])
        self.input_type.setFixedWidth(90)
        self.input_type.currentIndexChanged.connect(lambda idx: self.db.save_config("last_input_type_idx", str(idx)))
        
        btn_add = QPushButton("➕ 快捷添加监视")
        btn_add.clicked.connect(self.quick_add_variable)
        
        btn_adv_c = QPushButton("🚀 ELF 变量浏览器")
        btn_adv_c.setStyleSheet("background-color: #0d47a1; color: white; font-weight: bold;")
        btn_adv_c.clicked.connect(self.open_advance_parser_window)

        quick_input_bar.addWidget(QLabel("<b>⚡ 快捷输入栏:</b>"))
        quick_input_bar.addWidget(self.input_name)
        quick_input_bar.addWidget(self.input_type)
        quick_input_bar.addWidget(btn_add)
        quick_input_bar.addSpacing(20)
        quick_input_bar.addWidget(btn_adv_c)
        quick_input_bar.addStretch()
        layout.addLayout(quick_input_bar)
        
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.handle_tab_close_requested)
        self.tab_widget.currentChanged.connect(lambda: self.restart_sampling_thread())
        
        self.btn_add_tab = QPushButton("➕")
        self.btn_add_tab.setFixedWidth(30)
        self.btn_add_tab.setToolTip("创建新变量调试窗口 (最多2个)")
        self.btn_add_tab.clicked.connect(self.create_new_debug_tab)
        self.tab_widget.setCornerWidget(self.btn_add_tab, Qt.Corner.TopRightCorner)
        
        self.create_new_debug_tab("主调试窗口")
        self.main_splitter.addWidget(self.tab_widget)
        
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_splitter.setStyleSheet(f"QSplitter::handle {{ height: 4px; background-color: {self.theme_colors['splitter']}; }}")
        
        self.plot_widgets = []
        self.ch_colors = self.theme_colors['channels']
        self.v_lines = []
        self.h_lines = []
        self.hover_dots = []
        self.hover_labels = []

        for i in range(3):
            pw = pg.PlotWidget()
            pw.setBackground(self.theme_colors['plot_bg'])
            pw.showGrid(x=True, y=True, alpha=0.2)
            pw.setLabel('bottom', '时间滚动追踪轴', units='ms')
            for axis_name in ('left', 'bottom'):
                axis = pw.getAxis(axis_name)
                axis.setPen(pg.mkPen(self.theme_colors['axis']))
                axis.setTextPen(pg.mkPen(self.theme_colors['axis_text']))
            pw.getViewBox().setDefaultPadding(0.0)
            pw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            pw.customContextMenuRequested.connect(lambda pos, idx=i: self.show_plot_context_menu(pos, idx))

            v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(self.theme_colors['crosshair'], width=1, style=Qt.PenStyle.DashLine))
            h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(self.theme_colors['crosshair'], width=1, style=Qt.PenStyle.DashLine))
            v_line.setVisible(False)
            h_line.setVisible(False)
            pw.addItem(v_line)
            pw.addItem(h_line)
            self.v_lines.append(v_line)
            self.h_lines.append(h_line)

            hover_dot = pg.ScatterPlotItem(size=10, pen=pg.mkPen(self.theme_colors['hover_pen'], width=2))
            hover_dot.setVisible(False)
            pw.addItem(hover_dot)
            self.hover_dots.append(hover_dot)

            hover_label = pg.TextItem(anchor=(0, 1), color=self.theme_colors['hover_text'], fill=self.theme_colors['hover_fill'])
            hover_label.setVisible(False)
            pw.addItem(hover_label)
            self.hover_labels.append(hover_label)
            pw.scene().sigMouseMoved.connect(lambda pos, idx=i: self.handle_plot_mouse_move(pos, idx))

            self.plot_widgets.append(pw)
            self.plot_splitter.addWidget(pw)
            
        self.main_splitter.addWidget(self.plot_splitter)
        self.main_splitter.setSizes([450, 1000])
        layout.addWidget(self.main_splitter)
        
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(5, 4, 5, 4)
        self.lbl_ch1 = QLabel("CH1: 📭 空闲")
        self.lbl_ch2 = QLabel("CH2: 📭 空闲")
        self.lbl_ch3 = QLabel("CH3: 📭 空闲")
        
        ch_style = ("font-family: 'Consolas'; font-size: 11px; font-weight: bold; padding: 2px 10px; "
                    f"border-radius: 4px; background-color: {self.theme_colors['panel']}; border: 1px solid {self.theme_colors['panel_border']};")
        self.lbl_ch1.setStyleSheet(f"{ch_style} color: {self.ch_colors[0]};")
        self.lbl_ch2.setStyleSheet(f"{ch_style} color: {self.ch_colors[1]};")
        self.lbl_ch3.setStyleSheet(f"{ch_style} color: {self.ch_colors[2]};")
        
        bottom_bar.addWidget(self.lbl_ch1)
        bottom_bar.addWidget(self.lbl_ch2)
        bottom_bar.addWidget(self.lbl_ch3)
        bottom_bar.addStretch()
        
        self.lbl_led = QLabel()
        self.lbl_led.setFixedSize(10, 10)
        self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
        self.lbl_status_text = QLabel("正在连接...")
        self.lbl_status_text.setStyleSheet("color: #757575; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")
        
        bottom_bar.addWidget(self.lbl_led)
        bottom_bar.addWidget(self.lbl_status_text)
        
        bottom_wrapper = QWidget()
        bottom_wrapper.setFixedHeight(28) 
        bottom_wrapper.setLayout(bottom_bar)
        layout.addWidget(bottom_wrapper)
        
        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

    def create_new_debug_tab(self, name=None):
        if self.tab_widget.count() >= 2:
            QMessageBox.warning(self, "标签页上限", "最多只允许开启 2 个变量调试窗口。")
            return
        if not name: name = f"窗口 {self.tab_widget.count() + 1}"
            
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["全局变量路径节点 (树状结构)", "底层数据类型", "实时物理数值 (5Hz后台)"])
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.show_context_menu)
        tree.itemDoubleClicked.connect(self.handle_tree_item_double_clicked)
        
        new_idx = self.tab_widget.addTab(tree, name)
        self.tab_widget.setCurrentIndex(new_idx)
        if self.tab_widget.count() >= 2: self.btn_add_tab.setEnabled(False)

    def handle_tab_close_requested(self, index):
        if self.tab_widget.count() <= 1:
            QMessageBox.warning(self, "禁止关闭", "至少需要保留一个正在执行的变量调试主窗口。")
            return
        tree = self.tab_widget.widget(index)
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            root_name = item.text(0)
            for path, _, _, _, t_idx in self.db.get_all_vars():
                if t_idx == index and (path == root_name or path.startswith(root_name + ".") or path.startswith(root_name + "[")):
                    self.db.delete_var(path, tab_idx=t_idx)
        for i in range(3): self.unbind_channel(i)
        self.tab_widget.removeTab(index)
        self.btn_add_tab.setEnabled(True)
        self.restart_sampling_thread()

    def toggle_freeze(self):
        self.is_frozen = not self.is_frozen
        if self.is_frozen:
            self.btn_freeze.setText("▶️ 恢复滚动")
            self.btn_freeze.setStyleSheet("background-color: #e65100; color: white; border: 1px solid #bf4a00;")
        else:
            self.btn_freeze.setText("❄️ 锁定滚动")
            self.btn_freeze.setStyleSheet(
                f"background-color: {self.theme_colors['neutral_button']}; color: {self.theme_colors['neutral_text']};"
            )
            for i in range(3):
                if self.v_lines[i]: self.v_lines[i].setVisible(False)
                if self.h_lines[i]: self.h_lines[i].setVisible(False)
                if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                if self.hover_labels[i]: self.hover_labels[i].setVisible(False)

    @staticmethod
    def _double_span_limits(minimum, maximum):
        minimum, maximum = float(minimum), float(maximum)
        span = maximum - minimum
        if not np.isfinite(span) or span <= 0:
            half_span = max(abs(minimum) * 0.1, 1.0)
            return minimum - half_span, maximum + half_span
        center = (minimum + maximum) / 2.0
        return center - span, center + span

    def start_canvas_auto_adapt(self):
        if not any(self.ch_paths):
            QMessageBox.information(self, "一键自适应画布", "请先把至少一个变量绑定到波形通道。")
            return
        self.reset_canvas_adapt_ranges()
        self.canvas_adapt_start_ms = (time.time() - self.start_time) * 1000.0
        self.btn_canvas_adapt.setEnabled(False)
        self.btn_canvas_adapt.setText("⏳ 正在统计 2 秒范围…")
        self.lbl_status_text.setText("正在采样画布自适应范围")
        self.canvas_adapt_timer.start(2000)

    def finish_canvas_auto_adapt(self):
        start_ms = self.canvas_adapt_start_ms
        self.canvas_adapt_start_ms = None
        adapted = 0
        with QMutexLocker(self.buffer_mutex):
            for plot_idx in range(3):
                x_values, y_values = [], []
                for path in self.plot_active_paths[plot_idx]:
                    try: channel_idx = self.ch_paths.index(path)
                    except ValueError: continue
                    timestamps = self.ch_time_stamps[channel_idx]
                    values = self.ch_buffers[channel_idx]
                    if not timestamps or not values: continue
                    first = bisect.bisect_left(timestamps, start_ms) if start_ms is not None else 0
                    count = min(len(timestamps), len(values))
                    if first >= count: continue
                    x_arr = np.asarray(timestamps[first:count], dtype=float)
                    y_arr = np.asarray(values[first:count], dtype=float)
                    finite = np.isfinite(x_arr) & np.isfinite(y_arr)
                    if finite.any():
                        x_values.append(x_arr[finite])
                        y_values.append(y_arr[finite])
                if not x_values: continue
                all_x, all_y = np.concatenate(x_values), np.concatenate(y_values)
                x_lower, x_upper = self._double_span_limits(np.min(all_x), np.max(all_x))
                y_lower, y_upper = self._double_span_limits(np.min(all_y), np.max(all_y))
                self.canvas_fixed_ranges[plot_idx] = (x_lower, x_upper, y_lower, y_upper)
                self.plot_widgets[plot_idx].setXRange(x_lower, x_upper, padding=0)
                self.plot_widgets[plot_idx].setYRange(y_lower, y_upper, padding=0)
                adapted += 1
        self.btn_canvas_adapt.setEnabled(True)
        self.btn_canvas_adapt.setText("🎯 自适应画布")
        if adapted:
            self.lbl_status_text.setText(f"已自适应 {adapted} 个画布（X/Y 跨度 ×2）")
        else:
            self.lbl_status_text.setText("画布自适应失败：观察期间没有有效数据")
            QMessageBox.warning(self, "一键自适应画布", "2 秒观察期间没有采集到有效波形数据。")

    def reset_canvas_adapt_ranges(self):
        self.canvas_fixed_ranges = [None, None, None]
        for plot_widget in getattr(self, 'plot_widgets', []):
            try: plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            except Exception: pass

    def trigger_manual_mcu_reset(self):
        answer = QMessageBox.question(
            self, "确认复位 MCU",
            "这是唯一的手动复位操作。\n\n确定要立即复位并运行 MCU 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return
        reset_text = "#ff8a80" if self.is_dark_theme else "#c62828"
        self._execute_flash_animation(self.btn_mcu_reset, "#e53935", f"color: {reset_text};")
        if self.config.backend_mode == "OPENOCD":
            if self.openocd_link_alive and self.tcl_socket:
                try:
                    self.tcl_socket.setblocking(True)
                    self.tcl_socket.sendall(b"reset run\x1a")
                    try: self.tcl_socket.recv(512)
                    except: pass
                except: pass
        else:
            if self.jlink_native_instance:
                try: self.jlink_native_instance.reset()
                except: pass
        self.force_reconnect_engine()

    def flash_elf_by_user(self):
        if self.config.backend_mode != "JLINK":
            QMessageBox.warning(self, "无法烧录", "当前不是 J-Link 模式。")
            return
        elf_path = os.path.abspath(self.config.elf_path)
        jlink_dir = os.path.dirname(self.config.jlink_dll_dir) if os.path.isfile(self.config.jlink_dll_dir) else self.config.jlink_dll_dir
        commander = os.path.join(jlink_dir, "JLink.exe")
        device = self.config.jlink_device
        loader_config, loader_error = self.find_flash_loader_config(jlink_dir, device)
        if not os.path.isfile(elf_path):
            QMessageBox.warning(self, "无法烧录", f"找不到 ELF：\n{elf_path}")
            return
        if not os.path.isfile(commander):
            QMessageBox.warning(self, "无法烧录", f"找不到与 J-Flash 同目录的 JLink.exe：\n{commander}")
            return
        if loader_error:
            QMessageBox.warning(self, "无法烧录", loader_error)
            return
        answer = QMessageBox.warning(
            self, "确认烧录 ELF",
            f"芯片：{device}\nELF：{elf_path}\n\n"
            "烧录会擦写 Flash，并在完成后复位运行 MCU。\n"
            "普通连接、采样和重连不会执行此操作。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return

        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("⏳ 正在烧录…")
        self.lbl_status_text.setText("正在烧录 ELF（界面仍可响应）")
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.stop_sampling_thread()
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.pending_flash_args = (
            commander, jlink_dir, elf_path, device, self.config.jlink_connect_device, loader_config
        )
        if old_link:
            self._close_native_link_async(old_link, next_action="flash")
            return
        self._start_pending_flash()

    def _start_pending_flash(self):
        args = getattr(self, 'pending_flash_args', None)
        self.pending_flash_args = None
        if not args:
            return
        self.flash_worker = ElfFlashWorker(
            *args, self
        )
        self.flash_worker.finished_signal.connect(self.handle_flash_finished)
        self.flash_worker.start()

    @staticmethod
    def find_flash_loader_config(jlink_dir, device_name):
        """Build a corrected one-device XML configuration for an extension MCU."""
        xml_path = os.path.join(jlink_dir, "JLinkDevices.xml")
        if not os.path.isfile(xml_path): return None, ""
        try:
            root = ET.parse(xml_path).getroot()
        except Exception as exc:
            return None, f"无法解析器件库：\n{xml_path}\n\n{exc}"
        chip_attrs = None
        flash_attrs = None
        for device in root.iter("Device"):
            chip = device.find("ChipInfo")
            if chip is None or chip.get("Name", "").casefold() != device_name.casefold(): continue
            flash = device.find("FlashBankInfo")
            chip_attrs = dict(chip.attrib)
            flash_attrs = dict(flash.attrib) if flash is not None else None
            break
        if not chip_attrs or not flash_attrs or not flash_attrs.get("Loader"):
            # DLL-only devices manage their algorithms internally.
            return None, ""

        loader = flash_attrs["Loader"].strip()
        normalized = loader.replace("/", os.sep).replace("\\", os.sep)
        direct = os.path.normpath(os.path.join(jlink_dir, normalized))
        loader_path = direct if os.path.isfile(direct) else ""

        basename = os.path.basename(normalized)
        matches = []
        if not loader_path:
            devices_root = os.path.join(jlink_dir, "Devices")
            if os.path.isdir(devices_root):
                for root_dir, _dirs, files in os.walk(devices_root):
                    for filename in files:
                        if filename.casefold() == basename.casefold(): matches.append(os.path.join(root_dir, filename))
        if len(matches) == 1: loader_path = matches[0]
        elif len(matches) > 1:
            # Prefer a directory whose path contains the selected device family.
            family = re.match(r"[A-Za-z]+\d+", device_name)
            if family:
                preferred = [path for path in matches if family.group(0).casefold() in path.casefold()]
                if len(preferred) == 1: loader_path = preferred[0]
            if not loader_path:
                return None, f"Flash 算法 {basename} 存在多个同名文件，无法唯一确定：\n" + "\n".join(matches)
        if not loader_path:
            return None, f"芯片 {device_name} 需要 Flash 算法：\n{loader}\n\n但在当前 J-Link 目录下未找到：\n{jlink_dir}"
        config = {
            "vendor": chip_attrs.get("Vendor", "Custom"),
            "ram_addr": int(chip_attrs.get("WorkRAMAddr", "0"), 0),
            "ram_size": int(chip_attrs.get("WorkRAMSize", "0"), 0),
            "core": chip_attrs.get("Core", "JLINK_CORE_CORTEX_M0"),
            "flash_name": flash_attrs.get("Name", "Internal Flash"),
            "flash_addr": int(flash_attrs.get("BaseAddr", "0"), 0),
            "flash_size": int(flash_attrs.get("MaxSize", "0"), 0),
            "loader_path": os.path.abspath(loader_path),
            "loader_type": flash_attrs.get("LoaderType", "FLASH_ALGO_TYPE_OPEN"),
        }
        return config, ""

    def handle_flash_finished(self, success, message, link):
        self.jlink_native_instance = link
        self.flash_sequence_active = False
        self.backend_suspended = False
        self.btn_flash.setText("⚡ 烧录")
        self.btn_flash.setEnabled(self.config.backend_mode == "JLINK")
        self.lbl_status_text.setText("烧录完成" if success else "烧录失败")
        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            QMessageBox.information(self, "烧录完成", message)
        else:
            self.restart_sampling_thread()
            QMessageBox.critical(self, "烧录失败", message)

    def force_reconnect_engine(self):
        self._execute_flash_animation(self.btn_reconnect, "#00b0ff", "")
        self.backend_suspended = True
        self.stop_sampling_thread()
        if self.config.backend_mode == "OPENOCD":
            if self.tcl_socket: 
                try: self.tcl_socket.close()
                except: pass
            self.tcl_socket = None
            self.openocd_link_alive = False
            stop_managed_openocd(self.config)
            time.sleep(0.2)
            launch_openocd(self.config)
            time.sleep(0.6)
            self.backend_suspended = False
            self.init_global_link_backend()
            self._finish_backend_reconnect()
            return

        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        if old_link:
            self._close_native_link_async(old_link, next_action="reconnect")
            self.lbl_status_text.setText("正在后台释放旧 J-Link 后端…")
        else:
            self.backend_suspended = False
            self.init_global_link_backend()
            self._finish_backend_reconnect()

    def disconnect_backend(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录过程中不能断开通信后端。")
            return
        self.backend_suspended = True
        self.stop_sampling_thread()
        if self.tcl_socket:
            try: self.tcl_socket.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: self.tcl_socket.close()
            except Exception: pass
        self.tcl_socket = None
        self.openocd_link_alive = False
        if self.config.backend_mode == "OPENOCD":
            try: stop_managed_openocd(self.config)
            except Exception: pass
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        if old_link:
            self._close_native_link_async(old_link)
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端已断开（未复位 MCU）")
        self.lbl_status_text.setStyleSheet("color: #f59e0b; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")

    def _close_native_link_async(self, link, next_action=""):
        closer = BackendCloseWorker(link, self)
        self.backend_close_workers.append(closer)
        closer.finished_signal.connect(
            lambda error, worker=closer, action=next_action:
            self._native_link_closed(worker, error, action)
        )
        closer.start()

    def _native_link_closed(self, worker, error, next_action):
        if worker in self.backend_close_workers:
            self.backend_close_workers.remove(worker)
        worker.deleteLater()
        if next_action == "reconnect":
            self.backend_suspended = False
            self.init_global_link_backend()
            self._finish_backend_reconnect()
        elif next_action == "flash":
            self._start_pending_flash()
        elif error:
            self.lbl_status_text.setText(f"后端已停止读写；驱动释放异常：{error}")

    def _finish_backend_reconnect(self):
        connected = (self.openocd_link_alive if self.config.backend_mode == "OPENOCD"
                     else bool(self.jlink_native_instance))
        self.btn_disconnect.setEnabled(connected)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端重连完成" if connected else "通信后端重连失败")
        self.restart_sampling_thread()

    def _execute_flash_animation(self, button, target_color, base_style):
        anim = QVariantAnimation(button)
        anim.setDuration(220) 
        anim.setStartValue(QColor(target_color))
        anim.setEndValue(QColor(self.theme_colors['neutral_button']))
        anim.valueChanged.connect(lambda val: button.setStyleSheet(f"{base_style} background-color: {val.name()};"))
        anim.start()

    def handle_layout_changed(self, layout_id):
        self.layout_switching = True
        self.current_layout_id = layout_id
        self.db.save_config("last_layout_id", str(layout_id))
        
        with QMutexLocker(self.buffer_mutex):
            for i in range(3):
                should_visible = (i == 0) or (i == 1 and layout_id >= 2) or (i == 2 and layout_id == 3)
                pw = self.plot_widgets[i]
                if should_visible: pw.setVisible(True)
                else:
                    pw.setVisible(False)
                    try: pw.getPlotItem().clear()
                    except: pass
                    self.plot_curves_dict[i].clear()
                    if self.v_lines[i]: self.v_lines[i].setVisible(False)
                    if self.h_lines[i]: self.h_lines[i].setVisible(False)
                    if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                    if self.hover_labels[i]: self.hover_labels[i].setVisible(False)
        
        container_height = self.plot_splitter.height() or 600
        if layout_id == 1: self.plot_splitter.setSizes([container_height, 0, 0])
        elif layout_id == 2:
            h = container_height // 2
            self.plot_splitter.setSizes([h, h, 0])
        elif layout_id == 3:
            h = container_height // 3
            self.plot_splitter.setSizes([h, h, h])
        QTimer.singleShot(100, self._reset_layout_switching_flag)

    def _reset_layout_switching_flag(self):
        self.layout_switching = False

    def restore_layout_and_type_configs(self):
        try:
            layout_id = int(self.db.get_config("last_layout_id", "1"))
            if layout_id == 1: self.rad_map1.setChecked(True)
            elif layout_id == 2: self.rad_map2.setChecked(True)
            elif layout_id == 3: self.rad_map3.setChecked(True)
            self.handle_layout_changed(layout_id)
            type_idx = int(self.db.get_config("last_input_type_idx", "7"))
            if type_idx < self.input_type.count(): self.input_type.setCurrentIndex(type_idx)
        except: pass

    def load_history_from_db(self):
        # Refresh saved variable layouts from this ELF so stale source-derived
        # offsets in an existing history database cannot be reused.
        old_rows = self.db.get_all_vars()
        roots = {row[3] for row in old_rows if row[3] in self.elf_db.types}
        for root in roots:
            for tab_idx in {row[4] for row in old_rows if row[3] == root}:
                for row in old_rows:
                    if row[3] == root and row[4] == tab_idx: self.db.delete_var(row[0], tab_idx)
                for record in self.elf_db.flatten(root):
                    self.db.save_var(record["path"], record["type"], record["offset"], root, tab_idx)
        db_vars = self.db.get_all_vars()
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            while self.tab_widget.count() <= t_idx: self.create_new_debug_tab()
            if '.' not in path and '[' not in path: self.inject_var_to_tree_ui(path, v_type, t_idx)
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            if '.' in path or '[' in path: self.inject_var_to_tree_ui(path, v_type, t_idx)

    def handle_high_speed_data(self, batch_data):
        locker = QMutexLocker(self.buffer_mutex)
        for timestamp, data_packet in batch_data:
            current_rel_time = (timestamp - self.start_time) * 1000.0
            for ch_idx, val in data_packet.items():
                self.ch_buffers[ch_idx].append(val)
                self.ch_time_stamps[ch_idx].append(current_rel_time)
        for i in range(3):
            if len(self.ch_buffers[i]) > 40000:
                self.ch_buffers[i] = self.ch_buffers[i][-25000:]
                self.ch_time_stamps[i] = self.ch_time_stamps[i][-25000:]

    def update_plot_canvas(self):
        if self.is_frozen or self.layout_switching: return
        layout_id = self.current_layout_id
        locker = QMutexLocker(self.buffer_mutex)
        for i in range(3):
            active_paths = self.plot_active_paths[i]
            curves_map = self.plot_curves_dict[i]
            ch_idx = -1
            for path in active_paths:
                for c_i in range(3):
                    if self.ch_paths[c_i] == path:
                        ch_idx = c_i; break
                if ch_idx != -1: break

            pw = self.plot_widgets[i]
            if i >= 1 and layout_id < 2: continue
            if i == 2 and layout_id < 3: continue
            if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20: continue
                
            try:  
                if not pw.getPlotItem().scene(): continue
                fixed_range = self.canvas_fixed_ranges[i]
                if fixed_range is not None:
                    # Keep the adapted X span, but anchor it to the newest
                    # sample so the time axis continues scrolling.
                    x_span = max(fixed_range[1] - fixed_range[0], 1.0)
                    if ch_idx != -1 and self.ch_time_stamps[ch_idx]:
                        max_x = self.ch_time_stamps[ch_idx][-1]
                    else:
                        max_x = (time.time() - self.start_time) * 1000.0
                    min_x = max_x - x_span
                    pw.setXRange(min_x, max_x, padding=0)
                elif ch_idx != -1 and self.ch_time_stamps[ch_idx]:
                    max_x = self.ch_time_stamps[ch_idx][-1]  
                    min_x = max_x - self.max_display_duration_ms
                    pw.setXRange(min_x, max_x, padding=0)
                else:
                    now_rel_time = (time.time() - self.start_time) * 1000.0
                    min_x = now_rel_time - self.max_display_duration_ms
                    pw.setXRange(min_x, now_rel_time, padding=0)
                
                curr_keys = list(curves_map.keys())
                for pk in curr_keys:
                    if pk not in active_paths and pk in curves_map:
                        pw.removeItem(curves_map[pk])
                        del curves_map[pk]
                
                for path in active_paths:
                    target_ch = -1
                    for c_i in range(3):
                        if self.ch_paths[c_i] == path: target_ch = c_i; break
                    if target_ch != -1 and self.ch_buffers[target_ch]:
                        t_data = self.ch_time_stamps[target_ch]
                        v_data = self.ch_buffers[target_ch]
                        start_idx = bisect.bisect_left(t_data, min_x)
                        if len(t_data) > start_idx:
                            x_arr = np.array(t_data[start_idx:], dtype=np.float64)
                            y_arr = np.array(v_data[start_idx:], dtype=np.float64)
                            if path not in curves_map:
                                smooth_pen = QPen(QColor(self.ch_colors[target_ch]), 2.0)
                                smooth_pen.setCosmetic(True)
                                smooth_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                                smooth_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                                curves_map[path] = pw.plot(pen=smooth_pen, antialias=True)
                            curves_map[path].setData(x_arr, y_arr)
            except: pass 

    def handle_plot_mouse_move(self, pos, plot_idx):
        if not self.is_frozen or self.layout_switching: return
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20: return

        active_paths = self.plot_active_paths[plot_idx]
        target_ch = -1
        for path in active_paths:
            for c_i in range(3):
                if self.ch_paths[c_i] == path: target_ch = c_i; break
            if target_ch != -1: break

        locker = QMutexLocker(self.buffer_mutex)
        if target_ch == -1 or not self.ch_buffers[target_ch]: return
        t_data = self.ch_time_stamps[target_ch]
        v_data = self.ch_buffers[target_ch]

        try:
            if not pw.getPlotItem().scene(): return
            mouse_point = pw.getViewBox().mapSceneToView(pos)
            x_mouse = mouse_point.x()
            idx = bisect.bisect_left(t_data, x_mouse)
            if idx == 0: closest_idx = 0
            elif idx >= len(t_data): closest_idx = len(t_data) - 1
            else: closest_idx = idx if (t_data[idx] - x_mouse) < (x_mouse - t_data[idx-1]) else (idx - 1)

            if closest_idx >= len(t_data): return
            exact_x = t_data[closest_idx]
            exact_y = v_data[closest_idx]

            if self.v_lines[plot_idx]: self.v_lines[plot_idx].setPos(exact_x)
            if self.h_lines[plot_idx]: self.h_lines[plot_idx].setPos(exact_y)
            if self.v_lines[plot_idx]: self.v_lines[plot_idx].setVisible(True)
            if self.h_lines[plot_idx]: self.h_lines[plot_idx].setVisible(True)

            if self.hover_dots[plot_idx]:
                self.hover_dots[plot_idx].setData([exact_x], [exact_y], brush=pg.mkBrush(self.ch_colors[target_ch]))
                self.hover_dots[plot_idx].setVisible(True)

            if self.hover_labels[plot_idx]:
                self.hover_labels[plot_idx].setText(f" t: {exact_x:.1f} ms\n v: {exact_y:.4f} ")
                self.hover_labels[plot_idx].setPos(exact_x, exact_y)
                self.hover_labels[plot_idx].setVisible(True)
        except: pass

    def show_plot_context_menu(self, pos, plot_idx):
        if self.layout_switching: return
        available_paths = [p for p in self.ch_paths if p is not None]
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible(): return
        try:
            menu = pw.getPlotItem().getContextMenus(None)
            menu.setStyleSheet(self.popup_menu_style)
            y_val_menu = None
            for act in menu.actions():
                if act.menu() and "Y_Val" in act.menu().title():
                    y_val_menu = act.menu(); y_val_menu.clear(); break
            if not y_val_menu:
                y_val_menu = QMenu("📈 Y_Val 数据源定义", menu)
                menu.insertMenu(menu.actions()[0] if menu.actions() else None, y_val_menu)
            y_val_menu.setStyleSheet(self.popup_menu_style)
            
            if not available_paths:
                action = QAction("⚠️ 暂无高速通道绑定的变量", y_val_menu)
                action.setEnabled(False)
                y_val_menu.addAction(action)
            else:
                for path in available_paths:
                    is_selected = path in self.plot_active_paths[plot_idx]
                    action = QAction(f"{'●   ' if is_selected else '○   '}{path}", y_val_menu)
                    action.triggered.connect(lambda checked, p=path, p_idx=plot_idx, current_state=is_selected: 
                        self.commit_vofa_layer_selection(p, p_idx, current_state))
                    y_val_menu.addAction(action)
            menu.exec(pw.mapToGlobal(pos))
        except: pass

    def commit_vofa_layer_selection(self, path, plot_idx, current_state):
        if not current_state:
            if path not in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].append(path)
        else:
            if path in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].remove(path)
        self.restart_sampling_thread()

    def show_context_menu(self, pos):
        tree = self.get_current_tree()
        if not tree: return
        item = tree.itemAt(pos)
        if not item: return
            
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path: path = item.text(0)
        
        menu = QMenu()
        menu.setStyleSheet(self.popup_menu_style)
        bound_ch_idx = -1
        for i in range(3):
            if self.ch_paths[i] == path: bound_ch_idx = i; break

        is_container = "{结构体/数组}" in item.text(2)
        if not is_container:
            if bound_ch_idx != -1:
                menu.addAction(f"❌ 取消该变量在 通道 {bound_ch_idx+1} 的波形监视")
            else:
                m1 = menu.addAction("🟢 绑定至 示波器通道 1")
                m2 = menu.addAction("🟡 绑定至 示波器通道 2")
                m3 = menu.addAction("🔵 绑定至 示波器通道 3")
            menu.addSeparator()
            
        m_del = menu.addAction("🗑️ 从监视列表中删除该整组变量及其全部子成员 (Delete)")
        act = menu.exec(tree.viewport().mapToGlobal(pos))
        
        if not is_container and bound_ch_idx != -1 and act and "取消" in act.text(): 
            self.unbind_channel(bound_ch_idx)
        elif not is_container and 'm1' in locals() and act == m1: self.bind_tree_item_to_channel(0, item)
        elif not is_container and 'm2' in locals() and act == m2: self.bind_tree_item_to_channel(1, item)
        elif not is_container and 'm3' in locals() and act == m3: self.bind_tree_item_to_channel(2, item)
        elif act == m_del:
            root_item = item
            while root_item.parent(): root_item = root_item.parent()
            self.remove_variable_root(root_item)

    def bind_tree_item_to_channel(self, ch_idx, item):
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        v_type = item.text(1)
        all_db_vars = {x[0]: (x[2], x[3]) for x in self.db.get_all_vars()}
        if path not in all_db_vars: return
        offset, base_var = all_db_vars[path]
        
        if base_var.startswith("0x") or base_var.startswith("0X"):
            absolute_address = int(base_var, 16) + offset
        else:
            base_addr = self.symbol_cache.get(base_var)
            if base_addr is None: return
            absolute_address = base_addr + offset
        
        for i in range(3):
            if self.ch_paths[i] == path: self.unbind_channel(i)
        
        locker = QMutexLocker(self.buffer_mutex)
        self.ch_paths[ch_idx] = path
        self.ch_types[ch_idx] = v_type
        self.ch_addrs[ch_idx] = hex(absolute_address)
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        if path not in self.plot_active_paths[ch_idx]: self.plot_active_paths[ch_idx].append(path)
        
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        self.restart_sampling_thread()

    def unbind_channel(self, ch_idx):
        locker = QMutexLocker(self.buffer_mutex)
        path = self.ch_paths[ch_idx]
        if path:
            for p_idx, p_active in enumerate(self.plot_active_paths):
                if path in p_active: p_active.remove(path)
                if path in self.plot_curves_dict[p_idx]:
                    try:
                        self.plot_widgets[p_idx].removeItem(self.plot_curves_dict[p_idx][path])
                        del self.plot_curves_dict[p_idx][path]
                    except: pass
        self.ch_paths[ch_idx] = None
        self.ch_types[ch_idx] = None
        self.ch_addrs[ch_idx] = None
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        self.restart_sampling_thread()

    def refresh_ui_channel_bar(self):
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        for i in range(3):
            labels[i].setText(f"CH{i+1}: {self.ch_paths[i]} 🔄" if self.ch_paths[i] else f"CH{i+1}: 📭 空闲")

    def quick_add_variable(self):
        name = self.input_name.text().strip()
        v_type = self.input_type.currentText()
        if not name: return
        current_tab_idx = self.tab_widget.currentIndex()
        if current_tab_idx < 0: return

        if name.startswith("0x") or name.startswith("0X"):
            self.db.save_var(name, v_type, 0, name, current_tab_idx)
            self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
            self.input_name.clear(); return

        if name not in self.symbol_cache:
            QMessageBox.warning(self, "提示", f"符号名 '{name}' 未在 ELF 中检索到。")
            return
        if name in self.elf_db.types:
            self.add_elf_variable(name)
        else:
            self.db.save_var(name, v_type, 0, name, current_tab_idx)
            self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
        self.input_name.clear()

    def open_advance_parser_window(self):
        self.adv_parse_dialog = ElfVariableDialog(self.elf_db, self)
        self.adv_parse_dialog.symbol_selected_signal.connect(self.add_elf_variable)
        self.adv_parse_dialog.reload_requested_signal.connect(
            lambda: self.reload_elf_symbols(show_message=True, source="manual")
        )
        self.adv_parse_dialog.show()

    def add_elf_variable(self, root):
        current_tab_idx = self.tab_widget.currentIndex()
        if current_tab_idx < 0: return
        try: records = self.elf_db.flatten(root)
        except Exception as exc:
            QMessageBox.warning(self, "ELF 变量解析失败", str(exc)); return
        for record in records:
            self.db.save_var(record["path"], record["type"], record["offset"], root, current_tab_idx)
        for record in records:
            self.inject_var_to_tree_ui(record["path"], record["type"], current_tab_idx)

    def process_injected_symbol_from_workspace(self, path, v_type, offset):
        # Kept as a compatibility entry point for old callers; extraction is
        # now always based on the root symbol's DWARF description in the ELF.
        self.add_elf_variable(path.split('.')[0].split('[')[0])

    def handle_tree_item_double_clicked(self, item, column):
        if column != 2: return
        val_text = item.text(2)
        v_type = item.text(1)
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        
        if "{结构体/数组}" in val_text or "断开" in val_text or "异常" in val_text: return
        type_info = self.type_info_for_path(path)
        if type_info is not None and not scalar_size(type_info): return

        all_db_paths = [x[0] for x in self.db.get_all_vars()]
        has_children = any(p.startswith(path + ".") or p.startswith(path + "[") for p in all_db_paths if p != path)
        if has_children: return
        
        tree = self.get_current_tree()
        initial_text = val_text
        if type_info and type_info.get("kind") == "enum" and " (" in initial_text: initial_text = initial_text.rsplit(" (", 1)[0]
        editor = InPlaceEditLineEdit(tree, initial_text, v_type, type_info)
        tree.setItemWidget(item, column, editor)
        editor.setFocus()
        editor.editing_finished_signal.connect(lambda text: self.finish_tree_item_editing(item, column, text))

    def finish_tree_item_editing(self, item, column, new_text):
        tree = self.get_current_tree()
        if tree: tree.removeItemWidget(item, column)
        if not new_text: return 
        
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        v_type = item.text(1)
        all_db_vars = {x[0]: (x[2], x[3]) for x in self.db.get_all_vars()}
        if path not in all_db_vars: return
        offset, base_var = all_db_vars[path]
        
        if base_var.startswith("0x") or base_var.startswith("0X"):
            addr_hex = hex(int(base_var, 16) + offset)
        else:
            base_addr = self.symbol_cache.get(base_var)
            if base_addr is None: return
            addr_hex = hex(base_addr + offset)
            
        try:
            type_info = self.type_info_for_path(path)
            if type_info is not None:
                data = self.encode_value(new_text, type_info)
                self.write_memory_bytes(int(addr_hex, 16), data)
                item.setText(2, new_text)
                return
            norm_type = v_type.replace("_t", "").strip()
            width = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
            if "float" in norm_type:
                raw_int = struct.unpack('<I', struct.pack('<f', float(new_text)))[0]
                self.send_write_command(addr_hex, raw_int, 4, is_float=True)
            else:
                self.send_write_command(addr_hex, int(float(new_text)), width)
            item.setText(2, new_text)
        except: pass

    def remove_variable_root(self, root_item):
        current_tab_idx = self.tab_widget.currentIndex()
        root_name = root_item.text(0)
        for path, _, _, _, t_idx in self.db.get_all_vars():
            # 💡 【完美修复】：彻底移除掉混入的 nudge 噪声单词，恢复纯净的逻辑判断
            if t_idx == current_tab_idx and (path == root_name or path.startswith(root_name + ".") or path.startswith(root_name + "[")):
                self.db.delete_var(path, current_tab_idx)
                for i in range(3):
                    if self.ch_paths[i] == path: self.unbind_channel(i)
                        
        tree = self.get_current_tree()
        if tree:
            index = tree.indexOfTopLevelItem(root_item)
            if index >= 0: tree.takeTopLevelItem(index)
        self.restart_sampling_thread()
        
    def restart_sampling_thread(self):
        if getattr(self, 'elf_reload_in_progress', False): return
        self.stop_sampling_thread()
        if self.backend_suspended:
            return
            
        hz_idx = self.wave_hz_combo.currentIndex()
        if hz_idx == 0:
            try: hz = int(self.custom_hz_edit.text().strip())
            except: hz = 300
        else: hz = [300, 500, 1000, 2000, 4000, 8000][hz_idx]
            
        self.custom_hz_edit.setEnabled(hz_idx == 0)
        delay = 1.0 / hz
        active_channels = {}
        for i in range(3):
            if self.ch_addrs[i]: active_channels[i] = (self.ch_addrs[i], self.ch_types[i], self.ch_paths[i])
        if active_channels:
            self.worker = HighSpeedSamplingWorker(self.config, active_channels, delay, self.jlink_native_instance, self.elf_db)
            self.worker.data_received.connect(self.handle_high_speed_data)
            self.worker.start()

    def stop_sampling_thread(self, wait_ms=250):
        worker = getattr(self, 'worker', None)
        self.worker = None
        if not worker:
            return True
        stopped = worker.stop(wait_ms)
        if not stopped:
            # Keep ownership until the blocked driver call returns.  This is
            # crucial: destroying a still-running QThread can crash Qt.
            self.retired_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._forget_retired_worker(w))
        else:
            worker.deleteLater()
        return stopped

    def _forget_retired_worker(self, worker):
        if worker in self.retired_workers:
            self.retired_workers.remove(worker)
        worker.deleteLater()

    def low_speed_tree_refresh(self):
        if self.layout_switching or self.backend_suspended: return
        all_db_vars = {x[0]: (x[2], x[3], x[4] if len(x)>4 else 0) for x in self.db.get_all_vars()}
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        locker = QMutexLocker(self.buffer_mutex)
        
        for i in range(3):
            if self.ch_paths[i]:
                labels[i].setText(f"CH{i+1}: {self.ch_buffers[i][-1]:.2f}" if (self.ch_buffers[i] and (self.config.backend_mode == "JLINK" or self.openocd_link_alive)) else f"CH{i+1}: 断开")
            else: labels[i].setText(f"CH{i+1}: 📭 空闲")

        def refresh_item(item, t_idx):
            tree = self.tab_widget.widget(t_idx)
            if not tree or tree.itemWidget(item, 2) is not None: return

            path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
            v_type = item.text(1)
            if "{结构体/数组}" in item.text(2):
                for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                return
                
            for i in range(3):
                if self.ch_paths[i] == path:
                    if self.config.backend_mode == "OPENOCD" and not self.openocd_link_alive: item.setText(2, "断开")
                    else: item.setText(2, f"{self.ch_buffers[i][-1]:.4f}" if self.ch_buffers[i] else "载入中")
                    return
            
            if path not in all_db_vars: return
            offset, base_var, _ = all_db_vars[path]
            if base_var.startswith("0x") or base_var.startswith("0X"): absolute_address = int(base_var, 16) + offset
            else:
                base_addr = self.symbol_cache.get(base_var)
                if base_addr is None: return
                absolute_address = base_addr + offset
            
            try:
                type_info = self.type_info_for_path(path)
                if type_info is not None and scalar_size(type_info):
                    value = self.decode_value(self.read_memory_bytes(absolute_address, scalar_size(type_info)), type_info)
                    item.setText(2, f"{value:.4f}" if isinstance(value, float) else str(value))
                    for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                    return
                norm_type = v_type.strip()
                is_custom_enum = False
                width_bytes = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
                if self.config.backend_mode == "OPENOCD":
                    if not self.openocd_link_alive: raise Exception()
                    cmd = "mdb" if width_bytes == 1 else ("mdh" if width_bytes == 2 else "mdw")
                    res = self.send_tcl_cmd(f"{cmd} {hex(absolute_address)}")
                    if "ERROR" in res or ":" not in res: raise Exception()
                    raw_int = int(res.split(":")[-1].strip(), 16)
                else:
                    if not self.jlink_native_instance: raise Exception()
                    if width_bytes == 1: raw_int = self.jlink_native_instance.memory_read8(absolute_address, 1)[0]
                    elif width_bytes == 2: raw_int = self.jlink_native_instance.memory_read16(absolute_address, 1)[0]
                    else: raw_int = self.jlink_native_instance.memory_read32(absolute_address, 1)[0]
                
                if "uint8" in norm_type: v = raw_int & 0xFF
                elif "int8" in norm_type or "char" in norm_type: v = struct.unpack('<b', struct.pack('<B', raw_int & 0xFF))[0]
                elif "uint16" in norm_type: v = raw_int & 0xFFFF
                elif "int16" in norm_type: v = struct.unpack('<h', struct.pack('<H', raw_int & 0xFFFF))[0]
                elif "uint32" in norm_type: v = raw_int & 0xFFFFFFFF
                elif "float" in norm_type: v = struct.unpack('<f', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                elif is_custom_enum: v = raw_int & 0xFF
                else: v = struct.unpack('<i', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                item.setText(2, f"{v:.4f}" if "float" in norm_type else str(v))
            except: item.setText(2, "断开")
            for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)

        for tab_i in range(self.tab_widget.count()):
            tree = self.tab_widget.widget(tab_i)
            if tree:
                for i in range(tree.topLevelItemCount()): refresh_item(tree.topLevelItem(i), tab_i)

    def inject_var_to_tree_ui(self, path, v_type, tab_idx):
        tree = self.tab_widget.widget(tab_idx)
        if not tree: return
        parts = re.findall(r'^[a-zA-Z_]\w*|\.[a-zA-Z_]\w*|\[\d+\]', path)
        if not parts: return
        current_parent = None
        current_path_accumulator = ""

        for i, part in enumerate(parts):
            current_path_accumulator += part
            display_name = part.lstrip('.')  
            found_child = None
            if current_parent is None:
                for idx in range(tree.topLevelItemCount()):
                    if tree.topLevelItem(idx).text(0) == display_name:
                        found_child = tree.topLevelItem(idx); break
            else:
                for idx in range(current_parent.childCount()):
                    if current_parent.child(idx).text(0) == display_name:
                        found_child = current_parent.child(idx); break

            if not found_child:
                child_type = "{未知}"
                for h_path, h_type, _, _, h_t_idx in self.db.get_all_vars():
                    if h_t_idx == tab_idx and h_path == current_path_accumulator:
                        child_type = h_type; break
                if child_type == "{未知}" and i < len(parts) - 1: child_type = "struct/array"

                node_info = self.type_info_for_path(current_path_accumulator)
                is_container = ((node_info is not None and not scalar_size(node_info)) or
                                ("[" in child_type and not re.search(r'\[\d+\]$', current_path_accumulator)) or
                                i < len(parts) - 1 or child_type == "struct/array")
                val_display = "{结构体/数组}" if is_container else "读取中..."
                new_item = QTreeWidgetItem([display_name, child_type, val_display])
                new_item.setData(0, Qt.ItemDataRole.UserRole, current_path_accumulator)
                
                if current_parent is None: tree.addTopLevelItem(new_item)
                else: current_parent.addChild(new_item)
                found_child = new_item
            current_parent = found_child

    def update_hardware_heartbeat(self):
        is_alive = False
        if self.backend_suspended:
            is_alive = False
        elif self.config.backend_mode == "OPENOCD":
            worker_alive = hasattr(self, 'worker') and self.worker and self.worker.isRunning() and self.worker.is_link_alive
            has_worker = hasattr(self, 'worker') and self.worker and self.worker.isRunning()
            is_alive = self.openocd_link_alive and (not has_worker or worker_alive)
        else:
            # Do not call into the native DLL from the GUI heartbeat.  Some
            # probe/USB disconnects make target_connected() wait inside the
            # driver and would freeze every widget, including Disconnect.
            worker_alive = bool(self.worker and self.worker.isRunning() and self.worker.is_link_alive)
            is_alive = bool(self.jlink_native_instance) and (not self.worker or worker_alive)

        if is_alive:
            self.heartbeat_state = not self.heartbeat_state
            self.lbl_led.setStyleSheet(f"background-color: {'#00E676' if self.heartbeat_state else '#00b252'}; border-radius: 5px;")
            self.lbl_status_text.setText(f"调试在线 ({self.config.backend_mode})")
            self.lbl_status_text.setStyleSheet(f"color: {self.theme_colors['status_ok']}; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
            self.lbl_status_text.setText("通信后端已断开" if self.backend_suspended else "链路断开")
            self.lbl_status_text.setStyleSheet("color: #d32f2f; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")

    def closeEvent(self, event):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "Flash 烧录尚未完成，为避免中断导致芯片无法启动，请等待烧录结束。")
            event.ignore()
            return
        if hasattr(self, 'elf_watch_timer'): self.elf_watch_timer.stop()
        self.backend_suspended = True
        self.stop_sampling_thread()
        if self.tcl_socket:
            try: self.tcl_socket.close()
            except: pass
        if self.config.backend_mode == "OPENOCD":
            try: stop_managed_openocd(self.config)
            except: pass
        # Process teardown releases a native J-Link handle.  Calling close()
        # here can block forever after USB removal, so never hold the GUI close
        # event on the vendor DLL.
        self.jlink_native_instance = None
        event.accept()


class HighSpeedSamplingWorker(QThread):
    data_received = pyqtSignal(list)

    def __init__(self, config, active_channels, sample_delay, jlink_instance=None, elf_db=None):
        super().__init__()
        self.config = config
        self.channels = active_channels 
        self.sample_delay = sample_delay
        self.jlink = jlink_instance
        self.elf_db = elf_db
        self.running = True
        self.is_link_alive = False  
        self.active_socket = None

    def run(self):
        s = None
        parsed_channels = []
        for ch_idx, (addr_hex, v_type, path) in self.channels.items():
            addr_int = int(addr_hex, 16)
            info = None
            if self.elf_db and not path.startswith(("0x", "0X")):
                try: info = self.elf_db.resolve(path)[1]
                except Exception: pass
            norm_type = v_type.replace("_t", "").strip().lower()
            width = scalar_size(info) if info else (1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4))
            encoding = int(info.get("encoding", 0) or 0) if info else 0
            parsed_channels.append({
                'ch_idx': ch_idx, 'addr': addr_int, 'width': width, 
                'is_float': encoding == 0x04 if info else "float" in norm_type,
                'is_signed': encoding in (0x05, 0x06) if info else "int" in norm_type and "uint" not in norm_type
            })

        if self.config.backend_mode == "OPENOCD":
            if not parsed_channels:
                use_block_read = False
                merged_cmd_bytes = b""
            else:
                addrs = [c['addr'] for c in parsed_channels]
                block_start, block_end = min(addrs) & ~0x3, (max(addrs) + 7) & ~0x3
                word_count = (block_end - block_start) // 4
                use_block_read = (block_end - block_start) <= 128 and len(parsed_channels) > 1 and all(c['width'] <= 4 for c in parsed_channels)
                def read_command(channel):
                    width = channel['width']
                    if width == 1: return f"mdb {hex(channel['addr'])}"
                    if width == 2: return f"mdh {hex(channel['addr'])}"
                    if width == 4: return f"mdw {hex(channel['addr'])}"
                    return f"mdb {hex(channel['addr'])} {width}"
                merged_cmd = f"mdw {hex(block_start)} {word_count}\x1a" if use_block_read else " ; ".join(read_command(c) for c in parsed_channels) + "\x1a"
                merged_cmd_bytes = merged_cmd.encode('utf-8')

            def connect_openocd():
                candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                candidate.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                candidate.settimeout(0.3)
                candidate.connect((self.config.openocd_host, self.config.tcl_port))
                return candidate

            try:
                s = connect_openocd()
                self.active_socket = s
                self.is_link_alive = True
            except Exception:
                self.is_link_alive = False

        local_batch = []
        last_flush_time = time.time()

        while self.running:
            t_now = time.time()
            packet = {}
            if self.config.backend_mode == "OPENOCD":
                if not merged_cmd_bytes or not self.is_link_alive:
                    if not merged_cmd_bytes:
                        time.sleep(0.04); continue
                    try:
                        if s: s.close()
                        s = connect_openocd()
                        self.active_socket = s
                        self.is_link_alive = True
                    except Exception:
                        self.is_link_alive = False
                        time.sleep(0.25)
                        continue
                try:
                    s.sendall(merged_cmd_bytes)
                    res = receive_tcl_response(s)
                    if "ERROR" in res: raise Exception()
                    lines = [l.strip() for l in res.split('\n') if l.strip()]
                    
                    if use_block_read:
                        words = []
                        for line in lines:
                            if ":" in line: words.extend([int(x, 16) for x in line.split(":")[-1].strip().split()])
                        for c in parsed_channels:
                            idx = (c['addr'] - block_start) // 4
                            shift = ((c['addr'] - block_start) % 4) * 8
                            if idx < len(words):
                                raw_val = (words[idx] >> shift) & ((1 << (c['width']*8)) - 1)
                                packet[c['ch_idx']] = decode_sample_value(raw_val, c)
                    else:
                        for i, c in enumerate(parsed_channels):
                            if i < len(lines):
                                tokens = lines[i].split(":")[-1].strip().split()
                                if c['width'] > 4:
                                    raw_int = int.from_bytes(bytes(int(token, 16) & 0xFF for token in tokens[:c['width']]), "little")
                                else:
                                    raw_int = int(tokens[0], 16)
                                packet[c['ch_idx']] = decode_sample_value(raw_int, c)
                except Exception:
                    self.is_link_alive = False
                    if s:
                        try: s.close()
                        except Exception: pass
                    s = None
                    self.active_socket = None
                    continue
            else:
                if self.jlink:
                    try:
                        for c in parsed_channels:
                            if c['width'] == 1: raw = self.jlink.memory_read8(c['addr'], 1)[0]
                            elif c['width'] == 2: raw = self.jlink.memory_read16(c['addr'], 1)[0]
                            elif c['width'] == 4: raw = self.jlink.memory_read32(c['addr'], 1)[0]
                            else: raw = int.from_bytes(bytes(self.jlink.memory_read8(c['addr'], c['width'])), "little")
                            packet[c['ch_idx']] = decode_sample_value(raw, c)
                        self.is_link_alive = True
                    except:
                        self.is_link_alive = False
                        time.sleep(0.04); continue
                else:
                    self.is_link_alive = False
                    time.sleep(0.04); continue

            if packet: local_batch.append((t_now, packet))
            if len(local_batch) >= 20 or (t_now - last_flush_time > 0.025):
                if self.running and local_batch: self.data_received.emit(local_batch)
                local_batch, last_flush_time = [], t_now
            if self.sample_delay > 0: time.sleep(self.sample_delay)

        if s: 
            try: s.close()
            except: pass
        self.active_socket = None

    def stop(self, wait_ms=250):
        self.running = False
        sock = self.active_socket
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: sock.close()
            except Exception: pass
            self.active_socket = None
        return self.wait(max(0, int(wait_ms)))






if __name__ == "__main__":
    app = QApplication(sys.argv)
    pg.setConfigOption('foreground', '#e0e0e0')
    app.setStyleSheet(DARK_STYLE)
    db = LiveWatchDB()
    dlg = LoginConfigDialog(db)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.is_connected:
        win = MCUStudioWindow(dlg.config, db)
        win.show()
        sys.exit(app.exec())
