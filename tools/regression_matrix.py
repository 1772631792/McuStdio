"""Hardware-free combinatorial regression matrix for MCU Studio."""

from __future__ import annotations

from itertools import product
import os
from pathlib import Path
import struct
import sys
import tempfile
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_user_workflow_matrix():
    from mcu_studio.ui import variable_write_available

    dimensions = {
        "backend": ("JLINK", "OPENOCD"),
        "theme": ("DARK", "LIGHT"),
        "variable": ("elf_scalar", "manual_address", "structured_member"),
        "operation": ("read", "table_write", "slider_write"),
        "lifecycle": ("connected", "disconnected", "reconnecting", "flashing"),
        "layout": (1, 2, 3),
    }
    count = 0
    for backend, theme, variable, operation, lifecycle, layout in product(*dimensions.values()):
        connected = lifecycle == "connected"
        suspended = lifecycle in ("disconnected", "reconnecting", "flashing")
        flashing = lifecycle == "flashing"
        allowed = variable_write_available(
            suspended, flashing, backend,
            connected and backend == "JLINK",
            connected and backend == "OPENOCD",
        )
        expected = operation == "read" or connected
        if operation != "read":
            require(allowed == expected, (backend, theme, variable, operation, lifecycle, layout))
        require(theme in ("DARK", "LIGHT") and layout in (1, 2, 3), "invalid UI dimension")
        count += 1
    require(count == 432, f"unexpected workflow matrix size: {count}")
    return count


def validate_numeric_matrix():
    from mcu_studio.ui import automatic_slider_profile

    types = (
        ("uint8_t", 1, 0x07, (0, 1, 127, 255)),
        ("int8_t", 1, 0x05, (-128, -1, 0, 127)),
        ("uint16_t", 2, 0x07, (0, 1, 32768, 65535)),
        ("int16_t", 2, 0x05, (-32768, -1, 0, 32767)),
        ("uint32_t", 4, 0x07, (0, 1, 100, 1_000_000)),
        ("int32_t", 4, 0x05, (-1_000_000, -1, 0, 1_000_000)),
        ("float", 4, 0x04, (-1000.5, -0.001, 0.0, 0.001, 1000.5)),
        ("double", 8, 0x04, (-1e9, -1e-9, 0.0, 1e-9, 1e9)),
        ("bool", 1, 0x02, (0, 1)),
    )
    count = 0
    for name, size, encoding, values in types:
        info = {"kind": "scalar", "name": name, "size": size, "encoding": encoding}
        for value in values:
            minimum, maximum, step = automatic_slider_profile(value, info, name)
            require(minimum <= value <= maximum, (name, value, minimum, maximum))
            require(maximum > minimum and step > 0, (name, value, step))
            if encoding != 0x04:
                require(float(step).is_integer(), (name, step))
            count += 1
    return count


def validate_queue_matrix():
    from mcu_studio.core import ProjectConfig
    from mcu_studio.workers import VariableWriteWorker

    count = 0
    for backend, width, burst in product(("JLINK", "OPENOCD"), (1, 2, 4, 8), (1, 5, 25)):
        config = ProjectConfig()
        config.backend_mode = backend
        worker = VariableWriteWorker(config)
        for value in range(burst):
            data = int(value).to_bytes(width, "little")
            worker.submit("gain", 0x20000000, data, value)
        require(len(worker._pending) == 1, (backend, width, burst))
        request = worker._pending["gain"]
        require(request[2] == int(burst - 1).to_bytes(width, "little"), (backend, width, burst))
        count += 1
    return count


def validate_discovery_matrix():
    from mcu_studio.core import discover_jlink_directories, discover_openocd_executables

    count = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        jlink_saved = root / "JLinkSaved"
        jlink_current = root / "JLinkCurrent"
        dll_name = "JLink_x64.dll" if struct.calcsize("P") == 8 else "JLinkARM.dll"
        for path in (jlink_saved, jlink_current):
            path.mkdir()
            (path / dll_name).touch()
        ocd_saved = root / "saved" / "openocd.exe"
        ocd_current = root / "current" / "openocd.exe"
        for path in (ocd_saved, ocd_current):
            path.parent.mkdir()
            path.touch()
        for saved_valid, current_valid in product((False, True), repeat=2):
            saved_j = str(jlink_saved if saved_valid else root / "missing-jlink")
            current_j = str(jlink_current if current_valid else root / "missing-current-jlink")
            jlinks = discover_jlink_directories(saved_j, current_j)
            if saved_valid:
                require(jlinks[0] == str(jlink_saved), "saved J-Link did not win")
            elif current_valid:
                require(jlinks[0] == str(jlink_current), "current J-Link did not win")

            saved_o = str(ocd_saved if saved_valid else root / "missing-openocd.exe")
            current_o = str(ocd_current if current_valid else root / "missing-current-openocd.exe")
            openocds = discover_openocd_executables(saved_o, current_o)
            if saved_valid:
                require(openocds[0] == str(ocd_saved), "saved OpenOCD did not win")
            elif current_valid:
                require(openocds[0] == str(ocd_current), "current OpenOCD did not win")
            count += 2
    return count


def main():
    results = {
        "user workflows": validate_user_workflow_matrix(),
        "numeric slider profiles": validate_numeric_matrix(),
        "coalesced write queues": validate_queue_matrix(),
        "tool discovery priority": validate_discovery_matrix(),
    }
    total = sum(results.values())
    for name, count in results.items():
        print(f"[OK] {name}: {count} combinations")
    print(f"\nRegression matrix passed: {total} combinations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
