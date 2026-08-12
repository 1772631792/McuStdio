# -*- coding: utf-8 -*-
"""Standalone MCU watch/plot GUI.

The only project module imported by this file is ``config.py``.  Symbols and
types are read directly from an ELF file and live values are read through
J-Link.  Acquisition runs in a dedicated thread; the Tk thread only paints a
rate-limited, down-sampled view.
"""

from __future__ import annotations

import math
import os
import platform
import queue
import re
import struct
import subprocess
import threading
import time
import tkinter as tk
import tempfile
from collections import deque
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from config import (
    DEVICE,
    FLASH_TIMEOUT_SECONDS,
    JLINK_AUTOCONNECT,
    JLINK_DLL_DIR,
    JLINK_EXE,
    JLINK_INTERFACE,
    JLINK_SPEED,
    MAX_ARRAY_ELEMENTS,
    MAX_TYPE_DEPTH,
)

try:
    from elftools.dwarf.descriptions import describe_form_class
    from elftools.dwarf.dwarf_expr import DWARFExprParser
    from elftools.elf.elffile import ELFFile
except ImportError as exc:  # pragma: no cover - shown in the GUI at runtime
    ELFFile = None
    DWARFExprParser = None
    describe_form_class = None
    ELFTOOLS_IMPORT_ERROR = exc
else:
    ELFTOOLS_IMPORT_ERROR = None


APP_TITLE = "MCU Industrial Watch & Plot"
WATCH_REFRESH_HZ = 5
DEFAULT_PLOT_SAMPLE_HZ = 30
MAX_SAMPLE_HZ = 8000
GUI_REFRESH_MS = 50
DEFAULT_PLOT_WINDOW_MS = 1000
MIN_PLOT_WINDOW_MS = 10
MAX_PLOT_WINDOW_MS = 10000
DEFAULT_LINE_WIDTH = 2.0
MAX_HISTORY_POINTS = int(MAX_SAMPLE_HZ * (MAX_PLOT_WINDOW_MS / 1000.0) * 1.25)
MAX_MERGE_GAP_BYTES = 16
MAX_READ_BLOCK_BYTES = 4096
MAX_WATCH_SIGNALS = 128
MAX_PLOTTED_SIGNALS = 16

COLORS = (
    "#00d4ff", "#ffcc00", "#67e480", "#ff6b9d", "#b48cff", "#ff8f45",
    "#6ae4d9", "#ef6cff", "#b6e36b", "#7aa7ff", "#ff5f56", "#c8d1dc",
)

UI = {
    "window": "#eef2f7",
    "surface": "#ffffff",
    "surface_alt": "#f7f9fc",
    "text": "#172033",
    "muted": "#6f7b8c",
    "border": "#dce3ec",
    "accent": "#1677ff",
    "accent_hover": "#0d63db",
    "accent_soft": "#e8f2ff",
    "danger": "#e5484d",
    "warning": "#f59e0b",
    "plot": "#0b1220",
}


def type_label(type_info: Dict[str, Any]) -> str:
    return str(type_info.get("name") or type_info.get("kind", "<unknown>"))


def scalar_size(type_info: Dict[str, Any]) -> int:
    if type_info.get("kind") not in ("scalar", "enum", "pointer"):
        return 0
    return int(type_info.get("size", 0) or 0)


def decode_scalar(data: bytes, type_info: Dict[str, Any]) -> Tuple[Any, Optional[float]]:
    """Return (display value, numeric value used by the plot)."""
    size = len(data)
    kind = type_info.get("kind")
    encoding = int(type_info.get("encoding", 0) or 0)
    if kind == "pointer":
        number = int.from_bytes(data, "little", signed=False)
        return f"0x{number:0{size * 2}X}", float(number)
    if encoding == 0x04 and size in (4, 8):
        number = struct.unpack("<f" if size == 4 else "<d", data)[0]
        return number, float(number)
    signed = encoding in (0x05, 0x06)
    number = int.from_bytes(data, "little", signed=signed)
    if kind == "enum":
        enum_name = type_info.get("values", {}).get(number)
        return (f"{enum_name} ({number})" if enum_name else number), float(number)
    if encoding == 0x02:
        return bool(number), float(number)
    if encoding in (0x06, 0x08) and size == 1 and 32 <= number < 127:
        return f"{number} ('{chr(number)}')", float(number)
    return number, float(number)


