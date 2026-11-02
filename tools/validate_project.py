"""Hardware-free validation for the maintained MCU Studio Python project."""

from __future__ import annotations

import ast
import compileall
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def validate_compilation() -> None:
    files = [
        PROJECT_ROOT / "main.py",
        PROJECT_ROOT / "build.py",
        PROJECT_ROOT / "packaging" / "Mcu_Studio.spec",
        Path(__file__),
    ]
    for path in files:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
    check(compileall.compile_dir(PROJECT_ROOT / "mcu_studio", quiet=1), "all application modules compile")

    import build as build_config

    build_config.validate_project_layout()
    check(
        build_config.ICON_PNG.name == "mcu_studio_anime.png"
        and build_config.ICON_FILE.name == "mcu_studio_anime.ico"
        and build_config.ICON_PNG.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        and build_config.ICON_FILE.read_bytes().startswith(b"\x00\x00\x01\x00"),
        "anime application icon is valid and wired into the build",
    )


def validate_imports_and_elf_helpers() -> None:
    import mcu_studio.core as core
    import mcu_studio.ui as ui
    import mcu_studio.workers as workers

    check(ui.MCUStudioWindow.__name__ == "MCUStudioWindow", "UI module imports")
    check(workers.ElfFlashWorker.__name__ == "ElfFlashWorker", "worker module imports")
    database = core.ElfDatabase()
    database.symbols = {"value": 0x20000000}
    database.types = {"value": {"kind": "scalar", "name": "uint32_t", "size": 4}}
    address, _type = database.resolve("value")
    check(address == 0x20000000, "ELF expression resolver executes")

    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    large_database = core.ElfDatabase()
    large_database.path = "synthetic-large.elf"
    large_database.symbols = {f"variable_{index:05d}": 0x20000000 + index * 4 for index in range(10_000)}
    scalar = {"kind": "scalar", "name": "uint32_t", "size": 4, "encoding": 7}
    large_database.types = {name: scalar for name in large_database.symbols}
    dialog = ui.ElfVariableDialog(large_database)
    check(
        dialog.tree.topLevelItemCount() == dialog.MAX_VISIBLE_RESULTS,
        "large ELF browser bounds the number of rendered rows",
    )
    selected_targets = []
    dialog.symbol_selected_signal.connect(lambda name, slot: selected_targets.append((name, slot)))
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog._accept_item(2, close_dialog=False)
    check(selected_targets and selected_targets[-1][1] == 2, "ELF browser can target any debug window")
    dialog.close()
    application.processEvents()
    with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", [str(Path(directory) / "main.py")]):
        test_database = core.LiveWatchDB()
        login = ui.LoginConfigDialog(test_database)
        interface_values = [login.interface_combo.itemData(index) for index in range(login.interface_combo.count())]
        target_values = [login.target_combo.itemData(index) for index in range(login.target_combo.count())]
        check(
            interface_values == ["stlink.cfg", "cmsis-dap.cfg", "jlink.cfg"],
            "OpenOCD probe dropdown contains ST-Link, DAPLink and J-Link",
        )
        check(
            target_values == ["stm32f1x.cfg", "stm32f2x.cfg", "stm32f3x.cfg", "stm32f4x.cfg", "stm32h7x.cfg"],
            "OpenOCD target dropdown contains STM32 F1-F4 and H7",
        )
        hinted = login._friendly_openocd_error(
            "LIBUSB_ERROR_NOT_SUPPORTED\nError: No J-Link device found"
        )
        check("原生 J-Link" in hinted and "替换 J-Link USB 驱动" in hinted, "J-Link/libusb failure has a safe diagnostic")
        login.close()
        application.processEvents()

        config = core.ProjectConfig()
        with patch.object(ui.MCUStudioWindow, "init_global_link_backend"), patch.object(
            ui.MCUStudioWindow, "load_elf_symbols"
        ):
            studio = ui.MCUStudioWindow(config, test_database)
        studio.show()
        application.processEvents()
        check(studio.tab_widget.count() == 3, "three fixed debug windows are created")
        check(studio.debug_trees[0].headerItem().text(0) == "全局变量", "debug variable column uses the compact title")
        check(
            studio.top_bar.height() < 60
            and studio.plot_toolbar_widget.height() == 40
            and studio.centralWidget().layout().stretch(1) == 1,
            "toolbars stay compact while the workspace receives all spare height",
        )
        action_buttons = (
            studio.btn_disconnect,
            studio.btn_reconnect,
            studio.btn_flash,
            studio.btn_mcu_reset,
        )
        check(
            [button.property("segmentPosition") for button in action_buttons]
            == ["first", "middle", "middle", "last"],
            "toolbar actions form one connected segmented group",
        )
        check(
            studio.btn_freeze.property("plotTool")
            and studio.btn_canvas_adapt.property("plotTool")
            and studio.rad_map1.isCheckable(),
            "waveform controls use the compact plot toolbar",
        )
        bottom_axis = studio.plot_widgets[0].getAxis("bottom")
        check(
            "ms" in bottom_axis.labelText and not bottom_axis.labelUnits,
            "waveform time axis stays in raw milliseconds without SI prefixes",
        )
        preserved_curve = studio.plot_widgets[1].plot([0.0, 1.0], [1.0, 2.0])
        studio.plot_active_paths[1].append("layout-test")
        studio.plot_curves_dict[1]["layout-test"] = preserved_curve
        studio.handle_layout_changed(1)
        studio.handle_layout_changed(2)
        application.processEvents()
        check(
            studio.plot_curves_dict[1].get("layout-test") is preserved_curve,
            "switching waveform layouts preserves plot items instead of rebuilding them",
        )
        studio.plot_widgets[1].removeItem(preserved_curve)
        studio.plot_curves_dict[1].clear()
        studio.plot_active_paths[1].remove("layout-test")
        original_reconnect_style = studio.btn_reconnect.styleSheet()
        studio._execute_flash_animation(studio.btn_reconnect, "#0a84ff")
        QTest.qWait(260)
        check(
            studio.btn_reconnect.text() == "重连"
            and studio.btn_reconnect.styleSheet() == original_reconnect_style,
            "button feedback preserves text and restores theme colors",
        )
        studio.ch_paths[0] = "preview.value"
        studio.plot_active_paths[0] = ["preview.value"]
        studio.ch_time_stamps[0] = [0.0, 1000.0, 2000.0]
        studio.ch_buffers[0] = [1.0, 3.0, 2.0]
        studio.start_canvas_auto_adapt()
        check(
            studio.canvas_fixed_ranges[0] is not None
            and studio.lbl_status_text.text().startswith("已适配"),
            "waveform fit uses buffered data immediately without a delay timer",
        )
        studio.db.save_var("shared_value", "uint32", 0, "0x20000000", 0)
        studio.db.save_var("shared_value", "uint32", 0, "0x20000000", 1)
        studio.inject_var_to_tree_ui("shared_value", "uint32", 0)
        studio.ch_paths[0] = "shared_value"
        shared_root = studio.debug_trees[0].topLevelItem(0)
        studio.remove_variable_root(shared_root, studio.debug_trees[0])
        check(
            studio.ch_paths[0] == "shared_value",
            "removing one duplicate watch keeps the shared waveform binding",
        )
        studio.db.delete_var("shared_value", 1)
        studio.ch_paths[0] = None
        studio.ch_buffers[0] = []
        studio.ch_time_stamps[0] = []
        original_tree = studio.debug_trees[1]
        detachable_bar = studio.tab_widget.tabBar()
        check(hasattr(detachable_bar, "detach_requested"), "debug tabs support drag-out detaching")
        detachable_bar.detach_requested.emit(1, QPoint(120, 80))
        application.processEvents()
        check(
            1 in studio.detached_debug_windows and studio.tab_widget.count() == 2,
            "detaching removes the tab from the main tab bar",
        )
        floating_window = studio.detached_debug_windows[1]
        check(original_tree.parent() is floating_window and original_tree.isVisible(), "floating window shows the complete debug tree")
        studio.elf_db = database
        studio.symbol_cache = dict(database.symbols)
        studio.add_elf_variable("value", 1)
        check(original_tree.topLevelItemCount() == 1, "ELF variable can be added to a detached debug window")
        check(studio.debug_slot_for_tree(studio.tab_widget.widget(1)) == 2, "remaining tab indices keep their logical window slots")
        studio.detached_debug_windows[1].close()
        application.processEvents()
        check(studio.tab_widget.count() == 3 and studio.tab_widget.widget(1) is original_tree, "closing a floating window restores the tab in order")
        studio.close()
        application.processEvents()
        test_database.conn.close()


class FragmentedSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


def validate_tcl_protocol() -> None:
    from mcu_studio.core import decode_openocd_memory_response, receive_tcl_response

    response = receive_tcl_response(FragmentedSocket([b"Open On-", b"Chip Debugger\x1aunused"]))
    check(response == "Open On-Chip Debugger", "fragmented TCL response is reassembled")
    try:
        receive_tcl_response(FragmentedSocket([b"x" * 32]), max_bytes=16)
    except RuntimeError:
        pass
    else:
        raise AssertionError("oversized TCL response was not rejected")
    print("[OK] oversized TCL response is rejected")

    channels = [
        {"ch_idx": 0, "addr": 0x20000150, "width": 4, "is_float": True, "is_signed": False, "byte_read": False},
        {"ch_idx": 1, "addr": 0x20000154, "width": 4, "is_float": True, "is_signed": False, "byte_read": False},
    ]
    raw_value = struct.unpack("<I", struct.pack("<f", 3.7745))[0]
    decoded = decode_openocd_memory_response(
        f"0x20000150: 0x{raw_value:08x} 0x45ad8000", channels, 0x20000150, 2
    )
    check(abs(decoded[0] - 3.7745) < 0.0001 and decoded[1] == 5552.0, "OpenOCD and J-Link share scalar decoding")


