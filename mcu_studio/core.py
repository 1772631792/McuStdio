"""ELF metadata, persisted configuration, and backend utilities."""

from __future__ import annotations

import os
import ipaddress
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import string
from pathlib import Path
from typing import Any, Dict

try:
    import winreg
except ImportError:
    winreg = None

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
PORTABLE_MARKER = "portable-mode.flag"


def application_data_dir():
    """Return a writable data directory without cluttering the EXE directory."""
    override = os.environ.get("MCU_STUDIO_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    launch_dir = Path(sys.argv[0]).resolve().parent
    if getattr(sys, "frozen", False):
        if (launch_dir / PORTABLE_MARKER).is_file():
            return launch_dir / "data"
        # A single-file release should keep using an explicitly supplied
        # database beside the EXE. Only new installs fall back to AppData.
        if (launch_dir / "live_watch_history.db").is_file():
            return launch_dir
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local_app_data:
            return Path(local_app_data) / "McuStudio"
        return Path.home() / "AppData" / "Local" / "McuStudio"
    return launch_dir


def receive_tcl_response(sock, chunk_size=4096, max_bytes=1024 * 1024):
    """Read one complete OpenOCD TCL response, including fragmented packets."""
    chunks = bytearray()
    while TCL_TERMINATOR not in chunks:
        block = sock.recv(chunk_size)
        if not block:
            raise ConnectionError("OpenOCD TCL connection closed")
        chunks.extend(block)
        if len(chunks) > max_bytes:
            raise RuntimeError("OpenOCD TCL response exceeded the 1 MiB safety limit")
    return bytes(chunks).split(TCL_TERMINATOR, 1)[0].decode("utf-8", errors="replace").strip()


def openocd_scripts_dir(executable):
    executable_dir = os.path.dirname(os.path.abspath(executable))
    candidates = (
        os.path.abspath(os.path.join(executable_dir, "../share/openocd/scripts")),
        os.path.abspath(os.path.join(executable_dir, "scripts")),
    )
    return next((path for path in candidates if os.path.isdir(path)), candidates[0])


def discover_openocd_configs(executable, subfolder):
    """Return cfg paths relative to OpenOCD's interface/target directory."""
    root = Path(openocd_scripts_dir(executable)) / subfolder
    if not root.is_dir():
        return []
    return sorted(
        (path.relative_to(root).as_posix() for path in root.rglob("*.cfg") if path.is_file()),
        key=str.casefold,
    )


def default_openocd_executable():
    """Locate the repository/release OpenOCD without depending on the CWD."""
    launch_dir = Path(sys.argv[0]).resolve().parent
    project_dir = Path(__file__).resolve().parent.parent
    candidates = (
        launch_dir / "runtime" / "OpenOCD" / "bin" / "openocd.exe",
        launch_dir / "OpenOCD" / "bin" / "openocd.exe",
        launch_dir.parent / "runtime" / "OpenOCD" / "bin" / "openocd.exe",
        project_dir / "runtime" / "OpenOCD" / "bin" / "openocd.exe",
    )
    match = next((path for path in candidates if path.is_file()), candidates[0])
    return os.path.normpath(str(match))


def _unique_existing_paths(candidates, validator):
    """Return normalized valid paths in priority order without duplicates."""
    results = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(str(candidate)))))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            validator(path)
        except (OSError, ValueError):
            continue
        results.append(path)
    return results


def discover_jlink_directories(preferred="", current=""):
    """Find usable SEGGER installations, keeping saved/user paths first."""
    candidates = [preferred, current]
    if winreg is not None:
        registry_keys = (
            r"SOFTWARE\SEGGER\J-Link",
            r"SOFTWARE\WOW6432Node\SEGGER\J-Link",
        )
        for key_name in registry_keys:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name) as key:
                    for value_name in ("InstallPath", "Path", "InstallDir"):
                        try:
                            candidates.append(winreg.QueryValueEx(key, value_name)[0])
                        except OSError:
                            pass
            except OSError:
                pass
    commander = shutil.which("JLink.exe")
    if commander:
        candidates.append(os.path.dirname(commander))
    for base in filter(None, (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))):
        segger = Path(base) / "SEGGER"
        candidates.append(segger / "JLink")
        if segger.is_dir():
            candidates.extend(sorted(segger.glob("JLink*"), reverse=True))

    def validate(directory):
        jlink_dll_path(directory)

    return _unique_existing_paths(candidates, validate)