class ElfDatabase:
    """ELF symbol and DWARF type database, independent from Debug.py."""

    def __init__(self) -> None:
        self.path = ""
        self.symbols: Dict[str, int] = {}
        self.types: Dict[str, Dict[str, Any]] = {}
        self._type_cache: Dict[int, Dict[str, Any]] = {}
        self._expr_parser: Any = None

    @staticmethod
    def _text(value: Any, default: str = "") -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value) if value is not None else default

    def _member_offset(self, attr: Any) -> int:
        if attr is None:
            return 0
        form_class = describe_form_class(attr.form)
        if form_class == "constant":
            return int(attr.value)
        if form_class in ("exprloc", "block") and self._expr_parser:
            try:
                ops = self._expr_parser.parse_expr(attr.value)
                if len(ops) == 1 and ops[0].op_name == "DW_OP_plus_uconst":
                    return int(ops[0].args[0])
            except Exception:
                pass
        return 0

    def _parse_type(self, die: Any) -> Dict[str, Any]:
        if die is None:
            return {"kind": "unknown", "name": "<unknown>", "size": 0}
        if die.offset in self._type_cache:
            return self._type_cache[die.offset]

        info: Dict[str, Any] = {"kind": "unknown", "name": "<unknown>", "size": 0}
        self._type_cache[die.offset] = info  # supports recursive structures
        attrs = die.attributes
        name_attr = attrs.get("DW_AT_name")
        name = self._text(name_attr.value) if name_attr else ""
        size_attr = attrs.get("DW_AT_byte_size")
        size = int(size_attr.value) if size_attr else 0

        def referenced() -> Dict[str, Any]:
            if "DW_AT_type" not in attrs:
                return {"kind": "unknown", "name": "<unknown>", "size": 0}
            return self._parse_type(die.get_DIE_from_attribute("DW_AT_type"))

        if die.tag == "DW_TAG_base_type":
            encoding = int(attrs["DW_AT_encoding"].value) if "DW_AT_encoding" in attrs else 0
            parsed = {"kind": "scalar", "name": name or "<scalar>", "size": size, "encoding": encoding}
        elif die.tag == "DW_TAG_enumeration_type":
            values: Dict[int, str] = {}
            for child in die.iter_children():
                if child.tag == "DW_TAG_enumerator":
                    n = child.attributes.get("DW_AT_name")
                    v = child.attributes.get("DW_AT_const_value")
                    if n is not None and v is not None:
                        values[int(v.value)] = self._text(n.value)
            parsed = {"kind": "enum", "name": name or "<enum>", "size": size, "values": values}
        elif die.tag == "DW_TAG_pointer_type":
            target = referenced()
            parsed = {"kind": "pointer", "name": f"{type_label(target)} *", "size": size or 4, "target": target}
        elif die.tag == "DW_TAG_array_type":
            element = referenced()
            dimensions: List[int] = []
            for child in die.iter_children():
                if child.tag != "DW_TAG_subrange_type":
                    continue
                if "DW_AT_count" in child.attributes:
                    dimensions.append(int(child.attributes["DW_AT_count"].value))
                elif "DW_AT_upper_bound" in child.attributes:
                    dimensions.append(int(child.attributes["DW_AT_upper_bound"].value) + 1)
            parsed = element
            for count in reversed(dimensions or [0]):
                parsed = {
                    "kind": "array", "name": f"{type_label(parsed)}[{count or '?'}]",
                    "size": int(parsed.get("size", 0)) * count, "count": count, "element": parsed,
                }
        elif die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            kind = "struct" if die.tag == "DW_TAG_structure_type" else "union"
            members = []
            for child in die.iter_children():
                if child.tag != "DW_TAG_member" or "DW_AT_type" not in child.attributes:
                    continue
                n = child.attributes.get("DW_AT_name")
                members.append({
                    "name": self._text(n.value, "<anonymous>") if n else "<anonymous>",
                    "offset": 0 if kind == "union" else self._member_offset(child.attributes.get("DW_AT_data_member_location")),
                    "type": self._parse_type(child.get_DIE_from_attribute("DW_AT_type")),
                })
            parsed = {"kind": kind, "name": name or f"<anonymous {kind}>", "size": size, "members": members}
        elif die.tag == "DW_TAG_typedef":
            parsed = dict(referenced())
            parsed["name"] = name or type_label(parsed)
        elif die.tag in ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type", "DW_TAG_atomic_type"):
            parsed = referenced()
        else:
            parsed = {"kind": "unknown", "name": name or die.tag.replace("DW_TAG_", ""), "size": size}

        if parsed is not info:
            info.clear()
            info.update(parsed)
        return info

    def load(self, path: str) -> None:
        if ELFFile is None:
            raise RuntimeError(f"缺少 pyelftools：{ELFTOOLS_IMPORT_ERROR}\n请执行 pip install pyelftools")
        symbols: Dict[str, int] = {}
        types: Dict[str, Dict[str, Any]] = {}
        with open(path, "rb") as stream:
            elf = ELFFile(stream)
            for section in elf.iter_sections():
                if section.header.sh_type not in ("SHT_SYMTAB", "SHT_DYNSYM"):
                    continue
                for symbol in section.iter_symbols():
                    entry = symbol.entry
                    if entry["st_info"]["type"] == "STT_OBJECT" and symbol.name and int(entry["st_value"]):
                        symbols.setdefault(symbol.name, int(entry["st_value"]))
            if elf.has_dwarf_info():
                dwarf = elf.get_dwarf_info()
                self._expr_parser = DWARFExprParser(dwarf.structs)
                self._type_cache = {}
                for cu in dwarf.iter_CUs():
                    for die in cu.iter_DIEs():
                        if die.tag != "DW_TAG_variable":
                            continue
                        if die.attributes.get("DW_AT_declaration") and die.attributes["DW_AT_declaration"].value:
                            continue

                        # GCC may emit a variable definition containing only
                        # DW_AT_specification and DW_AT_location. Its name and
                        # type then live on the referenced declaration DIE.
                        declaration_die = None
                        if "DW_AT_specification" in die.attributes:
                            declaration_die = die.get_DIE_from_attribute("DW_AT_specification")

                        n = die.attributes.get("DW_AT_name")
                        if n is None and declaration_die is not None:
                            n = declaration_die.attributes.get("DW_AT_name")
                        if not n:
                            continue
                        variable_name = self._text(n.value)
                        type_die = die
                        if "DW_AT_type" not in type_die.attributes:
                            type_die = declaration_die
                        if (variable_name in symbols) and (type_die is not None) and \
                           ("DW_AT_type" in type_die.attributes):
                            types[variable_name] = self._parse_type(
                                type_die.get_DIE_from_attribute("DW_AT_type"))
        if not symbols:
            raise RuntimeError("ELF 中没有可用的全局数据符号")
        self.path = os.path.abspath(path)
        self.symbols = symbols
        self.types = types

    def resolve(self, expression: str) -> Tuple[int, Dict[str, Any]]:
        match = re.match(r"^([A-Za-z_]\w*)", expression.strip())
        if not match:
            raise ValueError("无效变量表达式")
        root = match.group(1)
        if root not in self.symbols:
            raise KeyError(f"找不到变量：{root}")
        if root not in self.types:
            raise KeyError(f"变量 {root} 没有 DWARF 类型信息，请使用带 -g 调试信息的 ELF")
        address = self.symbols[root]
        type_info = self.types[root]
        pos = match.end()
        while pos < len(expression):
            rest = expression[pos:]
            index_match = re.match(r"^\[(\d+)\]", rest)
            if index_match:
                if type_info.get("kind") != "array":
                    raise ValueError(f"{expression[:pos]} 不是数组")
                index = int(index_match.group(1))
                count = int(type_info.get("count", 0))
                if not 0 <= index < count:
                    raise IndexError(f"数组下标越界：0..{count - 1}")
                element = type_info["element"]
                address += index * int(element.get("size", 0))
                type_info = element
                pos += index_match.end()
                continue
            member_match = re.match(r"^\.([A-Za-z_]\w*)", rest)
            if member_match:
                if type_info.get("kind") not in ("struct", "union"):
                    raise ValueError(f"{expression[:pos]} 不是结构体/联合体")
                member_name = member_match.group(1)
                member = next((m for m in type_info.get("members", []) if m["name"] == member_name), None)
                if member is None:
                    raise KeyError(f"找不到成员：{member_name}")
                address += int(member["offset"])
                type_info = member["type"]
                pos += member_match.end()
                continue
            raise ValueError(f"无法解析：{expression[pos:]}")
        return address, type_info


class JLinkTransport:
    def __init__(self) -> None:
        self.jlink: Any = None
        self.lock = threading.RLock()

    @property
    def connected(self) -> bool:
        return self.jlink is not None

    def connect(self) -> None:
        import pylink
        with self.lock:
            if self.jlink is not None:
                return
            dll_name = "JLink_x64.dll" if "64" in platform.architecture()[0] else "JLinkARM.dll"
            dll_path = os.path.join(JLINK_DLL_DIR, dll_name)
            if not os.path.isfile(dll_path):
                raise FileNotFoundError(f"J-Link DLL 不存在：{dll_path}")
            link = pylink.JLink(lib=pylink.library.Library(dllpath=dll_path))
            try:
                link.open()
                link.set_tif(getattr(pylink.enums.JLinkInterfaces, JLINK_INTERFACE))
                link.connect(DEVICE, speed=JLINK_SPEED)
            except Exception:
                try:
                    link.close()
                except Exception:
                    pass
                raise
            self.jlink = link

    def close(self) -> None:
        with self.lock:
            link, self.jlink = self.jlink, None
            if link is not None:
                link.close()

    def read(self, address: int, size: int) -> bytes:
        with self.lock:
            if self.jlink is None:
                raise RuntimeError("J-Link 未连接")
            return bytes(self.jlink.memory_read8(address, size))

    def write(self, address: int, data: bytes) -> None:
        with self.lock:
            if self.jlink is None:
                raise RuntimeError("J-Link 未连接")
            self.jlink.memory_write8(address, list(data))

    def reset(self) -> None:
        """Only the explicit GUI reset button calls this method."""
        with self.lock:
            if self.jlink is None:
                raise RuntimeError("J-Link 未连接")
            self.jlink.reset(halt=False)


@dataclass
class WatchSignal:
    expression: str
    address: int
    type_info: Dict[str, Any]
    color: str
    value: Any = "--"
    numeric: Optional[float] = None
    plotted: bool = False
    samples: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_POINTS))

    @property
    def size(self) -> int:
        return scalar_size(self.type_info)


@dataclass(frozen=True)
class ReadBlock:
    address: int
    size: int
    signals: Tuple[Tuple[str, int, int], ...]  # expression, offset, size


def build_read_plan(signals: Sequence[WatchSignal]) -> List[ReadBlock]:
    """Coalesce selected addresses into a small number of J-Link transactions."""
    ordered = sorted((s.address, s.address + s.size, s.expression) for s in signals if s.size > 0)
    if not ordered:
        return []
    groups: List[List[Tuple[int, int, str]]] = [[ordered[0]]]
    group_start, group_end = ordered[0][0], ordered[0][1]
    for start, end, expression in ordered[1:]:
        proposed_end = max(group_end, end)
        if start - group_end <= MAX_MERGE_GAP_BYTES and proposed_end - group_start <= MAX_READ_BLOCK_BYTES:
            groups[-1].append((start, end, expression))
            group_end = proposed_end
        else:
            groups.append([(start, end, expression)])
            group_start, group_end = start, end
    result = []
    for group in groups:
        start = min(item[0] for item in group)
        end = max(item[1] for item in group)
        result.append(ReadBlock(start, end - start, tuple((expr, addr - start, stop - addr) for addr, stop, expr in group)))
    return result