def validate_openocd_configuration() -> None:
    from mcu_studio.core import (
        ProjectConfig,
        default_openocd_executable,
        discover_openocd_configs,
        launch_openocd,
        resolve_openocd_config,
        validate_openocd_config,
    )

    config = ProjectConfig()
    config.backend_mode = "OPENOCD"
    config.openocd_exe = str(PROJECT_ROOT / "runtime" / "OpenOCD" / "bin" / "openocd.exe")
    config.interface = "cmsis-dap.cfg"
    config.target = "stm32f0x.cfg"
    details = validate_openocd_config(config)
    args = config.get_openocd_args()
    check(Path(details["interface"]).is_file() and Path(details["target"]).is_file(), "OpenOCD cfg files resolve")
    interfaces = discover_openocd_configs(config.openocd_exe, "interface")
    targets = discover_openocd_configs(config.openocd_exe, "target")
    check("cmsis-dap.cfg" in interfaces and "stm32f0x.cfg" in targets, "OpenOCD cfg choices are auto-discovered")
    check(
        Path(resolve_openocd_config(config.openocd_exe, "interface/cmsis-dap.cfg", "interface")).is_file(),
        "prefixed relative OpenOCD cfg paths resolve",
    )
    check("gdb port disabled" in args and "telnet port disabled" in args, "unused OpenOCD ports are disabled")
    check(Path(default_openocd_executable()).is_file(), "bundled OpenOCD is discoverable")
    result = subprocess.run(
        [config.openocd_exe, *args, "-c", "shutdown"],
        cwd=Path(config.openocd_exe).parent,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    )
    output = result.stdout + result.stderr
    check(result.returncode == 0 and "shutdown command invoked" in output, "OpenOCD accepts generated arguments and cfg files")
    check("DEPRECATED" not in output, "OpenOCD command syntax has no deprecation warnings")
    jlink_driver = subprocess.run(
        [config.openocd_exe, "-c", "adapter driver jlink", "-c", "shutdown"],
        cwd=Path(config.openocd_exe).parent,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
    )
    check(jlink_driver.returncode == 0, "bundled OpenOCD includes the J-Link adapter driver")

    with tempfile.TemporaryDirectory() as custom_dir:
        custom_interface = Path(custom_dir) / "vendor-probe.cfg"
        custom_target = Path(custom_dir) / "vendor-chip.cfg"
        custom_interface.touch()
        custom_target.touch()
        config.interface = str(custom_interface)
        config.target = str(custom_target)
        custom_args = config.get_openocd_args()
        search_dirs = [custom_args[index + 1] for index, value in enumerate(custom_args) if value == "-s"]
        check(str(Path(custom_dir)) in search_dirs, "custom cfg directory is added to OpenOCD search paths")
    config.interface = "cmsis-dap.cfg"
    config.target = "stm32f0x.cfg"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        config.tcl_port = occupied.getsockname()[1]
        with patch("mcu_studio.core.subprocess.Popen") as popen:
            try:
                launch_openocd(config, startup_timeout=0.5)
            except RuntimeError as exc:
                check("已被占用" in str(exc), "occupied OpenOCD TCL port is rejected")
            else:
                raise AssertionError("occupied OpenOCD port was accepted")
            check(not popen.called, "port collision cannot start or connect to another process")