def discover_openocd_executables(preferred="", current=""):
    """Find OpenOCD executables, preferring the persisted project choice."""
    candidates = [preferred, current, default_openocd_executable(), shutil.which("openocd.exe")]
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for base in filter(None, program_files):
        root = Path(base)
        candidates.extend((
            root / "OpenOCD" / "bin" / "openocd.exe",
            root / "xPacks" / "OpenOCD" / "bin" / "openocd.exe",
        ))
        stm_plugins = root / "STMicroelectronics" / "STM32Cube" / "STM32CubeIDE" / "plugins"
        if stm_plugins.is_dir():
            candidates.extend(stm_plugins.glob("com.st.stm32cube.ide.mcu.externaltools.openocd.win32_*/tools/bin/openocd.exe"))
    user_profile = Path(os.environ.get("USERPROFILE", ""))
    xpack_root = user_profile / ".local" / "xPacks" / "@xpack-dev-tools" / "openocd"
    if xpack_root.is_dir():
        candidates.extend(sorted(xpack_root.glob("*/.content/bin/openocd.exe"), reverse=True))
    chocolatey = os.environ.get("ChocolateyInstall")
    if chocolatey:
        candidates.append(Path(chocolatey) / "bin" / "openocd.exe")

    def validate(executable):
        if os.path.basename(executable).casefold() != "openocd.exe" or not os.path.isfile(executable):
            raise FileNotFoundError(executable)

    return _unique_existing_paths(candidates, validate)


def fixed_drive_roots():
    """Return local fixed-drive roots without touching network volumes."""
    if sys.platform != "win32":
        return [os.path.abspath(os.sep)]
    try:
        import ctypes
        drive_type = ctypes.windll.kernel32.GetDriveTypeW
        return [
            f"{letter}:\\" for letter in string.ascii_uppercase
            if os.path.isdir(f"{letter}:\\") and drive_type(f"{letter}:\\") == 3
        ]
    except (AttributeError, OSError):
        return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:\\")]


def scan_debugger_installations(stop_requested=None, progress=None):
    """Search fixed disks for OpenOCD and J-Link from a background thread."""
    stop_requested = stop_requested or (lambda: False)
    openocd_paths = []
    jlink_paths = []
    openocd_seen = set()
    jlink_seen = set()
    skipped_names = {
        "$recycle.bin", "system volume information", "windows", "winsxs",
        "node_modules", ".git", "__pycache__", ".cache", "temp", "tmp",
    }
    visited = 0
    last_report = 0
    for drive in fixed_drive_roots():
        for root, directories, filenames in os.walk(drive, topdown=True, followlinks=False):
            if stop_requested():
                return jlink_paths, openocd_paths
            directories[:] = [
                name for name in directories
                if name.casefold() not in skipped_names and not name.startswith("$Windows.~")
            ]
            visited += 1
            lower_files = {name.casefold(): name for name in filenames}
            openocd_name = lower_files.get("openocd.exe")
            if openocd_name:
                candidate = os.path.normpath(os.path.join(root, openocd_name))
                key = os.path.normcase(candidate)
                if key not in openocd_seen:
                    openocd_seen.add(key)
                    openocd_paths.append(candidate)
            if "jlink_x64.dll" in lower_files or "jlinkarm.dll" in lower_files:
                candidate = os.path.normpath(root)
                key = os.path.normcase(candidate)
                if key not in jlink_seen:
                    try:
                        jlink_dll_path(candidate)
                    except (OSError, ValueError):
                        pass
                    else:
                        jlink_seen.add(key)
                        jlink_paths.append(candidate)
            if progress and (visited - last_report >= 2500 or openocd_name):
                last_report = visited
                progress(list(jlink_paths), list(openocd_paths), visited, drive)
    if progress:
        progress(list(jlink_paths), list(openocd_paths), visited, "")
    return jlink_paths, openocd_paths


def resolve_openocd_config(executable, value, subfolder):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"OpenOCD {subfolder} cfg 不能为空")
    if os.path.isabs(value):
        path = os.path.abspath(os.path.expanduser(value))
    else:
        relative = value.replace("\\", "/")
        prefix = f"{subfolder}/"
        if relative.casefold().startswith(prefix.casefold()):
            path = os.path.join(openocd_scripts_dir(executable), *relative.split("/"))
        else:
            path = os.path.join(openocd_scripts_dir(executable), subfolder, *relative.split("/"))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到 OpenOCD {subfolder} cfg：{path}")
    return os.path.normpath(path)