class AcquisitionEngine:
    def __init__(self, transport: JLinkTransport, events: queue.Queue) -> None:
        self.transport = transport
        self.events = events
        self._lock = threading.RLock()
        self._signals: Dict[str, WatchSignal] = {}
        self._watch_plan: List[ReadBlock] = []
        self._plot_plan: List[ReadBlock] = []
        self._target_hz = float(DEFAULT_PLOT_SAMPLE_HZ)
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mcu-acquisition", daemon=True)
        self._thread.start()

    def set_rate(self, hz: float) -> None:
        with self._lock:
            self._target_hz = max(1.0, min(float(MAX_SAMPLE_HZ), hz))

    def set_signals(self, signals: Dict[str, WatchSignal]) -> None:
        with self._lock:
            self._signals = dict(signals)
            self._watch_plan = build_read_plan(list(signals.values()))
            self._plot_plan = build_read_plan([signal for signal in signals.values() if signal.plotted])

    def start(self) -> None:
        self._enabled.set()

    def pause(self) -> None:
        self._enabled.clear()

    @property
    def running(self) -> bool:
        return self._enabled.is_set()

    def shutdown(self) -> None:
        self._enabled.clear()
        self._stop.set()
        self._thread.join(timeout=1.5)

    def _snapshot(self) -> Tuple[float, Dict[str, WatchSignal], List[ReadBlock], List[ReadBlock]]:
        with self._lock:
            return self._target_hz, self._signals, self._watch_plan, self._plot_plan

    def _read_plan(
        self,
        plan: Sequence[ReadBlock],
        signals: Dict[str, WatchSignal],
        capture_plot: bool,
    ) -> None:
        timestamp = time.perf_counter()
        for block in plan:
            raw = self.transport.read(block.address, block.size)
            for expression, offset, size in block.signals:
                signal = signals.get(expression)
                if signal is None:
                    continue
                display, numeric = decode_scalar(raw[offset:offset + size], signal.type_info)
                signal.value, signal.numeric = display, numeric
                if capture_plot and signal.plotted and numeric is not None and math.isfinite(numeric):
                    signal.samples.append((timestamp, numeric))

    def _run(self) -> None:
        next_watch = time.perf_counter()
        next_plot = next_watch
        rate_window_start = next_watch
        plot_cycles = 0
        while not self._stop.is_set():
            if not self._enabled.wait(0.1):
                next_watch = time.perf_counter()
                next_plot = next_watch
                rate_window_start = next_watch
                plot_cycles = 0
                continue
            hz, signals, watch_plan, plot_plan = self._snapshot()
            if not watch_plan or not self.transport.connected:
                time.sleep(0.02)
                continue
            now = time.perf_counter()
            watch_due = now >= next_watch
            plot_due = bool(plot_plan) and now >= next_plot
            if not watch_due and not plot_due:
                deadline = min(next_watch, next_plot if plot_plan else next_watch)
                remaining = deadline - now
                if remaining > 0.002:
                    time.sleep(remaining - 0.001)
                else:
                    time.sleep(0)
                continue
            try:
                # If both clocks expire together, one merged watch transaction serves
                # both consumers. Otherwise only the due plan is sent to J-Link.
                if watch_due and plot_due:
                    self._read_plan(watch_plan, signals, capture_plot=True)
                elif watch_due:
                    self._read_plan(watch_plan, signals, capture_plot=False)
                else:
                    self._read_plan(plot_plan, signals, capture_plot=True)
                finished = time.perf_counter()
                if watch_due:
                    next_watch = max(next_watch + 1.0 / WATCH_REFRESH_HZ, finished)
                if plot_due:
                    next_plot = max(next_plot + 1.0 / hz, finished)
                    plot_cycles += 1
                elapsed_window = finished - rate_window_start
                if elapsed_window >= 0.5:
                    actual = plot_cycles / elapsed_window if plot_plan else 0.0
                    self.events.put(("rate", actual, len(plot_plan)))
                    rate_window_start, plot_cycles = finished, 0
            except Exception as exc:
                self.events.put(("acquisition_error", str(exc)))
                self._enabled.clear()
                continue


class PlotCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(
            master, background=UI["plot"], highlightthickness=1,
            highlightbackground="#1d2a3d", relief="flat", **kwargs,
        )
        self.bind("<Configure>", lambda _event: self.after_idle(self.redraw))
        self.bind("<MouseWheel>", self._mouse_wheel)
        self.signals: Sequence[WatchSignal] = ()
        self.window_ms = DEFAULT_PLOT_WINDOW_MS
        self.line_width = DEFAULT_LINE_WIDTH
        self.on_display_change: Optional[Any] = None

    def set_signals(self, signals: Sequence[WatchSignal]) -> None:
        self.signals = signals

    def set_display(self, window_ms: int, line_width: float) -> None:
        self.window_ms = max(MIN_PLOT_WINDOW_MS, min(MAX_PLOT_WINDOW_MS, int(window_ms)))
        self.line_width = max(0.5, min(8.0, float(line_width)))
        self.redraw()

    def _mouse_wheel(self, event: tk.Event) -> str:
        direction = 1 if event.delta > 0 else -1
        if event.state & 0x0004:  # Ctrl + wheel: curve thickness
            self.line_width = max(0.5, min(8.0, self.line_width + direction * 0.5))
        else:  # Wheel up zooms in; wheel down shows a wider time range.
            factor = 0.8 if direction > 0 else 1.25
            self.window_ms = max(
                MIN_PLOT_WINDOW_MS,
                min(MAX_PLOT_WINDOW_MS, int(round(self.window_ms * factor))),
            )
        if self.on_display_change is not None:
            self.on_display_change(self.window_ms, self.line_width)
        self.redraw()
        return "break"

    @staticmethod
    def _envelope(points: Sequence[Tuple[float, float]], width: int, t0: float, t1: float) -> List[Tuple[float, float]]:
        if len(points) <= max(2, width * 2):
            return list(points)
        buckets: Dict[int, List[Tuple[float, float]]] = {}
        span = max(t1 - t0, 1e-9)
        for point in points:
            column = max(0, min(width - 1, int((point[0] - t0) / span * width)))
            bucket = buckets.setdefault(column, [])
            if not bucket:
                bucket.extend((point, point))
            else:
                if point[1] < bucket[0][1]:
                    bucket[0] = point
                if point[1] > bucket[1][1]:
                    bucket[1] = point
        output = []
        for column in sorted(buckets):
            low, high = buckets[column]
            output.extend((low, high) if low[0] <= high[0] else (high, low))
        return output

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
        left, right, top, bottom = 62, 16, 24, 34
        plot_w, plot_h = max(1, width - left - right), max(1, height - top - bottom)
        now = time.perf_counter()
        window_seconds = self.window_ms / 1000.0
        t0, t1 = now - window_seconds, now
        visible: List[Tuple[WatchSignal, List[Tuple[float, float]]]] = []
        all_values: List[float] = []
        for signal in self.signals[:MAX_PLOTTED_SIGNALS]:
            points = [point for point in tuple(signal.samples) if point[0] >= t0]
            if points:
                visible.append((signal, points))
                all_values.extend(point[1] for point in points)
        if all_values:
            ymin, ymax = min(all_values), max(all_values)
            if math.isclose(ymin, ymax, rel_tol=1e-9, abs_tol=1e-12):
                pad = max(abs(ymin) * 0.05, 1.0)
                ymin, ymax = ymin - pad, ymax + pad
            else:
                pad = (ymax - ymin) * 0.08
                ymin, ymax = ymin - pad, ymax + pad
        else:
            ymin, ymax = -1.0, 1.0

        for i in range(6):
            y = top + plot_h * i / 5
            value = ymax - (ymax - ymin) * i / 5
            self.create_line(left, y, left + plot_w, y, fill="#26303c")
            self.create_text(left - 7, y, text=f"{value:.5g}", fill="#8c9aaa", anchor="e", font=("Consolas", 9))
        for i in range(6):
            x = left + plot_w * i / 5
            self.create_line(x, top, x, top + plot_h, fill="#202a35")
            offset_ms = self.window_ms * (5 - i) / 5
            self.create_text(
                x, top + plot_h + 16, text=f"-{offset_ms:.0f} ms",
                fill="#8c9aaa", font=("Consolas", 9),
            )

        for signal, raw_points in visible:
            points = self._envelope(raw_points, plot_w, t0, t1)
            coordinates: List[float] = []
            for timestamp, value in points:
                x = left + (timestamp - t0) / (t1 - t0) * plot_w
                y = top + (ymax - value) / (ymax - ymin) * plot_h
                coordinates.extend((x, y))
            if len(coordinates) >= 4:
                self.create_line(*coordinates, fill=signal.color, width=self.line_width)

        legend_x = left + 8
        for signal in self.signals[:MAX_PLOTTED_SIGNALS]:
            label = f"{signal.expression} = {signal.value}"
            item = self.create_text(legend_x, 8, text=label, fill=signal.color, anchor="nw", font=("Consolas", 9))
            bbox = self.bbox(item)
            legend_x = (bbox[2] + 18) if bbox else legend_x + 100
            if legend_x > width - 180:
                break
        if not self.signals:
            self.create_text(
                width / 2, height / 2,
                text="在 Watch 变量上点击右键，选择“添加到 Plot”",
                fill="#708090", font=("Microsoft YaHei UI", 14),
            )


class PlotGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1360x820")
        self.minsize(980, 620)
        self.configure(background=UI["window"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.db = ElfDatabase()
        self.transport = JLinkTransport()
        self.events: queue.Queue = queue.Queue()
        self.engine = AcquisitionEngine(self.transport, self.events)
        self.signals: Dict[str, WatchSignal] = {}
        self.tree_nodes: Dict[str, Tuple[str, int, Dict[str, Any]]] = {}
        self.watch_groups: Dict[str, List[str]] = {}
        self.watch_group_records: Dict[str, Tuple[int, Dict[str, Any]]] = {}
        self.watch_item_signals: Dict[str, str] = {}
        self.watch_item_groups: Dict[str, List[str]] = {}
        self._color_index = 0
        self._closing = False
        self._elf_load_token = 0

        self.elf_var = tk.StringVar(value="未加载 ELF")
        self.filter_var = tk.StringVar()
        self.rate_var = tk.StringVar(value=str(DEFAULT_PLOT_SAMPLE_HZ))
        self.plot_window_var = tk.StringVar(value=str(DEFAULT_PLOT_WINDOW_MS))
        self.line_width_var = tk.StringVar(value=str(DEFAULT_LINE_WIDTH))
        self.connection_var = tk.StringVar(value="未连接")
        self.actual_rate_var = tk.StringVar(value="Plot 实际 0 Hz | 0 批次")
        self.status_var = tk.StringVar(value="请选择含 DWARF 调试信息的 ELF 文件")

        self._configure_style()
        self._build_ui()
        self.after_idle(self._enable_windows_backdrop)
        self.filter_var.trace_add("write", lambda *_args: self._schedule_filter())
        self.after(GUI_REFRESH_MS, self._ui_tick)
        self.after(100, self._auto_load_elf)
        self.after(250, self._auto_connect)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font = ("Microsoft YaHei UI", 9)
        style.configure("TFrame", background=UI["window"])
        style.configure("TLabel", background=UI["window"], foreground=UI["text"], font=font)
        style.configure("Toolbar.TFrame", background=UI["surface"])
        style.configure("Toolbar.TLabel", background=UI["surface"], foreground=UI["muted"], font=font)
        style.configure("Brand.TLabel", background=UI["surface"], foreground=UI["text"], font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Card.TFrame", background=UI["surface"])
        style.configure("Card.TLabel", background=UI["surface"], foreground=UI["text"], font=font)
        style.configure("Muted.Card.TLabel", background=UI["surface"], foreground=UI["muted"], font=("Microsoft YaHei UI", 8))
        style.configure(
            "Card.TLabelframe", background=UI["surface"], bordercolor=UI["border"],
            relief="solid", borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label", background=UI["surface"], foreground=UI["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "TButton", background=UI["surface_alt"], foreground=UI["text"],
            bordercolor=UI["border"], lightcolor=UI["surface_alt"], darkcolor=UI["surface_alt"],
            padding=(11, 7), font=font, relief="flat",
        )
        style.map("TButton", background=[("active", UI["accent_soft"]), ("pressed", "#d8e9ff")])
        style.configure(
            "Accent.TButton", background=UI["accent"], foreground="#ffffff",
            bordercolor=UI["accent"], lightcolor=UI["accent"], darkcolor=UI["accent"],
        )
        style.map(
            "Accent.TButton",
            background=[("active", UI["accent_hover"]), ("pressed", "#0957c4"), ("disabled", "#9fc7f7")],
            foreground=[("disabled", "#eef6ff")],
        )
        style.configure(
            "Warning.TButton", background="#fff6df", foreground="#9a5b00",
            bordercolor="#f4d58b", lightcolor="#fff6df", darkcolor="#fff6df",
        )
        style.map("Warning.TButton", background=[("active", "#ffedbd"), ("pressed", "#ffe39a")])
        style.configure(
            "Treeview", rowheight=27, font=font, background=UI["surface"],
            fieldbackground=UI["surface"], foreground=UI["text"], borderwidth=0,
        )
        style.map("Treeview", background=[("selected", UI["accent_soft"])], foreground=[("selected", UI["accent_hover"])])
        style.configure(
            "Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"),
            background=UI["surface_alt"], foreground=UI["muted"], relief="flat", padding=(6, 7),
        )
        style.map("Treeview.Heading", background=[("active", UI["accent_soft"])])
        style.configure("TEntry", fieldbackground=UI["surface"], bordercolor=UI["border"], padding=6)
        style.configure("TSpinbox", fieldbackground=UI["surface"], bordercolor=UI["border"], padding=5)
        style.configure(
            "Vertical.TScrollbar", background="#c7d0dc", troughcolor=UI["surface_alt"],
            bordercolor=UI["surface_alt"], arrowcolor=UI["muted"], width=11,
        )
        style.configure("TPanedwindow", background=UI["window"])
        style.configure("Status.TLabel", foreground=UI["muted"], background=UI["window"], font=("Microsoft YaHei UI", 8))

    def _enable_windows_backdrop(self) -> None:
        """Best-effort Windows 11 Mica and rounded corners; silently falls back."""
        if platform.system() != "Windows":
            return
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(2)  # DWMSBT_MAINWINDOW
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
            corners = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))
        except Exception:
            pass

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(14, 10), style="Toolbar.TFrame")
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="MCU Scope", style="Brand.TLabel").pack(side="left", padx=(0, 14))
        ttk.Button(toolbar, text="打开 ELF", command=self._choose_elf).pack(side="left")
        ttk.Button(toolbar, text="重新加载", command=self._reload_elf).pack(side="left", padx=(6, 12))
        ttk.Label(toolbar, textvariable=self.elf_var, width=42, style="Toolbar.TLabel").pack(side="left", fill="x", expand=True)
        self.connect_button = ttk.Button(toolbar, text="连接 J-Link", command=self._toggle_connection, style="Accent.TButton")
        self.connect_button.pack(side="left", padx=5)
        self.flash_button = ttk.Button(toolbar, text="烧录 ELF", command=self._flash_elf, style="Warning.TButton")
        self.flash_button.pack(side="left", padx=5)
        self.reset_button = ttk.Button(toolbar, text="复位 MCU", command=self._reset_mcu)
        self.reset_button.pack(side="left", padx=5)
        self.start_button = ttk.Button(toolbar, text="开始采集", command=self._toggle_acquisition, style="Accent.TButton")
        self.start_button.pack(side="left", padx=5)
        ttk.Label(toolbar, text="绘图 Hz", style="Toolbar.TLabel").pack(side="left", padx=(14, 4))
        rate = ttk.Spinbox(toolbar, from_=1, to=MAX_SAMPLE_HZ, width=7, textvariable=self.rate_var, command=self._apply_rate)
        rate.pack(side="left")
        rate.bind("<Return>", self._apply_rate)
        rate.bind("<FocusOut>", self._apply_rate)

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12, pady=(10, 6))
        left = ttk.Frame(pane, width=425)
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=3)

        search = ttk.Frame(left, style="Card.TFrame", padding=(10, 8))
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text="搜索", style="Card.TLabel").pack(side="left")
        ttk.Entry(search, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(8, 0))

        left_pane = ttk.Panedwindow(left, orient="vertical")
        left_pane.pack(fill="both", expand=True)
        source_frame = ttk.LabelFrame(left_pane, text="ELF 变量（双击标量添加到 Watch）", padding=7, style="Card.TLabelframe")
        watches = ttk.LabelFrame(
            left_pane,
            text=f"Watch（固定 {WATCH_REFRESH_HZ} Hz；双击/Enter 改值，Del 删除，右键 Plot）",
            padding=7,
            style="Card.TLabelframe",
        )
        left_pane.add(source_frame, weight=3)
        left_pane.add(watches, weight=2)

        tree_frame = ttk.Frame(source_frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("type",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="变量 / 成员")
        self.tree.heading("type", text="类型")
        self.tree.column("#0", width=250, minwidth=150)
        self.tree.column("type", width=125, minwidth=70)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._tree_click, add=True)
        self.tree.bind("<Double-1>", self._tree_double_click)

        self.watch_table = ttk.Treeview(
            watches, columns=("value", "type", "plot"), show="tree headings", selectmode="browse"
        )
        self.watch_table.heading("#0", text="变量 / 结构体成员")
        self.watch_table.heading("value", text="当前值")
        self.watch_table.heading("type", text="类型")
        self.watch_table.heading("plot", text="Plot")
        self.watch_table.column("#0", width=190, minwidth=100)
        self.watch_table.column("value", width=105, minwidth=75)
        self.watch_table.column("type", width=100, minwidth=65)
        self.watch_table.column("plot", width=45, minwidth=40, anchor="center", stretch=False)
        watch_scroll = ttk.Scrollbar(watches, orient="vertical", command=self.watch_table.yview)
        self.watch_table.configure(yscrollcommand=watch_scroll.set)
        self.watch_table.pack(side="left", fill="both", expand=True)
        watch_scroll.pack(side="right", fill="y")
        self.watch_table.bind("<Double-1>", self._begin_value_edit)
        self.watch_table.bind("<Return>", self._begin_value_edit)
        self.watch_table.bind("<Delete>", self._delete_watch)
        self.watch_table.bind("<Button-3>", self._show_watch_menu)
        self.watch_menu = tk.Menu(self, tearoff=False)
        self.watch_menu.add_command(label="添加到 Plot", command=lambda: self._set_selected_plot(True))
        self.watch_menu.add_command(label="从 Plot 移除", command=lambda: self._set_selected_plot(False))
        self.watch_menu.add_separator()
        self.watch_menu.add_command(label="删除 Watch 变量", command=self._delete_watch)

        plot_controls = ttk.Frame(right, padding=(12, 8), style="Card.TFrame")
        plot_controls.pack(fill="x")
        ttk.Label(plot_controls, text="显示窗口", style="Card.TLabel").pack(side="left")
        window_box = ttk.Spinbox(
            plot_controls,
            from_=MIN_PLOT_WINDOW_MS,
            to=MAX_PLOT_WINDOW_MS,
            increment=10,
            width=8,
            textvariable=self.plot_window_var,
            command=self._apply_plot_display,
        )
        window_box.pack(side="left", padx=(4, 2))
        window_box.bind("<Return>", self._apply_plot_display)
        window_box.bind("<FocusOut>", self._apply_plot_display)
        ttk.Label(plot_controls, text="ms     曲线线宽", style="Card.TLabel").pack(side="left")
        width_box = ttk.Spinbox(
            plot_controls,
            from_=0.5,
            to=8.0,
            increment=0.5,
            width=5,
            textvariable=self.line_width_var,
            command=self._apply_plot_display,
        )
        width_box.pack(side="left", padx=(4, 8))
        width_box.bind("<Return>", self._apply_plot_display)
        width_box.bind("<FocusOut>", self._apply_plot_display)
        ttk.Label(
            plot_controls, text=f"范围 {MIN_PLOT_WINDOW_MS}–{MAX_PLOT_WINDOW_MS} ms  ·  滚轮缩放",
            style="Muted.Card.TLabel",
        ).pack(side="left")
        ttk.Label(plot_controls, text="REAL-TIME PLOT", style="Muted.Card.TLabel").pack(side="right", padx=(12, 2))

        self.plot = PlotCanvas(right)
        self.plot.on_display_change = self._plot_mouse_changed
        self.plot.pack(fill="both", expand=True)

        status = ttk.Frame(self, padding=(8, 2, 8, 7))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(status, textvariable=self.actual_rate_var, style="Status.TLabel").pack(side="right", padx=(14, 4))
        ttk.Label(status, textvariable=self.connection_var, style="Status.TLabel").pack(side="right", padx=(14, 0))
        ttk.Label(status, text="批量合并读取  ·  双线程  ·  ms 时间轴", style="Status.TLabel").pack(side="right")

    def _auto_load_elf(self) -> None:
        candidates = []
        for root, dirs, files in os.walk(os.getcwd()):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                if name.lower().endswith(".elf"):
                    path = os.path.join(root, name)
                    candidates.append((os.path.getmtime(path), path))
        if candidates:
            self._load_elf(max(candidates)[1])

    def _choose_elf(self) -> None:
        path = filedialog.askopenfilename(title="选择 ELF", filetypes=(("ELF firmware", "*.elf"), ("All files", "*.*")))
        if path:
            self._load_elf(path)

    def _reload_elf(self) -> None:
        if self.db.path:
            self._load_elf(self.db.path)
        else:
            self._choose_elf()

    def _load_elf(self, path: str) -> None:
        self.engine.pause()
        self.start_button.configure(text="开始采集")
        self._elf_load_token += 1
        token = self._elf_load_token
        self.status_var.set("正在解析 ELF 符号与 DWARF 类型…")
        self.elf_var.set(os.path.basename(path))

        def worker() -> None:
            try:
                database = ElfDatabase()
                database.load(path)
                self.events.put(("elf_loaded", token, database))
            except Exception as exc:
                self.events.put(("elf_error", token, str(exc)))

        threading.Thread(target=worker, name="elf-loader", daemon=True).start()

    def _install_database(self, database: ElfDatabase) -> None:
        self.db = database
        self.signals.clear()
        self.watch_groups.clear()
        self.watch_group_records.clear()
        self.engine.set_signals(self.signals)
        self._rebuild_tree()
        self._rebuild_watch_tree()
        self.elf_var.set(f"{os.path.basename(database.path)}  ({len(database.symbols)} symbols / {len(database.types)} typed)")
        self.status_var.set("ELF 已加载。展开结构体/数组，点击标量前的方框加入监视。")

    def _schedule_filter(self) -> None:
        if hasattr(self, "_filter_after"):
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(180, self._rebuild_tree)

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.tree_nodes.clear()
        keyword = self.filter_var.get().strip().lower()
        names = sorted(self.db.types, key=str.lower)
        if keyword:
            names = [name for name in names if keyword in name.lower()]
        for name in names[:5000]:
            address = self.db.symbols[name]
            info = self.db.types[name]
            item = self.tree.insert("", "end", text=self._node_text(name, info, name), values=(type_label(info),))
            self.tree_nodes[item] = (name, address, info)
            self._insert_children(item, name, address, info, 0, set())
        self._update_tree_checks()

    def _insert_children(self, parent: str, expression: str, address: int, info: Dict[str, Any], depth: int, visited: set) -> None:
        if depth >= MAX_TYPE_DEPTH or id(info) in visited:
            return
        visited = set(visited)
        visited.add(id(info))
        kind = info.get("kind")
        if kind == "array":
            element = info.get("element", {})
            element_size = int(element.get("size", 0) or 0)
            count = min(int(info.get("count", 0) or 0), int(MAX_ARRAY_ELEMENTS))
            for index in range(count):
                child_expr = f"{expression}[{index}]"
                child_addr = address + index * element_size
                item = self.tree.insert(parent, "end", text=self._node_text(f"[{index}]", element, child_expr), values=(type_label(element),))
                self.tree_nodes[item] = (child_expr, child_addr, element)
                self._insert_children(item, child_expr, child_addr, element, depth + 1, visited)
            if int(info.get("count", 0) or 0) > count:
                self.tree.insert(parent, "end", text=f"… 省略 {int(info['count']) - count} 项", values=("",))
        elif kind in ("struct", "union"):
            for member in info.get("members", []):
                child_expr = f"{expression}.{member['name']}"
                child_addr = address + int(member["offset"])
                child_info = member["type"]
                item = self.tree.insert(parent, "end", text=self._node_text(member["name"], child_info, child_expr), values=(type_label(child_info),))
                self.tree_nodes[item] = (child_expr, child_addr, child_info)
                self._insert_children(item, child_expr, child_addr, child_info, depth + 1, visited)

    def _node_text(self, label: str, info: Dict[str, Any], expression: str) -> str:
        if scalar_size(info) or self._type_has_watchable_leaf(info):
            return f"{'☑' if expression in self.signals else '☐'} {label}"
        return label

    def _type_has_watchable_leaf(self, info: Dict[str, Any], depth: int = 0, visited: Optional[set] = None) -> bool:
        if scalar_size(info):
            return True
        if depth >= MAX_TYPE_DEPTH:
            return False
        visited = set() if visited is None else set(visited)
        if id(info) in visited:
            return False
        visited.add(id(info))
        if info.get("kind") == "array":
            return self._type_has_watchable_leaf(info.get("element", {}), depth + 1, visited)
        if info.get("kind") in ("struct", "union"):
            return any(
                self._type_has_watchable_leaf(member.get("type", {}), depth + 1, visited)
                for member in info.get("members", [])
            )
        return False

    def _tree_leaf_records(self, item: str) -> List[Tuple[str, int, Dict[str, Any]]]:
        record = self.tree_nodes.get(item)
        if record is not None and scalar_size(record[2]):
            return [record]
        leaves: List[Tuple[str, int, Dict[str, Any]]] = []
        for child in self.tree.get_children(item):
            leaves.extend(self._tree_leaf_records(child))
        # Unions or anonymous DWARF entries can occasionally expose duplicate paths.
        return list({record[0]: record for record in leaves}.values())

    def _tree_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        region = self.tree.identify_region(event.x, event.y)
        if not item or region not in ("tree", "cell") or item not in self.tree_nodes:
            return
        if self.tree.identify_element(event.x, event.y) == "Treeitem.indicator":
            return
        bbox = self.tree.bbox(item, "#0")
        depth = 0
        parent = self.tree.parent(item)
        while parent:
            depth += 1
            parent = self.tree.parent(parent)
        checkbox_right = bbox[0] + 40 + depth * 20 if bbox else 0
        if bbox and event.x <= checkbox_right and self._tree_leaf_records(item):
            self.after_idle(lambda: self._toggle_tree_node(item))

    def _toggle_tree_node(self, item: str) -> None:
        leaves = self._tree_leaf_records(item)
        if not leaves:
            return
        root_record = self.tree_nodes[item]
        root_expression, root_address, root_info = root_record
        expressions = {record[0] for record in leaves}
        if expressions.issubset(self.signals):
            for expression in expressions:
                del self.signals[expression]
            self.watch_groups.pop(root_expression, None)
            self.watch_group_records.pop(root_expression, None)
            for group_name in list(self.watch_groups):
                remaining = [expr for expr in self.watch_groups[group_name] if expr not in expressions]
                if remaining:
                    self.watch_groups[group_name] = remaining
                else:
                    self.watch_groups.pop(group_name, None)
                    self.watch_group_records.pop(group_name, None)
            action = "移除"
        else:
            missing = [record for record in leaves if record[0] not in self.signals]
            available = MAX_WATCH_SIGNALS - len(self.signals)
            if len(missing) > available:
                messagebox.showwarning(
                    "Watch 数量限制",
                    f"该节点包含 {len(leaves)} 个标量，当前只能再添加 {available} 个。\n"
                    f"请先删除部分 Watch 变量或选择具体成员。",
                )
                return
            for expression, address, info in missing:
                color = COLORS[self._color_index % len(COLORS)]
                self._color_index += 1
                self.signals[expression] = WatchSignal(expression, address, info, color)
            # One aggregate Watch row owns overlapping child rows, preventing the
            # same member from appearing both flat and under several structures.
            for group_name, members in list(self.watch_groups.items()):
                if expressions.intersection(members):
                    self.watch_groups.pop(group_name, None)
                    self.watch_group_records.pop(group_name, None)
            if not scalar_size(root_info):
                self.watch_groups[root_expression] = [record[0] for record in leaves]
                self.watch_group_records[root_expression] = (root_address, root_info)
            action = "添加"
        self.engine.set_signals(self.signals)
        if self.transport.connected and self.signals:
            self.engine.start()
            self.start_button.configure(text="暂停采集")
        self._update_tree_checks()
        self._rebuild_watch_tree()
        self.status_var.set(f"已{action} {len(leaves)} 个结构体/数组标量；Watch 固定 {WATCH_REFRESH_HZ} Hz")

    def _tree_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if not item or item not in self.tree_nodes:
            return
        expression, address, info = self.tree_nodes[item]
        if scalar_size(info) and expression not in self.signals:
            self.after_idle(lambda: self._toggle_signal(expression, address, info))

    def _toggle_signal(self, expression: str, address: int, info: Dict[str, Any]) -> None:
        if expression in self.signals:
            del self.signals[expression]
            for group_name in list(self.watch_groups):
                members = [member for member in self.watch_groups[group_name] if member != expression]
                if members:
                    self.watch_groups[group_name] = members
                else:
                    self.watch_groups.pop(group_name, None)
                    self.watch_group_records.pop(group_name, None)
        else:
            if len(self.signals) >= MAX_WATCH_SIGNALS:
                messagebox.showwarning("监视数量限制", f"最多同时监视 {MAX_WATCH_SIGNALS} 个变量。")
                return
            color = COLORS[self._color_index % len(COLORS)]
            self._color_index += 1
            self.signals[expression] = WatchSignal(expression, address, info, color)
        self.engine.set_signals(self.signals)
        if self.transport.connected and self.signals:
            self.engine.start()
            self.start_button.configure(text="暂停采集")
        self._update_tree_checks()
        self._rebuild_watch_tree()
        plan = build_read_plan(list(self.signals.values()))
        self.status_var.set(f"Watch 已选择 {len(self.signals)} 项，{WATCH_REFRESH_HZ} Hz，合并为 {len(plan)} 个读取批次")

    def _update_tree_checks(self) -> None:
        def update_branch(item: str) -> List[str]:
            record = self.tree_nodes.get(item)
            if record is not None and scalar_size(record[2]):
                expressions = [record[0]]
            else:
                expressions = []
                for child in self.tree.get_children(item):
                    expressions.extend(update_branch(child))
            if record is None:
                return expressions
            current = self.tree.item(item, "text")
            if expressions:
                selected = sum(expression in self.signals for expression in expressions)
                marker = "☑" if selected == len(expressions) else ("◩" if selected else "☐")
                label = re.sub(r"^[☐☑◩]\s*", "", current)
                self.tree.item(item, text=f"{marker} {label}")
            return expressions

        for root in self.tree.get_children(""):
            update_branch(root)

    def _rebuild_watch_tree(self) -> None:
        self.watch_table.delete(*self.watch_table.get_children())
        self.watch_item_signals.clear()
        self.watch_item_groups.clear()
        counter = 0

        def new_id() -> str:
            nonlocal counter
            counter += 1
            return f"watch_{counter}"

        def insert_node(
            parent: str,
            label: str,
            expression: str,
            address: int,
            info: Dict[str, Any],
            allowed: set,
            open_node: bool = False,
        ) -> Optional[str]:
            if scalar_size(info):
                if expression not in allowed or expression not in self.signals:
                    return None
                signal = self.signals[expression]
                iid = new_id()
                self.watch_table.insert(
                    parent, "end", iid=iid, text=label,
                    values=(str(signal.value), type_label(info), "●" if signal.plotted else ""),
                    open=False,
                )
                self.watch_item_signals[iid] = expression
                return iid

            iid = new_id()
            self.watch_table.insert(
                parent, "end", iid=iid, text=label,
                values=("", type_label(info), ""), open=open_node,
            )
            child_ids: List[str] = []
            if info.get("kind") == "array":
                element = info.get("element", {})
                element_size = int(element.get("size", 0) or 0)
                count = min(int(info.get("count", 0) or 0), int(MAX_ARRAY_ELEMENTS))
                for index in range(count):
                    child_expr = f"{expression}[{index}]"
                    child = insert_node(
                        iid, f"[{index}]", child_expr, address + index * element_size,
                        element, allowed,
                    )
                    if child:
                        child_ids.append(child)
            elif info.get("kind") in ("struct", "union"):
                for member in info.get("members", []):
                    child_expr = f"{expression}.{member['name']}"
                    child = insert_node(
                        iid, member["name"], child_expr, address + int(member["offset"]),
                        member["type"], allowed,
                    )
                    if child:
                        child_ids.append(child)
            if not child_ids:
                self.watch_table.delete(iid)
                return None
            members = [expr for expr in allowed if expr in self.signals and (expr == expression or expr.startswith(expression + ".") or expr.startswith(expression + "["))]
            self.watch_item_groups[iid] = members
            return iid

        grouped_members: set = set()
        for root_expression, members in self.watch_groups.items():
            record = self.watch_group_records.get(root_expression)
            if record is None:
                continue
            allowed = {member for member in members if member in self.signals}
            if not allowed:
                continue
            grouped_members.update(allowed)
            address, info = record
            insert_node("", root_expression, root_expression, address, info, allowed, open_node=True)

        for expression, signal in self.signals.items():
            if expression not in grouped_members:
                insert_node("", expression, expression, signal.address, signal.type_info, {expression})
        self._refresh_watch_table()

    def _refresh_watch_table(self) -> None:
        for item, expression in list(self.watch_item_signals.items()):
            signal = self.signals.get(expression)
            if signal is None or not self.watch_table.exists(item):
                continue
            self.watch_table.item(
                item,
                values=(str(signal.value), type_label(signal.type_info), "●" if signal.plotted else ""),
                tags=(item,),
            )
            self.watch_table.tag_configure(item, foreground=signal.color if signal.plotted else "#202020")
        for item, members in list(self.watch_item_groups.items()):
            active = [self.signals[expr] for expr in members if expr in self.signals]
            if not active or not self.watch_table.exists(item):
                continue
            plotted = sum(signal.plotted for signal in active)
            marker = "●" if plotted == len(active) else ("◐" if plotted else "")
            current = list(self.watch_table.item(item, "values"))
            if len(current) >= 3:
                current[0] = f"{{{len(active)} members}}"
                current[2] = marker
                self.watch_table.item(item, values=current)
        self.plot.set_signals([signal for signal in self.signals.values() if signal.plotted])

    def _remove_selected(self) -> None:
        selected = self.watch_table.selection()
        if not selected:
            return
        item = selected[0]
        expression = self.watch_item_signals.get(item)
        if expression and expression in self.signals:
            signal = self.signals[expression]
            self._toggle_signal(signal.expression, signal.address, signal.type_info)
            return
        members = self.watch_item_groups.get(item, [])
        if members:
            for member in members:
                self.signals.pop(member, None)
            removed = set(members)
            for group_name in list(self.watch_groups):
                remaining = [member for member in self.watch_groups[group_name] if member not in removed]
                if remaining:
                    self.watch_groups[group_name] = remaining
                else:
                    self.watch_groups.pop(group_name, None)
                    self.watch_group_records.pop(group_name, None)
            self.engine.set_signals(self.signals)
            self._update_tree_checks()
            self._rebuild_watch_tree()

    def _delete_watch(self, _event: Any = None) -> str:
        self._remove_selected()
        return "break"

    def _show_watch_menu(self, event: tk.Event) -> None:
        item = self.watch_table.identify_row(event.y)
        if not item:
            return
        self.watch_table.selection_set(item)
        self.watch_table.focus(item)
        self.watch_menu.tk_popup(event.x_root, event.y_root)

    def _set_selected_plot(self, enabled: bool) -> None:
        selected = self.watch_table.selection()
        if not selected:
            return
        item = selected[0]
        expressions = [self.watch_item_signals[item]] if item in self.watch_item_signals else self.watch_item_groups.get(item, [])
        targets = [self.signals[expr] for expr in expressions if expr in self.signals]
        if not targets:
            return
        if enabled:
            new_count = sum(not signal.plotted for signal in targets)
            plotted_count = sum(signal.plotted for signal in self.signals.values())
            if plotted_count + new_count > MAX_PLOTTED_SIGNALS:
                messagebox.showwarning("Plot 数量限制", f"最多同时绘制 {MAX_PLOTTED_SIGNALS} 条曲线。")
                return
        for signal in targets:
            signal.plotted = enabled
            signal.samples.clear()
        self.engine.set_signals(self.signals)
        if self.transport.connected:
            self.engine.start()
            self.start_button.configure(text="暂停采集")
        self._refresh_watch_table()
        action = "加入" if enabled else "移出"
        self.status_var.set(f"{len(targets)} 个变量已{action} Plot；绘图采样率 {self.rate_var.get()} Hz")

    def _encode_write(self, text: str, info: Dict[str, Any]) -> bytes:
        size = scalar_size(info)
        encoding = int(info.get("encoding", 0) or 0)
        if info.get("kind") == "enum":
            reverse = {name: value for value, name in info.get("values", {}).items()}
            number = reverse[text] if text in reverse else int(text, 0)
            return int(number).to_bytes(size, "little", signed=number < 0)
        if encoding == 0x04 and size in (4, 8):
            return struct.pack("<f" if size == 4 else "<d", float(text))
        number = int(text, 0)
        return number.to_bytes(size, "little", signed=encoding in (0x05, 0x06))

    def _begin_value_edit(self, event: Any = None) -> str:
        if event is not None and hasattr(event, "y"):
            item = self.watch_table.identify_row(event.y)
            if item:
                self.watch_table.selection_set(item)
                self.watch_table.focus(item)
        selected = self.watch_table.selection()
        if not selected or selected[0] not in self.watch_item_signals:
            return "break"
        item = selected[0]
        expression = self.watch_item_signals[item]
        bbox = self.watch_table.bbox(item, "value")
        if not bbox:
            return "break"
        if hasattr(self, "_value_editor") and self._value_editor.winfo_exists():
            self._value_editor.destroy()
        x, y, width, height = bbox
        editor = ttk.Entry(self.watch_table)
        editor.place(x=x, y=y, width=width, height=height)
        signal = self.signals[expression]
        current = signal.numeric if signal.numeric is not None else signal.value
        editor.insert(0, str(current))
        editor.selection_range(0, "end")
        editor.focus_set()
        editor.bind("<Return>", lambda _event: self._commit_value_edit(expression, editor))
        editor.bind("<Escape>", lambda _event: editor.destroy())
        self._value_editor = editor
        return "break"

    def _commit_value_edit(self, expression: str, editor: ttk.Entry) -> str:
        text = editor.get().strip()
        signal = self.signals.get(expression)
        if signal is None:
            editor.destroy()
            return "break"
        try:
            data = self._encode_write(text, signal.type_info)
            self.transport.write(signal.address, data)
            signal.value = text
            self.status_var.set(f"写入成功：{signal.expression} = {text}")
            editor.destroy()
        except Exception as exc:
            messagebox.showerror("写入失败", str(exc))
            editor.focus_set()
            editor.selection_range(0, "end")
        return "break"

    def _apply_plot_display(self, _event: Any = None) -> None:
        try:
            window_ms = int(float(self.plot_window_var.get()))
            line_width = float(self.line_width_var.get())
            if not MIN_PLOT_WINDOW_MS <= window_ms <= MAX_PLOT_WINDOW_MS:
                raise ValueError(f"显示窗口必须是 {MIN_PLOT_WINDOW_MS}–{MAX_PLOT_WINDOW_MS} ms")
            if not 0.5 <= line_width <= 8.0:
                raise ValueError("曲线线宽必须是 0.5–8.0")
        except ValueError as exc:
            messagebox.showwarning("绘图参数", str(exc) or "请输入有效的绘图参数")
            self.plot_window_var.set(str(DEFAULT_PLOT_WINDOW_MS))
            self.line_width_var.set(str(DEFAULT_LINE_WIDTH))
            window_ms = DEFAULT_PLOT_WINDOW_MS
            line_width = DEFAULT_LINE_WIDTH
        self.plot_window_var.set(str(window_ms))
        self.line_width_var.set(f"{line_width:g}")
        self.plot.set_display(window_ms, line_width)
        self.status_var.set(f"绘图窗口 {window_ms} ms，曲线线宽 {line_width:g}")

    def _plot_mouse_changed(self, window_ms: int, line_width: float) -> None:
        self.plot_window_var.set(str(window_ms))
        self.line_width_var.set(f"{line_width:g}")
        self.status_var.set(
            f"滚轮缩放：绘图窗口 {window_ms} ms；Ctrl+滚轮：曲线线宽 {line_width:g}"
        )

    def _apply_rate(self, _event: Any = None) -> None:
        try:
            hz = int(float(self.rate_var.get()))
            if not 1 <= hz <= MAX_SAMPLE_HZ:
                raise ValueError
        except ValueError:
            messagebox.showwarning("绘图采样率", f"请输入 1 到 {MAX_SAMPLE_HZ} Hz。")
            self.rate_var.set(str(DEFAULT_PLOT_SAMPLE_HZ))
            hz = DEFAULT_PLOT_SAMPLE_HZ
        self.rate_var.set(str(hz))
        self.engine.set_rate(hz)
        self.status_var.set(f"Plot 采样率已设置为 {hz} Hz；Watch 仍固定 {WATCH_REFRESH_HZ} Hz")

    def _toggle_connection(self) -> None:
        if self.transport.connected:
            self.engine.pause()
            self.start_button.configure(text="开始采集")
            try:
                self.transport.close()
            except Exception as exc:
                messagebox.showerror("断开失败", str(exc))
            self.connection_var.set("未连接")
            self.connect_button.configure(text="连接 J-Link", state="normal")
            return
        self.connect_button.configure(state="disabled")
        self.connection_var.set("连接中…")

        def worker() -> None:
            try:
                self.transport.connect()
                if self._closing:
                    self.transport.close()
                else:
                    self.events.put(("connected",))
            except Exception as exc:
                if not self._closing:
                    self.events.put(("connect_error", str(exc)))

        threading.Thread(target=worker, name="jlink-connect", daemon=True).start()

    def _auto_connect(self) -> None:
        """Connect at startup without resetting or halting the target."""
        if not self.transport.connected and str(self.connect_button.cget("state")) != "disabled":
            self.status_var.set("正在自动连接 J-Link（不会复位 MCU）…")
            self._toggle_connection()

    def _reset_mcu(self) -> None:
        if not self.transport.connected:
            messagebox.showinfo("复位 MCU", "请先连接 J-Link。")
            return
        self.engine.pause()
        self.start_button.configure(text="开始采集")
        self.reset_button.configure(state="disabled")
        self.status_var.set("正在复位 MCU…")

        def worker() -> None:
            try:
                self.transport.reset()
                self.events.put(("reset_complete",))
            except Exception as exc:
                self.events.put(("reset_error", str(exc)))

        threading.Thread(target=worker, name="mcu-reset", daemon=True).start()

    def _flash_elf(self) -> None:
        if str(self.connect_button.cget("state")) == "disabled":
            messagebox.showinfo("烧录 ELF", "J-Link 正在执行连接操作，请稍候再烧录。")
            return
        if not self.db.path or not os.path.isfile(self.db.path):
            messagebox.showinfo("烧录 ELF", "请先加载有效的 ELF 文件。")
            return
        if not os.path.isfile(JLINK_EXE):
            messagebox.showerror("烧录 ELF", f"找不到 J-Link Commander：\n{JLINK_EXE}")
            return

        self.engine.pause()
        self.start_button.configure(text="开始采集")
        self.flash_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.reset_button.configure(state="disabled")
        self.connection_var.set("烧录中…")
        self.status_var.set("正在按 Debug.py 流程烧录：复位 → 下载 → 复位…")
        elf_path = self.db.path

        def worker() -> None:
            command_file: Optional[str] = None
            try:
                # Reparse the ELF because it may have been rebuilt after the
                # file was first opened. Watch addresses must match the image
                # that is about to be programmed.
                flashed_database = ElfDatabase()
                flashed_database.load(elf_path)

                # Use the same proven Commander sequence as Debug.py.  These reset
                # commands exist only inside an explicit user-requested flash action.
                self.transport.close()
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".jlink", encoding="utf-8", newline="\n", delete=False
                ) as script:
                    command_file = script.name
                    script.write("r\n")
                    script.write(f'loadfile "{elf_path}"\n')
                    script.write("r\n")
                    script.write("qc\n")
                result = subprocess.run(
                    [
                        JLINK_EXE,
                        "-device", DEVICE,
                        "-if", JLINK_INTERFACE,
                        "-speed", str(JLINK_SPEED),
                        "-autoconnect", str(JLINK_AUTOCONNECT),
                        "-ExitOnError", "1",
                        "-CommanderScript", command_file,
                    ],
                    cwd=os.path.dirname(elf_path),
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=FLASH_TIMEOUT_SECONDS,
                )
                output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
                if result.returncode != 0:
                    raise RuntimeError(f"J-Link Commander 返回代码 {result.returncode}\n{output[-2000:]}")
                if self._closing:
                    return
                self.transport.connect()
                self.events.put(("flash_complete", flashed_database))
            except Exception as exc:
                reconnect_error = ""
                if not self._closing and not self.transport.connected:
                    try:
                        self.transport.connect()
                    except Exception as reconnect_exc:
                        reconnect_error = f"\n重新连接失败：{reconnect_exc}"
                if not self._closing:
                    self.events.put(("flash_error", f"{exc}{reconnect_error}"))
            finally:
                if command_file:
                    try:
                        os.unlink(command_file)
                    except OSError:
                        pass

        threading.Thread(target=worker, name="elf-flash", daemon=True).start()

    def _toggle_acquisition(self) -> None:
        if self.engine.running:
            self.engine.pause()
            self.start_button.configure(text="开始采集")
            self.status_var.set("采集已暂停")
            return
        if not self.transport.connected:
            messagebox.showinfo("开始采集", "请先连接 J-Link。")
            return
        if not self.signals:
            messagebox.showinfo("开始采集", "请先从左侧勾选至少一个标量。")
            return
        self._apply_rate()
        self.engine.start()
        self.start_button.configure(text="暂停采集")
        self.status_var.set("正在批量采集…")

    def _ui_tick(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "elf_loaded" and event[1] == self._elf_load_token:
                    self._install_database(event[2])
                elif kind == "elf_error" and event[1] == self._elf_load_token:
                    self.status_var.set("ELF 加载失败")
                    messagebox.showerror("ELF 加载失败", event[2])
                elif kind == "connected":
                    self.connection_var.set("已连接")
                    self.connect_button.configure(text="断开 J-Link", state="normal")
                    if self.signals:
                        self.engine.start()
                        self.start_button.configure(text="暂停采集")
                    self.status_var.set(
                        f"已连接 {DEVICE} / {JLINK_INTERFACE} / {JLINK_SPEED} kHz；Watch {WATCH_REFRESH_HZ} Hz"
                    )
                elif kind == "connect_error":
                    self.connection_var.set("连接失败")
                    self.connect_button.configure(text="连接 J-Link", state="normal")
                    messagebox.showerror("J-Link 连接失败", event[1])
                elif kind == "reset_complete":
                    self.reset_button.configure(state="normal")
                    if self.signals:
                        self.engine.start()
                        self.start_button.configure(text="暂停采集")
                    self.status_var.set("MCU 已复位并继续运行")
                elif kind == "reset_error":
                    self.reset_button.configure(state="normal")
                    self.status_var.set("MCU 复位失败")
                    messagebox.showerror("复位失败", event[1])
                elif kind == "flash_complete":
                    self._install_database(event[1])
                    self.flash_button.configure(state="normal")
                    self.connect_button.configure(text="断开 J-Link", state="normal")
                    self.reset_button.configure(state="normal")
                    self.connection_var.set("已连接")
                    if self.signals:
                        self.engine.start()
                        self.start_button.configure(text="暂停采集")
                    self.status_var.set("ELF 烧录完成，MCU 已按烧录流程复位并重新连接")
                    messagebox.showinfo("烧录完成", "ELF 烧录完成并已重新连接。")
                elif kind == "flash_error":
                    self.flash_button.configure(state="normal")
                    self.connect_button.configure(
                        text="断开 J-Link" if self.transport.connected else "连接 J-Link", state="normal"
                    )
                    self.reset_button.configure(state="normal")
                    self.connection_var.set("已连接" if self.transport.connected else "未连接")
                    self.status_var.set("ELF 烧录失败")
                    messagebox.showerror("烧录失败", event[1])
                elif kind == "rate":
                    self.actual_rate_var.set(f"Plot 实际 {event[1]:.1f} Hz | {event[2]} 批次")
                elif kind == "acquisition_error":
                    self.start_button.configure(text="开始采集")
                    self.status_var.set(f"采集停止：{event[1]}")
                    messagebox.showerror("采集错误", event[1])
        except queue.Empty:
            pass
        self._refresh_watch_table()
        self.plot.redraw()
        self.after(GUI_REFRESH_MS, self._ui_tick)

    def _on_close(self) -> None:
        self._closing = True
        self.engine.shutdown()
        try:
            self.transport.close()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = PlotGui()
    app.mainloop()


if __name__ == "__main__":
    main()