def validate_jlink_bitness_and_flash_command() -> None:
    import mcu_studio.core as core
    import mcu_studio.workers as workers

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        expected_name = "JLink_x64.dll" if sys.maxsize > 2**32 else "JLinkARM.dll"
        expected_dll = root / expected_name
        expected_dll.touch()
        check(Path(core.jlink_dll_path(root)) == expected_dll, "J-Link DLL bitness is deterministic")

        commander = root / "JLink.exe"
        firmware = root / "firmware.elf"
        commander.touch()
        firmware.touch()
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["cwd"] = kwargs["cwd"]
            script = Path(command[command.index("-CommanderScript") + 1])
            captured["script"] = script.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "O.K.")

        worker = workers.ElfFlashWorker(
            str(commander), str(root), str(firmware), "PY32F002Ax5", "Cortex-M0"
        )
        with patch.object(workers.subprocess, "run", side_effect=fake_run), patch.object(
            worker, "_reconnect_without_reset", return_value=None
        ):
            worker.run()
        command = captured["command"]
        check(command[command.index("-device") + 1] == "PY32F002Ax5", "original J-Link device name is preserved")
        check(captured["cwd"] == str(root), "J-Link Commander uses its own installation directory")
        check(str(firmware) in captured["script"], "J-Link script contains the selected ELF")


def validate_large_watch_database() -> None:
    from mcu_studio.core import LiveWatchDB

    with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", [str(Path(directory) / "main.py")]):
        database = LiveWatchDB()
        records = [
            {"path": f"state.values[{index}]", "type": "uint32_t", "offset": index * 4}
            for index in range(10_000)
        ]
        database.replace_root_vars("state", 0, records)
        check(len(database.get_var_map(0)) == len(records), "large watch lists use one transaction and cached lookup")
        database.conn.close()


def validate_no_second_qt_binding() -> None:
    forbidden = {"PyQt5", "PySide2", "PySide6", "shiboken2", "shiboken6"}
    found = set()
    for path in (PROJECT_ROOT / "mcu_studio").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names if alias.name.split(".")[0] in forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in forbidden:
                    found.add(root)
    check(not found, "application imports PyQt6 only")


def validate_git_whitespace() -> None:
    result = subprocess.run(
        ["git", "diff", "--check"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        errors="replace",
    )
    check(result.returncode == 0, "Git diff has no whitespace errors")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    validate_compilation()
    validate_imports_and_elf_helpers()
    validate_tcl_protocol()
    validate_openocd_configuration()
    validate_jlink_bitness_and_flash_command()
    validate_large_watch_database()
    validate_no_second_qt_binding()
    validate_git_whitespace()
    print("\nProject validation passed without accessing hardware.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