def validate_openocd_config(config):
    executable_value = str(config.openocd_exe or "").strip()
    if not executable_value:
        raise FileNotFoundError("OpenOCD 路径未设置")
    executable = os.path.abspath(os.path.expanduser(executable_value))
    if not os.path.isfile(executable):
        raise FileNotFoundError(f"找不到 OpenOCD：{executable or '(未设置)'}")
    if os.path.basename(executable).casefold() != "openocd.exe":
        raise ValueError(f"OpenOCD 路径必须指向 openocd.exe：{executable}")
    try:
        host = str(ipaddress.ip_address(str(config.openocd_host).strip()))
    except ValueError as exc:
        raise ValueError(f"无效的 OpenOCD 监听地址：{config.openocd_host}") from exc
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("OpenOCD TCL 仅允许绑定本机回环地址")
    port = int(config.tcl_port)
    if not 1 <= port <= 65535:
        raise ValueError(f"无效的 OpenOCD TCL 端口：{port}")
    return {
        "executable": executable,
        "scripts": openocd_scripts_dir(executable),
        "interface": resolve_openocd_config(executable, config.interface, "interface"),
        "target": resolve_openocd_config(executable, config.target, "target"),
        "host": host,
        "port": port,
    }


def connect_openocd_tcl(config, timeout=0.5):
    """Connect to the configured TCL service and verify that it is OpenOCD."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(timeout)
    try:
        sock.connect((config.openocd_host, int(config.tcl_port)))
        sock.sendall(b"version\x1a")
        response = receive_tcl_response(sock)
        if "Open On-Chip Debugger" not in response:
            raise ConnectionError("TCL 端口响应不是 OpenOCD")
        return sock
    except Exception:
        sock.close()
        raise


def _openocd_log_tail(config, limit=4000):
    handle = getattr(config, "openocd_log_handle", None)
    if handle:
        try:
            handle.flush()
        except OSError:
            pass
    path = getattr(config, "openocd_log_path", "")
    if not path or not os.path.isfile(path):
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _close_openocd_log(config, remove=True):
    handle = getattr(config, "openocd_log_handle", None)
    if handle:
        try:
            handle.close()
        except OSError:
            pass
    config.openocd_log_handle = None
    path = getattr(config, "openocd_log_path", "")
    if remove and path:
        try:
            os.remove(path)
        except OSError:
            pass
        config.openocd_log_path = ""


def launch_openocd(config, startup_timeout=5.0):
    """Start, identify, and retain only the configured OpenOCD instance."""
    details = validate_openocd_config(config)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((details["host"], details["port"]))
        except OSError as exc:
            raise RuntimeError(
                f"OpenOCD TCL 端口 {details['host']}:{details['port']} 已被占用；"
                "为避免误连其他调试进程，已取消启动。"
            ) from exc

    startupinfo = subprocess.STARTUPINFO() if os.name == "nt" else None
    creationflags = 0
    if config.show_log_window:
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    elif startupinfo is not None:
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    popen_kwargs = {}
    if not config.show_log_window:
        descriptor, log_path = tempfile.mkstemp(prefix="mcu_studio_openocd_", suffix=".log")
        log_handle = os.fdopen(descriptor, "w+", encoding="utf-8", errors="replace")
        config.openocd_log_path = log_path
        config.openocd_log_handle = log_handle
        popen_kwargs.update(stdout=log_handle, stderr=subprocess.STDOUT)
    try:
        config.openocd_process = subprocess.Popen(
            [details["executable"]] + config.get_openocd_args(),
            cwd=os.path.dirname(details["executable"]),
            startupinfo=startupinfo,
            creationflags=creationflags,
            **popen_kwargs,
        )
        deadline = time.monotonic() + max(0.5, float(startup_timeout))
        last_error = ""
        while time.monotonic() < deadline:
            return_code = config.openocd_process.poll()
            if return_code is not None:
                log = _openocd_log_tail(config)
                raise RuntimeError(
                    f"OpenOCD 启动失败，退出码 {return_code}。\n{log or '没有可用日志。'}"
                )
            try:
                test_socket = connect_openocd_tcl(config, timeout=0.3)
                test_socket.close()
                return config.openocd_process
            except (OSError, RuntimeError, ConnectionError) as exc:
                last_error = str(exc)
                time.sleep(0.1)
        log = _openocd_log_tail(config)
        raise RuntimeError(
            f"OpenOCD 在 {startup_timeout:.1f} 秒内未就绪：{last_error}\n"
            f"{log or '没有可用日志。'}"
        )
    except Exception:
        stop_managed_openocd(config)
        raise


def stop_managed_openocd(config):
    process = getattr(config, "openocd_process", None)
    try:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
    finally:
        config.openocd_process = None
        _close_openocd_log(config)


def jlink_dll_path(jlink_dir):
    """Return the DLL matching this Python process; never fall back across bitness."""
    directory_value = str(jlink_dir or "").strip()
    if not directory_value:
        raise FileNotFoundError("J-Link 目录未设置")
    directory = os.path.abspath(os.path.expanduser(directory_value))
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"找不到 J-Link 目录：{directory or '(未设置)'}")
    dll_name = "JLink_x64.dll" if struct.calcsize("P") == 8 else "JLinkARM.dll"
    path = os.path.join(directory, dll_name)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"当前 Python 为 {struct.calcsize('P') * 8} 位，需要同位数 J-Link DLL：{path}"
        )
    return path


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


def decode_openocd_memory_response(response, channels, block_start=None, word_count=None):
    """Decode OpenOCD mdw/mdh/mdb output with the same scalar rules as J-Link."""
    if "ERROR" in response.upper():
        raise RuntimeError("OpenOCD memory read failed")
    lines = [line.strip() for line in response.splitlines() if ":" in line]
    packet = {}
    if block_start is not None:
        words = []
        for line in lines:
            words.extend(int(token, 16) for token in line.split(":", 1)[1].strip().split())
        expected = int(word_count or 0)
        if len(words) < expected:
            raise RuntimeError("OpenOCD block read returned too few words")
        for channel in channels:
            offset = channel["addr"] - block_start
            index, byte_offset = divmod(offset, 4)
            raw_value = (words[index] >> (byte_offset * 8)) & ((1 << (channel["width"] * 8)) - 1)
            packet[channel["ch_idx"]] = decode_sample_value(raw_value, channel)
        return packet

    if len(lines) != len(channels):
        raise RuntimeError("OpenOCD returned an unexpected number of memory rows")
    for line, channel in zip(lines, channels):
        tokens = line.split(":", 1)[1].strip().split()
        if channel.get("byte_read") or channel["width"] > 4:
            if len(tokens) < channel["width"]:
                raise RuntimeError("OpenOCD byte read returned too few bytes")
            raw_value = int.from_bytes(
                bytes(int(token, 16) & 0xFF for token in tokens[:channel["width"]]), "little"
            )
        else:
            if not tokens:
                raise RuntimeError("OpenOCD memory read returned no value")
            raw_value = int(tokens[0], 16)
        packet[channel["ch_idx"]] = decode_sample_value(raw_value, channel)
    return packet


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
        self.backend_mode = "JLINK" 
        self.openocd_exe = ""
        self.interface = ""  
        self.target = ""     
        self.jlink_dll_dir = ""        
        self.jlink_device = "Cortex-M0+" 
        self.jlink_connect_device = "Cortex-M0+"
        self.openocd_host = "127.0.0.1"
        self.tcl_port = 6666
        self.show_log_window = False  
        self.openocd_process = None
        self.openocd_log_handle = None
        self.openocd_log_path = ""
        self.theme_mode = "DARK"

    def get_openocd_args(self):
        details = validate_openocd_config(self)
        search_dirs = [details["scripts"]]
        for config_path in (details["interface"], details["target"]):
            config_dir = os.path.dirname(config_path)
            if config_dir not in search_dirs:
                search_dirs.append(config_dir)
        args = []
        for search_dir in search_dirs:
            args.extend(("-s", search_dir))
        args.extend([
            "-c", f"bindto {details['host']}",
            "-c", f"tcl port {details['port']}",
            "-c", "gdb port disabled",
            "-c", "telnet port disabled",
            "-f", details["interface"],
            "-f", details["target"],
        ])
        return args

class LiveWatchDB:
    def __init__(self):
        base_dir = application_data_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        self.path = str(base_dir / "live_watch_history.db")
        self.conn = sqlite3.connect(self.path)
        self.cursor = self.conn.cursor()
        # DELETE keeps the persistent state in one visible .db file. SQLite's
        # rollback journal exists only while a transaction is being committed.
        self.cursor.execute("PRAGMA journal_mode=DELETE")
        self.cursor.execute("PRAGMA synchronous=NORMAL")
        self._initialize_profile_schema()
        self._vars_cache = None

    def _table_columns(self, table):
        return {row[1] for row in self.cursor.execute(f'PRAGMA table_info("{table}")')}

    def _initialize_profile_schema(self):
        """Create profiles and migrate the former single-configuration schema."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                is_main INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )''')
        self.cursor.execute(
            "INSERT OR IGNORE INTO profiles(id, name, is_main) VALUES (1, '主配置', 1)"
        )
        self.cursor.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)')

        table_specs = {
            "variables": (
                {"name", "type", "offset", "base_var", "tab_idx"},
                '''CREATE TABLE variables (
                    profile_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT,
                    offset INTEGER,
                    base_var TEXT,
                    tab_idx INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (profile_id, name, tab_idx)
                )''',
                "INSERT INTO variables(profile_id, name, type, offset, base_var, tab_idx) "
                "SELECT 1, name, type, offset, base_var, tab_idx FROM {legacy}",
            ),
            "config_cache": (
                {"key", "value"},
                '''CREATE TABLE config_cache (
                    profile_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    PRIMARY KEY (profile_id, key)
                )''',
                "INSERT INTO config_cache(profile_id, key, value) "
                "SELECT 1, key, value FROM {legacy}",
            ),
            "c_code_cache": (
                {"id", "code"},
                '''CREATE TABLE c_code_cache (
                    profile_id INTEGER NOT NULL,
                    id INTEGER NOT NULL,
                    code TEXT,
                    PRIMARY KEY (profile_id, id)
                )''',
                "INSERT INTO c_code_cache(profile_id, id, code) "
                "SELECT 1, id, code FROM {legacy}",
            ),
            "sample_history": (
                {"id", "session_id", "time_ms", "variable", "value"},
                '''CREATE TABLE sample_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    time_ms REAL NOT NULL,
                    variable TEXT NOT NULL,
                    value REAL NOT NULL
                )''',
                "INSERT INTO sample_history(id, profile_id, session_id, time_ms, variable, value) "
                "SELECT id, 1, session_id, time_ms, variable, value FROM {legacy}",
            ),
        }
        self.cursor.execute("DROP INDEX IF EXISTS idx_sample_history_lookup")
        for table, (legacy_columns, create_sql, migrate_sql) in table_specs.items():
            columns = self._table_columns(table)
            if columns and "profile_id" not in columns:
                legacy = f"{table}_single_profile_legacy"
                self.cursor.execute(f'DROP TABLE IF EXISTS "{legacy}"')
                self.cursor.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')
                self.cursor.execute(create_sql)
                if legacy_columns.issubset(columns):
                    self.cursor.execute(migrate_sql.format(legacy=legacy))
                self.cursor.execute(f'DROP TABLE "{legacy}"')
            elif not columns:
                self.cursor.execute(create_sql)

        self.cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_sample_history_lookup '
            'ON sample_history(profile_id, session_id, variable, time_ms)'
        )
        row = self.cursor.execute(
            "SELECT value FROM app_meta WHERE key='active_profile_id'"
        ).fetchone()
        try:
            active_profile_id = int(row[0]) if row else 1
        except (TypeError, ValueError):
            active_profile_id = 1
        if not self.cursor.execute(
            "SELECT 1 FROM profiles WHERE id=?", (active_profile_id,)
        ).fetchone():
            active_profile_id = 1
        self.active_profile_id = active_profile_id
        self.cursor.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('active_profile_id', ?)",
            (str(active_profile_id),),
        )
        self.conn.commit()

    def list_profiles(self):
        return self.cursor.execute(
            "SELECT id, name, is_main FROM profiles ORDER BY is_main DESC, id"
        ).fetchall()

    def create_profile(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValueError("配置名称不能为空")
        if len(name) > 40:
            raise ValueError("配置名称不能超过 40 个字符")
        try:
            self.cursor.execute("INSERT INTO profiles(name, is_main) VALUES (?, 0)", (name,))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"配置名称已存在：{name}") from exc
        profile_id = int(self.cursor.lastrowid)
        self.conn.commit()
        self.set_active_profile(profile_id)
        return profile_id

    def set_active_profile(self, profile_id):
        profile_id = int(profile_id)
        if not self.cursor.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
            raise ValueError("配置不存在")
        self.active_profile_id = profile_id
        self.cursor.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('active_profile_id', ?)",
            (str(profile_id),),
        )
        self.cursor.execute(
            "UPDATE profiles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (profile_id,)
        )
        self.conn.commit()
        self._invalidate_vars_cache()

    def delete_profile(self, profile_id):
        profile_id = int(profile_id)
        row = self.cursor.execute(
            "SELECT is_main FROM profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if row is None:
            raise ValueError("配置不存在")
        if int(row[0]):
            raise ValueError("主配置不能删除")
        with self.conn:
            for table in ("variables", "config_cache", "c_code_cache", "sample_history"):
                self.conn.execute(f'DELETE FROM "{table}" WHERE profile_id=?', (profile_id,))
            self.conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
        if self.active_profile_id == profile_id:
            self.set_active_profile(1)
        self._invalidate_vars_cache()

    def clear_profile(self, profile_id=None):
        profile_id = int(profile_id or self.active_profile_id)
        if not self.cursor.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
            raise ValueError("配置不存在")
        with self.conn:
            for table in ("variables", "config_cache", "c_code_cache", "sample_history"):
                self.conn.execute(f'DELETE FROM "{table}" WHERE profile_id=?', (profile_id,))
        self._invalidate_vars_cache()

    def _invalidate_vars_cache(self):
        self._vars_cache = None

    def save_var(self, name, var_type, offset=0, base_var="", tab_idx=0):
        self.cursor.execute("INSERT OR REPLACE INTO variables (profile_id, name, type, offset, base_var, tab_idx) VALUES (?, ?, ?, ?, ?, ?)", (self.active_profile_id, name, var_type, offset, base_var, tab_idx))
        self.conn.commit()
        self._invalidate_vars_cache()

    def delete_var(self, name, tab_idx=0):
        self.cursor.execute("DELETE FROM variables WHERE profile_id=? AND name=? AND tab_idx=?", (self.active_profile_id, name, tab_idx))
        self.conn.commit()
        self._invalidate_vars_cache()

    def get_all_vars(self):
        if self._vars_cache is None:
            self.cursor.execute("SELECT name, type, offset, base_var, tab_idx FROM variables WHERE profile_id=?", (self.active_profile_id,))
            self._vars_cache = self.cursor.fetchall()
        return list(self._vars_cache)

    def get_var_map(self, tab_idx):
        return {
            row[0]: (row[2], row[3], row[1])
            for row in self.get_all_vars()
            if int(row[4]) == int(tab_idx)
        }

    def get_var_type(self, name, tab_idx):
        for row in self.get_all_vars():
            if row[0] == name and int(row[4]) == int(tab_idx):
                return row[1]
        return None

    def replace_root_vars(self, root, tab_idx, records):
        """Replace one expanded root in a single transaction."""
        self.cursor.execute(
            "DELETE FROM variables WHERE profile_id=? AND tab_idx=? AND "
            "(name=? OR substr(name, 1, length(?) + 1)=? || '.' "
            "OR substr(name, 1, length(?) + 1)=? || '[')",
            (self.active_profile_id, tab_idx, root, root, root, root, root),
        )
        self.cursor.executemany(
            "INSERT OR REPLACE INTO variables (profile_id, name, type, offset, base_var, tab_idx) VALUES (?, ?, ?, ?, ?, ?)",
            ((self.active_profile_id, record["path"], record["type"], record["offset"], root, tab_idx) for record in records),
        )
        self.conn.commit()
        self._invalidate_vars_cache()

    def save_config(self, key, value):
        self.cursor.execute("INSERT OR REPLACE INTO config_cache (profile_id, key, value) VALUES (?, ?, ?)", (self.active_profile_id, key, value))
        self.conn.commit()

    def get_config(self, key, default=""):
        self.cursor.execute("SELECT value FROM config_cache WHERE profile_id=? AND key=?", (self.active_profile_id, key))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def save_c_code(self, code):
        self.cursor.execute("INSERT OR REPLACE INTO c_code_cache (profile_id, id, code) VALUES (?, 1, ?)", (self.active_profile_id, code))
        self.conn.commit()

    def get_c_code(self):
        self.cursor.execute("SELECT code FROM c_code_cache WHERE profile_id=? AND id=1", (self.active_profile_id,))
        row = self.cursor.fetchone()
        return row[0] if row else ""

    def get_sample_history(self, variable, session_id=None):
        if session_id is None:
            self.cursor.execute(
                "SELECT session_id FROM sample_history WHERE profile_id=? AND variable=? ORDER BY id DESC LIMIT 1",
                (self.active_profile_id, variable),
            )
            row = self.cursor.fetchone()
            if row is None:
                return []
            session_id = row[0]
        self.cursor.execute(
            "SELECT time_ms, value FROM sample_history "
            "WHERE profile_id=? AND session_id=? AND variable=? ORDER BY time_ms, id",
            (self.active_profile_id, session_id, variable),
        )
        return self.cursor.fetchall()
