"""Hardware-free validation for the maintained MCU Studio Python project."""

from __future__ import annotations

import ast
import compileall
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
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
        PROJECT_ROOT / "build_portable.py",
        PROJECT_ROOT / "packaging" / "Mcu_Studio.spec",
        PROJECT_ROOT / "tools" / "regression_matrix.py",
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
    import numpy as np
    import mcu_studio.core as core
    import mcu_studio.spectrum as spectrum
    import mcu_studio.ui as ui
    import mcu_studio.workers as workers

    check(ui.MCUStudioWindow.__name__ == "MCUStudioWindow", "UI module imports")
    check(
        "*.elf" in ui.FIRMWARE_FILE_FILTER
        and "*.axf" in ui.FIRMWARE_FILE_FILTER,
        "firmware picker accepts ELF and AXF files",
    )
    first_batch, next_offset = ui.rotating_batch(list(range(70)), 0, 32)
    second_batch, final_offset = ui.rotating_batch(list(range(70)), next_offset, 32)
    check(
        len(first_batch) == len(second_batch) == 32
        and first_batch == list(range(32))
        and second_batch == list(range(32, 64))
        and final_offset == 64,
        "large visible watch lists are read in bounded round-robin batches",
    )
    check(
        ui.QMessageBox is ui.SilentMessageBox,
        "application dialogs use the silent message box implementation",
    )
    check(
        workers.ElfFlashWorker.__name__ == "ElfFlashWorker"
        and workers.ElfParseWorker.__name__ == "ElfParseWorker",
        "flash and background ELF workers import",
    )
    check(workers.OpenOcdFlashWorker.__name__ == "OpenOcdFlashWorker", "OpenOCD flash worker imports")
    fft_size = 2048
    fft_rate = 1024.0
    fft_time = np.arange(fft_size, dtype=float) / fft_rate
    fft_signal = (2.0 * np.sin(2.0 * np.pi * 32.0 * fft_time)
                  + 0.2 * np.sin(2.0 * np.pi * 64.0 * fft_time)
                  + 0.1 * np.sin(2.0 * np.pi * 96.0 * fft_time))
    fft_result = spectrum.analyze_spectrum(
        fft_time * 1000.0,
        fft_signal,
        {
            "fft_size": fft_size,
            "window": "flattop",
            "fundamental_min_hz": 28.0,
            "fundamental_max_hz": 36.0,
            "harmonic_count": 5,
            "harmonic_search_bins": 1,
        },
    )
    check(
        fft_result["ok"]
        and abs(fft_result["fundamental_frequency_hz"] - 32.0) < 0.1
        and abs(fft_result["fundamental_amplitude"] - 2.0) < 0.03
        and abs(fft_result["harmonics"][1]["percent"] - 10.0) < 0.3
        and abs(fft_result["harmonics"][2]["percent"] - 5.0) < 0.3
        and abs(fft_result["thd_percent"] - math.sqrt(125.0)) < 0.5,
        "FFT corrects window gain and reports fundamental, harmonics and THD",
    )
    check(
        workers.LowSpeedReadWorker.__name__ == "LowSpeedReadWorker"
        and workers.BackendReconnectWorker.__name__ == "BackendReconnectWorker",
        "non-blocking read and reconnect workers import",
    )
    write_config = core.ProjectConfig()
    write_worker = workers.VariableWriteWorker(write_config)
    first_sequence = write_worker.submit("gain", 0x20000000, b"\x01", 1)
    second_sequence = write_worker.submit("gain", 0x20000000, b"\x02", 2)
    check(
        second_sequence == first_sequence + 1
        and len(write_worker._pending) == 1
        and write_worker._pending["gain"][2] == b"\x02",
        "slider writes keep only the newest pending value per variable",
    )
    read_channels = [
        {"ch_idx": 0, "addr": 0x20000000, "width": 4},
        {"ch_idx": 1, "addr": 0x20000004, "width": 4},
        {"ch_idx": 2, "addr": 0x20000010, "width": 4},
    ]
    read_blocks = workers.build_contiguous_read_blocks(read_channels)
    check(
        [(start, size, len(members)) for start, size, members in read_blocks]
        == [(0x20000000, 8, 2), (0x20000010, 4, 1)],
        "contiguous J-Link variables share reads without touching address gaps",
    )

    class FakeJLink:
        def __init__(self):
            self.calls = []
            self.worker = None

        def memory_read8(self, address, count):
            self.calls.append((address, count))
            self.worker.running = False
            return [0] * count

    fake_link = FakeJLink()
    batch_config = core.ProjectConfig()
    batch_worker = workers.HighSpeedSamplingWorker(
        batch_config,
        {
            0: ("0x20000000", "float", "first"),
            1: ("0x20000004", "float", "second"),
        },
        0,
        fake_link,
    )
    fake_link.worker = batch_worker
    batch_worker.run()
    check(
        fake_link.calls == [(0x20000000, 8)],
        "J-Link worker executes one DLL read for adjacent waveform variables",
    )

    class BusyJLink:
        def memory_read32(self, _address, _count):
            raise RuntimeError("J-Link is already in use by another application")

    busy_worker = workers.HighSpeedSamplingWorker(
        batch_config,
        {0: ("0x20000000", "uint32_t", "busy")},
        0,
        BusyJLink(),
    )
    link_losses = []
    busy_worker.link_lost.connect(link_losses.append)
    busy_worker.run()
    check(
        len(link_losses) == 1
        and "another application" in link_losses[0]
        and not busy_worker.is_link_alive,
        "sampling stops and reports once when another program takes the debugger",
    )
    database = core.ElfDatabase()
    database.symbols = {"value": 0x20000000}
    database.types = {"value": {"kind": "scalar", "name": "uint32_t", "size": 4}}
    address, _type = database.resolve("value")
    check(address == 0x20000000, "ELF expression resolver executes")

    from PyQt6.QtCore import QPoint, QPointF, Qt, QThread
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

    browser_database = core.ElfDatabase()
    browser_database.path = "browser.elf"
    browser_database.symbols = {"motor": 0x20000100, "temperature": 0x20000200}
    browser_database.types = {
        "motor": {
            "kind": "struct", "name": "MotorState", "size": 8,
            "source_file": "motor.c", "is_static": False,
            "members": [
                {"name": "speed", "offset": 0, "type": scalar},
                {"name": "current", "offset": 4, "type": scalar},
            ],
        },
        "temperature": dict(scalar, source_file="sensor.c", is_static=True),
    }
    dialog.replace_elf_database(browser_database)
    check(dialog.source_tree.topLevelItemCount() == 3, "ELF browser groups variables by source file")
    dialog.search.setText("motors")
    dialog._fill()
    check(
        dialog.tree.topLevelItemCount() == 1 and dialog.tree.topLevelItem(0).text(0) == "motor",
        "ELF browser variable search supports fuzzy subsequence matching",
    )
    dialog.search.clear()
    dialog._fill()
    dialog.source_search.setText("mtrc")
    dialog._fill_source_tree()
    check(
        dialog.source_tree.topLevelItemCount() == 2
        and dialog.source_tree.topLevelItem(1).data(0, Qt.ItemDataRole.UserRole) == "motor.c",
        "ELF browser source-file search supports fuzzy filtering",
    )
    dialog.source_search.clear()
    dialog._fill_source_tree()
    motor_source = next(
        dialog.source_tree.topLevelItem(index)
        for index in range(dialog.source_tree.topLevelItemCount())
        if dialog.source_tree.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) == "motor.c"
    )
    dialog.source_tree.setCurrentItem(motor_source)
    application.processEvents()
    motor_item = dialog.tree.topLevelItem(0)
    motor_item.setExpanded(True)
    application.processEvents()
    check(
        dialog.tree.topLevelItemCount() == 1
        and motor_item.childCount() == 2
        and motor_item.child(1).text(0) == "current"
        and motor_item.child(1).text(3) == "0x20000104",
        "ELF browser filters by source and lazily expands structured variables",
    )
    before_adds = len(selected_targets)
    dialog.tree.setCurrentItem(motor_item.child(0))
    dialog._accept_item(0)
    dialog._accept_item(0)
    check(
        len(selected_targets) == before_adds + 2
        and selected_targets[-1] == ("motor", 0),
        "ELF browser supports continuous adds without closing",
    )
    dialog.source_tree.setCurrentItem(dialog.source_tree.topLevelItem(0))
    dialog.recent_variables = {
        "temperature": {"count": 2, "last": 200},
        "motor": {"count": 8, "last": 100},
    }
    dialog._fill()
    check(
        dialog.tree.topLevelItem(0).text(0) == "temperature",
        "ELF browser ranks all-file results by most recent use",
    )
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
        check(
            login.jlink_dir_edit.isEditable()
            and login.openocd_edit.isEditable()
            and login.jlink_dir_edit.count() >= 1
            and login.openocd_edit.count() >= 1
            and login.discovery_worker is None,
            "debugger dropdowns load remembered paths without scanning at startup",
        )
        check(
            bool(login.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
            and login.btn_jl_dir.width() == 76
            and login.btn_detect_jlink.width() == 88,
            "configuration window is resizable and keeps path action buttons compact",
        )
        check(
            login.profile_combo.currentText() == "主配置"
            and not login.btn_profile_delete.isEnabled()
            and login.btn_profile_add.width() == 36
            and login.btn_profile_delete.width() == 36,
            "compact profile selector keeps the main profile undeletable",
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
        check(
            studio.backend_suspended
            and studio.jlink_native_instance is None
            and studio.reconnect_worker is None,
            "workspace UI is created before any debugger backend is claimed",
        )

        old_state = core.ElfDatabase()
        old_state.symbols = {"state": 0x20000000}
        old_state.types = {
            "state": {
                "kind": "struct", "name": "State", "size": 4,
                "members": [{"name": "value", "offset": 0, "type": scalar}],
            }
        }
        test_database.replace_root_vars("state", 0, old_state.flatten("state"))
        new_state = core.ElfDatabase()
        new_state.symbols = {"state": 0x20001000}
        new_state.types = {
            "state": {
                "kind": "struct", "name": "State", "size": 12,
                "members": [{"name": "value", "offset": 8, "type": scalar}],
            }
        }
        studio.elf_db = new_state
        studio.symbol_cache = dict(new_state.symbols)
        studio.synchronize_saved_variables_with_elf()
        state_row = test_database.get_var_map(0)["state.value"]
        check(
            state_row[0] == 8
            and studio.resolve_saved_variable_address("state.value", 0, "state") == 0x20001008,
            "saved watches follow current ELF names, addresses and member offsets",
        )
        new_state.symbols = {}
        new_state.types = {}
        studio.symbol_cache = {}
        studio.synchronize_saved_variables_with_elf()
        check(
            "state.value" in test_database.get_var_map(0)
            and studio.resolve_saved_variable_address("state.value", 8, "state") is None,
            "temporarily missing ELF roots stay saved without reusing stale addresses",
        )
        studio.plot_active_paths[0] = ["state.value"]
        check(
            studio._rebuild_sampling_channels_from_bindings() == 1
            and studio.plot_active_paths[0] == ["state.value"]
            and not studio.ch_paths,
            "temporarily missing waveform bindings remain configured but are not sampled",
        )
        new_state.symbols = {"state": 0x20002000}
        new_state.types = {
            "state": {
                "kind": "struct", "name": "State", "size": 12,
                "members": [{"name": "value", "offset": 8, "type": scalar}],
            }
        }
        studio.symbol_cache = dict(new_state.symbols)
        studio.synchronize_saved_variables_with_elf()
        check(
            studio._rebuild_sampling_channels_from_bindings() == 0
            and studio.ch_paths == ["state.value"]
            and studio.ch_addrs == [hex(0x20002008)],
            "saved waveform bindings resume automatically when the ELF variable returns",
        )
        new_state.symbols = {}
        new_state.types = {}
        studio.symbol_cache = {}
        studio.synchronize_saved_variables_with_elf(remove_missing=True)
        check(
            not test_database.get_var_map(0)
            and studio._rebuild_sampling_channels_from_bindings() == 0
            and not studio.plot_active_paths[0]
            and not studio.ch_paths,
            "authoritative post-flash ELF sync removes variable names that no longer exist",
        )
        studio.elf_last_signature = (1, 100)
        studio.elf_pending_signature = None
        studio.elf_pending_stable_ticks = 0
        with patch.object(studio, "_elf_file_signature", return_value=(2, 200)), patch.object(
            studio, "reload_elf_symbols"
        ) as premature_reload:
            studio.check_elf_file_changed()
            studio.check_elf_file_changed()
        check(
            not premature_reload.called
            and studio.elf_update_pending
            and studio.btn_flash.text() == "烧录*",
            "a newly compiled ELF stays pending until flash while the old symbol map remains active",
        )
        studio._set_elf_update_pending(False)

        studio.backend_suspended = False
        with patch.object(studio, "disconnect_backend", side_effect=lambda: setattr(studio, "backend_suspended", True)) as disconnect:
            studio._handle_backend_connection_lost("probe busy")
            studio._handle_backend_connection_lost("duplicate")
        check(
            disconnect.call_count == 1
            and studio.backend_disconnect_reason == "probe busy"
            and "手动重连" in studio.lbl_status_text.text(),
            "backend contention automatically suspends once and waits for manual reconnect",
        )

        studio.backend_suspended = True
        studio.initial_backend_connect = False
        studio.config.backend_mode = "JLINK"
        fake_backend = object()
        with patch.object(studio, "reload_elf_symbols", return_value=(True, "latest firmware loaded")) as reload_latest, patch.object(
            studio, "_finish_backend_reconnect"
        ) as finish_reconnect:
            studio._backend_reconnect_finished(True, "", fake_backend)
        check(
            reload_latest.call_args.kwargs == {
                "show_message": False,
                "source": "reconnect",
                "restart_sampling": False,
            }
            and finish_reconnect.call_args.args == (True, "latest firmware loaded", False),
            "manual reconnect reloads the latest ELF or AXF once before sampling resumes",
        )
        studio.jlink_native_instance = None
        studio.variable_write_worker.update_jlink(None)
        studio.backend_suspended = False
        studio.backend_disconnect_reason = ""

        check(studio.tab_widget.count() == 3, "three fixed debug windows are created")
        check(
            all(isinstance(tree, ui.DebugVariableTree) for tree in studio.debug_trees.values()),
            "docked and floating debug trees share variable drag-and-drop support",
        )
        studio.backend_suspended = True
        test_database.save_var("controller.gain", "float", 4, "controller", 0)
        studio.inject_var_to_tree_ui("controller.gain", "float", 0)
        studio.move_debug_variable("controller.gain", 0, 1, is_member=True)
        standalone_item = next(
            (
                studio.debug_trees[1].topLevelItem(index)
                for index in range(studio.debug_trees[1].topLevelItemCount())
                if studio.debug_trees[1].topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
                    == "controller.gain"
                and studio.debug_trees[1].topLevelItem(index).data(
                    0, int(Qt.ItemDataRole.UserRole) + 1
                ) == "standalone"
            ),
            None,
        )
        check(
            "controller.gain" in test_database.get_var_map(0)
            and "controller.gain" in test_database.get_var_map(1)
            and standalone_item is not None,
            "a structure member can be dragged out as an independent tuning item",
        )
        test_database.save_var("movable", "uint32_t", 0, "movable", 0)
        studio.inject_var_to_tree_ui("movable", "uint32_t", 0)
        studio.move_debug_variable("movable", 0, 2, is_member=False)
        check(
            "movable" not in test_database.get_var_map(0)
            and "movable" in test_database.get_var_map(2),
            "a top-level watch moves between the main and floating-capable debug windows",
        )
        # Keep the drag-and-drop fixture isolated from the original UI tests
        # below, which intentionally assume an empty watch tree.
        test_database.delete_var("controller.gain", 0)
        test_database.delete_var("controller.gain", 1)
        test_database.delete_var("movable", 2)
        studio.standalone_debug_variables.discard("1:controller.gain")
        studio._save_standalone_debug_variables()
        studio._rebuild_debug_trees()
        check(studio.debug_trees[0].headerItem().text(0) == "全局变量", "debug variable column uses the compact title")
        studio.centralWidget().layout().activate()
        application.processEvents()
        check(
            studio.top_bar.height() < 60 and studio.plot_toolbar_widget.height() == 40,
            f"toolbars stay compact (top {studio.top_bar.height()} px, plot {studio.plot_toolbar_widget.height()} px)",
        )
        check(
            studio.centralWidget().layout().stretch(1) == 1,
            "workspace receives all spare vertical height",
        )
        check(
            studio.btn_back_config.objectName() == "backConfigButton"
            and studio.btn_back_config.width() == 38
            and studio.btn_back_config.mapTo(studio.top_bar, QPoint(0, 0)).x()
                < studio.btn_disconnect.mapTo(studio.top_bar, QPoint(0, 0)).x()
            and "不会向 MCU 发送复位" in studio.btn_back_config.toolTip(),
            "compact return icon sits beside Disconnect without resetting the target",
        )
        float_profile = ui.automatic_slider_profile(12.5, {"kind": "scalar", "size": 4, "encoding": 0x04}, "float")
        int_profile = ui.automatic_slider_profile(100, {"kind": "scalar", "size": 1, "encoding": 0x07}, "uint8_t")
        check(
            float_profile[0] < 12.5 < float_profile[1]
            and 0 < float_profile[2] < 1
            and int_profile == (0.0, 255.0, 2.0)
            and ui.InlineVariableSlider.__name__ == "InlineVariableSlider",
            "variable slider ranges and steps are derived from type and current value",
        )
        studio.db.save_var("slider_value", "float", 0, "0x20000020", 0)
        studio.inject_var_to_tree_ui("slider_value", "float", 0)
        slider_item = next(
            studio.debug_trees[0].topLevelItem(index)
            for index in range(studio.debug_trees[0].topLevelItemCount())
            if studio.debug_trees[0].topLevelItem(index).text(0) == "slider_value"
        )
        check(
            studio.enable_inline_slider(
                studio.debug_trees[0], slider_item, "slider_value",
                {"min": -5.0, "max": 5.0, "step": 0.1, "live_write": False},
            )
            and isinstance(studio.inline_slider_widgets.get("0:slider_value"), ui.InlineVariableSlider)
            and studio.inline_slider_widgets["0:slider_value"].plus_button.text() == "+"
            and studio.debug_trees[0].itemWidget(slider_item, 2) is None
            and studio.inline_slider_contexts["0:slider_value"][2].isFirstColumnSpanned()
            and studio.debug_trees[0].itemWidget(studio.inline_slider_contexts["0:slider_value"][2], 0)
                is studio.inline_slider_widgets["0:slider_value"]
            and "0:slider_value" in studio.inline_slider_states,
            "numeric watches open a persisted full-width slider row directly below the variable",
        )
        slider_widget = studio.inline_slider_widgets["0:slider_value"]
        studio.handle_tree_item_double_clicked(slider_item, 2)
        check(
            studio.inline_slider_widgets["0:slider_value"] is slider_widget
            and studio.debug_trees[0].itemWidget(slider_item, 2) is None,
            "double-clicking a controlled variable cannot replace its dedicated slider panel",
        )
        slider_widget.value = 1.0
        slider_widget.user_editing = True
        slider_widget.update_from_target(3.0)
        value_during_drag = slider_widget.value
        slider_widget.user_editing = False
        slider_widget.update_from_target(3.0)
        check(
            value_during_drag == 1.0
            and slider_widget.value == 3.0
            and math.isclose(slider_widget._quantize(3.06), 3.1, rel_tol=0.0, abs_tol=1e-12)
            and not slider_widget.live_write,
            "target readback cannot override an active gesture and slider values follow the configured step",
        )
        slider_widget.apply_state({
            "min": -1.0, "max": 1.0, "step": 0.1,
            "live_write": False, "auto_range": True,
        })
        slider_widget.value = 1.0
        slider_widget._step_by(1)
        check(
            slider_widget.value > 1.0
            and slider_widget.maximum > 1.0
            and slider_widget.minus_button.autoRepeat()
            and slider_widget.plus_button.autoRepeat()
            and slider_widget.slider.focusPolicy() == Qt.FocusPolicy.StrongFocus,
            "slider arrows and held step buttons adjust values and automatically expand the range",
        )
        stale_read_key = (0x20000020, 4)
        slider_item.setText(2, "0.0500")
        studio.low_speed_pending = {
            (0, "slider_value"): (slider_item, stale_read_key, None, "float", None),
        }
        studio._finish_low_speed_read({stale_read_key: struct.pack("<f", 0.05)}, "")
        check(
            slider_item.text(2) == "0.0500"
            and studio.inline_slider_widgets["0:slider_value"] is slider_widget
            and math.isclose(slider_widget.value, 0.05, abs_tol=1e-6),
            "low-speed readback updates both the ordinary table value and dedicated slider panel",
        )
        studio.db.save_var("slider_value_2", "float", 0, "0x20000024", 0)
        studio.inject_var_to_tree_ui("slider_value_2", "float", 0)
        slider_item_2 = next(
            studio.debug_trees[0].topLevelItem(index)
            for index in range(studio.debug_trees[0].topLevelItemCount())
            if studio.debug_trees[0].topLevelItem(index).text(0) == "slider_value_2"
        )
        check(
            studio.enable_inline_slider(
                studio.debug_trees[0], slider_item_2, "slider_value_2",
                {"min": 0.0, "max": 10.0, "step": 0.5},
            )
            and len(studio.inline_slider_widgets) == 2
            and studio.inline_slider_widgets["0:slider_value"] is slider_widget
            and studio.inline_slider_widgets["0:slider_value_2"].step == 0.5,
            "multiple variables keep independent full-width slider rows at the same time",
        )
        studio.disable_inline_slider(studio.debug_trees[0], slider_item_2, "slider_value_2")
        check(
            "0:slider_value" in studio.inline_slider_widgets
            and "0:slider_value_2" not in studio.inline_slider_widgets,
            "one slider row can be removed without affecting the others",
        )
        studio.db.delete_var("slider_value_2", 0)
        studio.debug_trees[0].takeTopLevelItem(studio.debug_trees[0].indexOfTopLevelItem(slider_item_2))
        history_session = "validation-session"
        studio.history_writer.submit([
            (history_session, 1.0, "slider_value", 0.1),
            (history_session, 2.0, "slider_value", 0.2),
        ])
        history_rows = []
        for _attempt in range(40):
            time.sleep(0.05)
            history_rows = test_database.get_sample_history("slider_value", history_session)
            if len(history_rows) == 2:
                break
        check(
            history_rows == [(1.0, 0.1), (2.0, 0.2)],
            "waveform history is batch-persisted by a dedicated SQLite writer",
        )
        studio.disable_inline_slider(studio.debug_trees[0], slider_item, "slider_value")
        studio.db.delete_var("slider_value", 0)
        studio.debug_trees[0].takeTopLevelItem(studio.debug_trees[0].indexOfTopLevelItem(slider_item))
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
            and studio.btn_measure.property("plotTool")
            and studio.rad_map1.isCheckable(),
            "waveform controls use the compact plot toolbar",
        )
        check(
            studio.curve_palette[:3] == ["#30d158", "#ff9f0a", "#0a84ff"]
            and not studio.plot_widgets[0].getPlotItem().menuEnabled(),
            "waveforms use an automatic color palette without pyqtgraph menus",
        )
        fft_time_ms = (np.arange(512, dtype=float) / 512.0 * 1000.0).tolist()
        studio.ch_paths = ["fft.signal"]
        studio.ch_types = ["float"]
        studio.ch_addrs = ["0x20000000"]
        studio.ch_time_stamps = [fft_time_ms]
        studio.ch_buffers = [[float(value) for value in np.sin(2.0 * np.pi * 16.0 * np.arange(512) / 512.0)]]
        studio.plot_active_paths[0] = ["fft.signal"]
        studio.fft_settings[0].update({
            "fft_size": 512,
            "fundamental_min_hz": 12.0,
            "fundamental_max_hz": 20.0,
        })
        studio.is_frozen = True
        studio.set_plot_mode(0, "fft")
        studio.fft_last_update[0] = 0.0
        studio._update_fft_plot(0)
        check(
            studio.plot_modes[0] == "fft"
            and studio.fft_results[0]["fft.signal"]["ok"]
            and studio.fft_summary_items[0].isVisible()
            and studio.plot_widgets[0].getAxis("bottom").labelText == "频率 (Hz)",
            "waveform context mode renders persisted FFT spectrum and harmonic summary",
        )
        fft_harmonic = studio.fft_results[0]["fft.signal"]["harmonics"][0]
        fft_scene_point = studio.plot_widgets[0].getViewBox().mapViewToScene(QPointF(
            fft_harmonic["frequency_hz"], fft_harmonic["percent"]
        ))
        studio.handle_plot_mouse_move(fft_scene_point, 0)
        check(
            studio.hover_dots[0].isVisible()
            and studio.hover_labels[0].isVisible()
            and "H1" in studio.hover_labels[0].toPlainText(),
            "frozen FFT hover identifies spectral bins and harmonic percentages",
        )
        studio.set_plot_mode(0, "time")
        studio.is_frozen = False
        studio.toggle_measurement_mode(True)
        studio.ch_paths = ["measure.first", "measure.second"]
        studio.ch_types = ["float", "float"]
        studio.ch_addrs = ["0x20000000", "0x20000004"]
        studio.plot_active_paths[0] = ["measure.first", "measure.second"]
        studio.ch_time_stamps = [[10.0, 20.0, 30.0], [10.0, 25.5, 30.0]]
        studio.ch_buffers = [[1.0, 2.0, 3.0], [1.5, 3.75, 2.5]]
        measurement_view = studio.plot_widgets[0].getViewBox()
        studio.plot_widgets[0].setXRange(0.0, 40.0, padding=0)
        studio.plot_widgets[0].setYRange(0.0, 5.0, padding=0)
        application.processEvents()
        nearest = studio._nearest_visible_sample(
            0, measurement_view.mapViewToScene(QPointF(25.5, 3.75))
        )
        studio.measurement_points[0] = [
            (10.0, 1.25, 0, "first"),
            (25.5, 3.75, 0, "second"),
        ]
        studio._render_measurement(0)
        measurement_x, measurement_y = studio.measurement_lines[0].getData()
        measurement_text = studio.lbl_measurement_result.text()
        check(
            studio.measurement_mode
            and studio.is_frozen
            and studio.btn_measure.text() == "测量中"
            and nearest == (25.5, 3.75, 0, "measure.second")
            and list(measurement_x) == [10.0, 25.5]
            and list(measurement_y) == [1.25, 3.75]
            and "Δt" in measurement_text
            and "+15.50 ms" in measurement_text
            and "ΔY" in measurement_text,
            "two-point measurement renders snapped points and bottom-bar deltas",
        )
        studio.toggle_measurement_mode(False)
        studio.ch_paths = []
        studio.ch_types = []
        studio.ch_addrs = []
        studio.plot_active_paths = [[], [], []]
        studio.ch_time_stamps = []
        studio.ch_buffers = []
        check(
            not studio.measurement_mode
            and studio.btn_measure.text() == "测量"
            and studio.lbl_measurement_result.text() == "测量：未开启",
            "leaving measurement mode clears its overlays",
        )
        # Resume after the measurement test so the global Space assertions below
        # still start from the normal running state.
        studio.toggle_freeze()
        studio.db.save_var("multi_a", "float", 0, "0x20000000", 0)
        studio.db.save_var("multi_b", "float", 0, "0x20000004", 0)
        studio.inject_var_to_tree_ui("multi_a", "float", 0)
        studio.inject_var_to_tree_ui("multi_b", "float", 0)
        first_item = studio.debug_trees[0].topLevelItem(0)
        second_item = studio.debug_trees[0].topLevelItem(1)
        with patch.object(studio, "restart_sampling_thread"):
            studio.bind_tree_item_to_channel(0, first_item)
            studio.bind_tree_item_to_channel(0, second_item)
        check(
            studio.ch_paths == ["multi_a", "multi_b"]
            and studio.plot_active_paths[0] == ["multi_a", "multi_b"]
            and studio._curve_color("multi_a") != studio._curve_color("multi_b")
            and "2 条曲线" in studio.lbl_ch1.text(),
            "one display channel can bind and sample multiple variables",
        )
        studio.set_curve_color("multi_a", "#ff453a")
        check(
            studio._curve_color("multi_a") == "#ff453a"
            and studio.db.get_config("curve_color:multi_a") == "#ff453a",
            "curve colors can be changed and persisted per variable",
        )
        studio.main_splitter.setSizes([520, 900])
        studio.plot_splitter.setSizes([300, 200, 100])
        studio.wave_hz_combo.blockSignals(True)
        studio.wave_hz_combo.setCurrentIndex(2)
        studio.wave_hz_combo.blockSignals(False)
        studio.save_workspace_state()
        saved_workspace = json.loads(studio.db.get_config(studio.WORKSPACE_STATE_KEY))
        studio.ch_paths = []
        studio.ch_types = []
        studio.ch_addrs = []
        studio.ch_buffers = []
        studio.ch_time_stamps = []
        studio.plot_active_paths = [[], [], []]
        with patch.object(studio, "restart_sampling_thread"):
            studio.restore_workspace_state()
        check(
            studio.plot_active_paths[0] == ["multi_a", "multi_b"]
            and studio.ch_paths == ["multi_a", "multi_b"]
            and saved_workspace["plot_paths"][0] == ["multi_a", "multi_b"]
            and studio.wave_hz_combo.currentIndex() == 2,
            "workspace restores waveform bindings, layout sizes and sampling settings",
        )
        studio.db.delete_var("multi_a", 0)
        studio.db.delete_var("multi_b", 0)
        studio.debug_trees[0].clear()
        studio.ch_paths = []
        studio.ch_types = []
        studio.ch_addrs = []
        studio.ch_buffers = []
        studio.ch_time_stamps = []
        studio.plot_active_paths = [[], [], []]
        studio.refresh_ui_channel_bar()
        ch2_x_before = studio.lbl_ch2.x()
        studio.lbl_ch1.setText("CH1: -1.23457e+12")
        application.processEvents()
        check(
            all(label.width() == 170 for label in (studio.lbl_ch1, studio.lbl_ch2, studio.lbl_ch3))
            and studio.lbl_ch2.x() == ch2_x_before
            and studio.format_channel_value(-123456789.0) == "-1.23457e+08",
            "channel cards keep fixed positions for negative and wide values",
        )
        reconnect_clicks = []
        studio.btn_reconnect.clicked.disconnect()
        studio.btn_reconnect.clicked.connect(lambda: reconnect_clicks.append(True))
        studio.btn_reconnect.setFocus()
        QTest.keyClick(studio.btn_reconnect, Qt.Key.Key_Space)
        application.processEvents()
        check(
            studio.is_frozen
            and studio.btn_freeze.text() == "继续"
            and not reconnect_clicks,
            "global Space pauses the waveform without activating a focused action button",
        )
        studio.input_name.setText("watch_value")
        studio.input_name.setFocus()
        QTest.keyClick(studio.input_name, Qt.Key.Key_Space)
        application.processEvents()
        check(
            not studio.is_frozen
            and studio.btn_freeze.text() == "暂停"
            and studio.input_name.text() == "watch_value",
            "global Space resumes the waveform and has no text-input side effect",
        )
        studio._set_flash_progress("success")
        check(
            studio.lbl_flash_progress.text() == "DOWNLOAD OK"
            and studio.flash_progress.value() == 100,
            "flash result is shown in the compact bottom progress strip",
        )
        flash_x_before = studio.flash_status_widget.mapTo(studio, QPoint(0, 0)).x()
        splitter_width = max(studio.main_splitter.width(), 1000)
        studio.main_splitter.setSizes([700, max(splitter_width - 700, 300)])
        application.processEvents()
        flash_x_after = studio.flash_status_widget.mapTo(studio, QPoint(0, 0)).x()
        check(
            flash_x_before == flash_x_after
            and studio.flash_status_widget.width() == 270,
            "flash progress remains fixed at the bottom-right when the left pane is resized",
        )
        studio._set_flash_progress("idle")
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
        # Leave margin beyond the 220 ms animation duration for busy CI hosts.
        QTest.qWait(400)
        check(
            studio.btn_reconnect.text() == "重连"
            and studio.btn_reconnect.styleSheet() == original_reconnect_style,
            "button feedback preserves text and restores theme colors",
        )
        studio.ch_paths = ["preview.value"]
        studio.ch_types = ["float"]
        studio.ch_addrs = ["0x20000000"]
        studio.plot_active_paths[0] = ["preview.value"]
        studio.ch_time_stamps = [[0.0, 1000.0, 2000.0]]
        studio.ch_buffers = [[1.0, 3.0, 2.0]]
        studio.start_canvas_auto_adapt()
        studio.update_plot_canvas()
        check(
            studio.canvas_fixed_ranges[0] is not None
            and studio.lbl_status_text.text().startswith("已适配"),
            "waveform fit uses buffered data immediately without a delay timer",
        )
        check(
            studio.plot_legends[0].isVisible()
            and len(studio.plot_legends[0].items) == 1,
            "active waveform variables appear in a compact in-plot legend",
        )
        studio._place_plot_legend_away_from_data(
            0,
            [index / 10.0 for index in range(101)],
            [5.0] * 101,
            (0.0, 10.0),
            (0.0, 5.0),
        )
        check(
            studio.plot_legend_corners[0] == "bottom-left",
            "waveform fit moves the legend away from occupied curve corners",
        )
        fitted_y_range = list(studio.plot_widgets[0].getViewBox().viewRange()[1])
        studio.ch_time_stamps[0].append(3000.0)
        studio.ch_buffers[0].append(100.0)
        studio.update_plot_canvas()
        current_y_range = studio.plot_widgets[0].getViewBox().viewRange()[1]
        check(
            all(abs(current_y_range[index] - fitted_y_range[index]) < 1e-9 for index in (0, 1)),
            "one-shot waveform fit preserves the chosen Y range as new data arrives",
        )
        plot_x, plot_y = studio._display_plot_data(
            list(range(40_000)),
            [99.0 if index == 12_345 else float(index % 17) for index in range(40_000)],
            0,
        )
        check(
            len(plot_x) <= 4_000 and 99.0 in plot_y,
            "waveform rendering bounds point count while preserving peaks",
        )
        studio.db.save_var("shared_value", "uint32", 0, "0x20000000", 0)
        studio.db.save_var("shared_value", "uint32", 0, "0x20000000", 1)
        studio.inject_var_to_tree_ui("shared_value", "uint32", 0)
        studio.ch_paths = ["shared_value"]
        studio.ch_types = ["uint32"]
        studio.ch_addrs = ["0x20000000"]
        studio.ch_buffers = [[]]
        studio.ch_time_stamps = [[]]
        studio.plot_active_paths[0] = ["shared_value"]
        shared_root = studio.debug_trees[0].topLevelItem(0)
        studio.remove_variable_root(shared_root, studio.debug_trees[0])
        check(
            studio.ch_paths[0] == "shared_value",
            "removing one duplicate watch keeps the shared waveform binding",
        )
        studio.db.delete_var("shared_value", 1)
        studio.ch_paths = []
        studio.ch_types = []
        studio.ch_addrs = []
        studio.ch_buffers = []
        studio.ch_time_stamps = []
        studio.plot_active_paths = [[], [], []]
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
        class BriefShutdownThread(QThread):
            def run(self):
                self.msleep(350)

        shutdown_thread = BriefShutdownThread(studio)
        shutdown_thread.finished.connect(shutdown_thread.deleteLater)
        shutdown_thread.start()
        safely_closed_links = []
        class FakeJLink:
            def close(self):
                safely_closed_links.append(True)
        studio.jlink_native_instance = FakeJLink()
        studio.variable_write_worker.update_jlink(studio.jlink_native_instance)
        close_destinations = []
        studio.session_closed.connect(close_destinations.append)
        studio.return_to_configuration()
        application.processEvents()
        check(
            studio.isVisible() and shutdown_thread.isRunning(),
            "window defers destruction while a managed QThread is still running",
        )
        QTest.qWait(700)
        application.processEvents()
        check(
            not studio.isVisible()
            and close_destinations == ["config"]
            and safely_closed_links == [True],
            "window safely returns to configuration after background QThreads finish",
        )
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
        discover_jlink_directories,
        discover_openocd_executables,
        discover_openocd_configs,
        launch_openocd,
        resolve_openocd_config,
        scan_debugger_installations,
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
    with tempfile.TemporaryDirectory() as discovery_dir:
        discovery_root = Path(discovery_dir)
        preferred_openocd = discovery_root / "openocd.exe"
        preferred_openocd.touch()
        preferred_jlink = discovery_root / "JLink"
        preferred_jlink.mkdir()
        dll_name = "JLink_x64.dll" if struct.calcsize("P") == 8 else "JLinkARM.dll"
        (preferred_jlink / dll_name).touch()
        check(
            discover_openocd_executables(str(preferred_openocd))[0] == str(preferred_openocd)
            and discover_jlink_directories(str(preferred_jlink))[0] == str(preferred_jlink),
            "persisted debugger paths take priority over automatic discovery",
        )
        nested_tools = discovery_root / "custom" / "embedded" / "tools"
        nested_tools.mkdir(parents=True)
        global_openocd = nested_tools / "openocd.exe"
        global_openocd.touch()
        global_jlink = nested_tools / "SEGGER"
        global_jlink.mkdir()
        (global_jlink / dll_name).touch()
        with patch("mcu_studio.core.fixed_drive_roots", return_value=[str(discovery_root)]):
            scanned_jlink, scanned_openocd = scan_debugger_installations()
        check(
            str(global_jlink) in scanned_jlink and str(global_openocd) in scanned_openocd,
            "background global scan finds debugger installations outside common paths",
        )
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


def validate_openocd_flash_worker() -> None:
    from mcu_studio import workers
    from mcu_studio.core import ProjectConfig

    config = ProjectConfig()
    config.backend_mode = "OPENOCD"
    config.openocd_exe = str(PROJECT_ROOT / "runtime" / "OpenOCD" / "bin" / "openocd.exe")
    config.interface = "cmsis-dap.cfg"
    config.target = "stm32f0x.cfg"
    captured = {}
    emitted = []

    with tempfile.TemporaryDirectory() as directory:
        firmware = Path(directory) / "firmware build.axf"
        firmware.touch()
        worker = workers.OpenOcdFlashWorker(config, str(firmware))
        worker.finished_signal.connect(
            lambda success, message, ready: emitted.append((success, message, ready))
        )

        def fake_run(command, **kwargs):
            captured["command"] = command
            return subprocess.CompletedProcess(
                command, 0, "** Programming Finished **\n** Verified OK **"
            )

        with patch.object(workers, "stop_managed_openocd") as stop, patch.object(
            workers, "launch_openocd"
        ) as launch, patch.object(workers.subprocess, "run", side_effect=fake_run):
            worker.run()

    flash_command = captured["command"][-1]
    check(
        stop.call_count == 1 and launch.call_count == 1,
        "OpenOCD flash worker stops and restores the managed debug backend",
    )
    check(
        emitted and emitted[0][0] and emitted[0][2]
        and flash_command.startswith('program "')
        and flash_command.endswith('" verify reset exit')
        and "\\" not in flash_command,
        "OpenOCD flash worker programs, verifies and resets the selected AXF",
    )


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
        firmware = root / "firmware.axf"
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
        check(str(firmware) in captured["script"], "J-Link script contains the selected AXF")


def validate_large_watch_database() -> None:
    from mcu_studio.core import LiveWatchDB, application_data_dir

    with tempfile.TemporaryDirectory() as directory, patch.object(sys, "argv", [str(Path(directory) / "main.py")]):
        database = LiveWatchDB()
        records = [
            {"path": f"state.values[{index}]", "type": "uint32_t", "offset": index * 4}
            for index in range(10_000)
        ]
        database.replace_root_vars("state", 0, records)
        check(len(database.get_var_map(0)) == len(records), "large watch lists use one transaction and cached lookup")
        check(
            database.cursor.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "delete",
            "SQLite keeps persistent state in one database file",
        )
        database.conn.close()
        check(
            not Path(f"{database.path}-wal").exists() and not Path(f"{database.path}-shm").exists(),
            "database close leaves no WAL or SHM sidecar files",
        )

    with tempfile.TemporaryDirectory() as directory:
        executable = Path(directory) / "Mcu_Studio.exe"
        sibling_database = Path(directory) / "live_watch_history.db"
        sibling_database.touch()
        with patch.object(sys, "argv", [str(executable)]), patch.object(
            sys, "frozen", True, create=True
        ), patch.dict(os.environ, {"MCU_STUDIO_DATA_DIR": ""}):
            check(
                application_data_dir() == Path(directory),
                "single-file build prefers an existing database beside the EXE",
            )


def validate_configuration_profiles() -> None:
    import sqlite3

    from mcu_studio.core import LiveWatchDB

    with tempfile.TemporaryDirectory() as directory, patch.object(
        sys, "argv", [str(Path(directory) / "main.py")]
    ):
        database = LiveWatchDB()
        profiles = database.list_profiles()
        check(
            profiles == [(1, "主配置", 1)],
            "a permanent main profile is created for every database",
        )
        database.save_config("elf_path", "main.elf")
        database.save_var("main_value", "uint32_t", 0, "main_value", 0)
        secondary_id = database.create_profile("电机控制板")
        check(
            database.active_profile_id == secondary_id
            and database.get_config("elf_path") == ""
            and database.get_all_vars() == [],
            "new profiles start empty and isolated from the main profile",
        )
        database.save_config("elf_path", "motor.elf")
        database.save_config("workspace_state_v1", '{"splitters":[1,2]}')
        database.save_var("motor_value", "float", 0, "motor_value", 0)
        database.cursor.execute(
            "INSERT INTO sample_history(profile_id, session_id, time_ms, variable, value) "
            "VALUES (?, 'motor-session', 1.0, 'motor_value', 2.5)",
            (secondary_id,),
        )
        database.conn.commit()
        database.set_active_profile(1)
        check(
            database.get_config("elf_path") == "main.elf"
            and "main_value" in database.get_var_map(0)
            and "motor_value" not in database.get_var_map(0),
            "switching profiles restores independent project data",
        )
        database.set_active_profile(secondary_id)
        database.clear_profile()
        check(
            any(row[0] == secondary_id for row in database.list_profiles())
            and database.get_config("elf_path") == ""
            and database.get_config("workspace_state_v1") == ""
            and database.get_all_vars() == [],
            "clearing a profile keeps its name while removing UI and variable data",
        )
        check(
            database.get_sample_history("motor_value", "motor-session") == []
            and database.cursor.execute(
                "SELECT COUNT(*) FROM sample_history WHERE profile_id=?", (secondary_id,)
            ).fetchone()[0] == 0,
            "clearing a profile removes waveform history used by CSV export",
        )
        database.delete_profile(secondary_id)
        check(
            database.active_profile_id == 1
            and database.list_profiles() == [(1, "主配置", 1)],
            "deleting a secondary profile returns to the protected main profile",
        )
        try:
            database.delete_profile(1)
        except ValueError:
            main_delete_rejected = True
        else:
            main_delete_rejected = False
        check(main_delete_rejected, "the main profile cannot be deleted")
        database.conn.close()

    with tempfile.TemporaryDirectory() as directory, patch.object(
        sys, "argv", [str(Path(directory) / "main.py")]
    ):
        legacy_path = Path(directory) / "live_watch_history.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.execute(
            "CREATE TABLE variables (name TEXT, type TEXT, offset INTEGER, base_var TEXT, "
            "tab_idx INTEGER DEFAULT 0, PRIMARY KEY(name, tab_idx))"
        )
        legacy.execute("CREATE TABLE config_cache (key TEXT PRIMARY KEY, value TEXT)")
        legacy.execute("CREATE TABLE c_code_cache (id INTEGER PRIMARY KEY, code TEXT)")
        legacy.execute(
            "CREATE TABLE sample_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, time_ms REAL, variable TEXT, value REAL)"
        )
        legacy.execute(
            "INSERT INTO variables VALUES ('legacy_value', 'uint32_t', 0, 'legacy_value', 0)"
        )
        legacy.execute("INSERT INTO config_cache VALUES ('elf_path', 'legacy.elf')")
        legacy.commit()
        legacy.close()
        migrated = LiveWatchDB()
        check(
            migrated.get_config("elf_path") == "legacy.elf"
            and "legacy_value" in migrated.get_var_map(0),
            "legacy single-configuration data migrates into the main profile",
        )
        migrated.conn.close()


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


def validate_combinatorial_matrix() -> None:
    from tools import regression_matrix

    results = {
        "workflows": regression_matrix.validate_user_workflow_matrix(),
        "numeric": regression_matrix.validate_numeric_matrix(),
        "queues": regression_matrix.validate_queue_matrix(),
        "discovery": regression_matrix.validate_discovery_matrix(),
    }
    check(sum(results.values()) == 500, "500 combinatorial user and component scenarios pass")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    validate_compilation()
    validate_imports_and_elf_helpers()
    validate_tcl_protocol()
    validate_openocd_configuration()
    validate_openocd_flash_worker()
    validate_jlink_bitness_and_flash_command()
    validate_large_watch_database()
    validate_configuration_profiles()
    validate_combinatorial_matrix()
    validate_no_second_qt_binding()
    validate_git_whitespace()
    print("\nProject validation passed without accessing hardware.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
