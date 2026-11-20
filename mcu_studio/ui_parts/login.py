"""Connection/profile configuration window shown before the workspace opens."""

from .common import (
    DebuggerDiscoveryWorker,
    FIRMWARE_FILE_FILTER,
    ProjectConfig,
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    __version__,
    default_openocd_executable,
    discover_jlink_directories,
    discover_openocd_configs,
    discover_openocd_executables,
    jlink_dll_path,
    json,
    os,
    pylink,
    re,
    sys,
    validate_openocd_config,
)
from .theme import (
    application_theme_stylesheet,
)
from .widgets import (
    PathComboBox,
    QCheckBox,
    QComboBox,
    QMessageBox,
)
from .dialogs import (
    JLinkDeviceDialog,
)


class LoginConfigDialog(QDialog):
    def __init__(self, db, enable_global_discovery=True):
        super().__init__()
        self.db = db
        self.config = ProjectConfig()
        self.is_connected = False
        self.discovery_worker = None
        self.enable_global_discovery = bool(enable_global_discovery)
        self.setWindowTitle(f"MCU Live Watch Studio V{__version__}")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(760, 600)
        self.resize(900, 680)
        self.init_ui()
        self.load_profiles()
        self.load_saved_config()
        self.toggle_engine_fields()

        self.detect_timer = QTimer(self)
        self.detect_timer.timeout.connect(self.static_env_check)
        self.detect_timer.start(1000)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("切换项目配置；主配置固定保留")
        self.profile_combo.currentIndexChanged.connect(self.profile_changed)
        self.btn_profile_add = QPushButton("+")
        self.btn_profile_add.setObjectName("profileAddButton")
        self.btn_profile_add.setFixedWidth(36)
        self.btn_profile_add.setToolTip("新建配置")
        self.btn_profile_add.clicked.connect(self.create_profile)
        self.btn_profile_delete = QPushButton("×")
        self.btn_profile_delete.setObjectName("profileDeleteButton")
        self.btn_profile_delete.setFixedWidth(36)
        self.btn_profile_delete.setToolTip("删除当前配置；主配置不能删除")
        self.btn_profile_delete.clicked.connect(self.delete_current_profile)
        self.btn_profile_clear = QPushButton("清空配置")
        self.btn_profile_clear.setProperty("commandButton", True)
        self.btn_profile_clear.setFixedWidth(82)
        self.btn_profile_clear.setToolTip("保留配置名称，清除该配置内的工程和工作区数据")
        self.btn_profile_clear.clicked.connect(self.clear_current_profile)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_row.addWidget(self.profile_combo, 1)
        profile_row.addWidget(self.btn_profile_add)
        profile_row.addWidget(self.btn_profile_delete)
        profile_row.addWidget(self.btn_profile_clear)
        form.addRow("项目配置", profile_row)

        appearance_section = QLabel("外观与固件")
        appearance_section.setObjectName("sectionTitle")
        form.addRow(appearance_section)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["深色模式（默认）", "浅色模式"])
        self.theme_combo.currentIndexChanged.connect(self.apply_selected_theme)
        form.addRow("界面主题", self.theme_combo)
        
        self.elf_edit = QLineEdit()
        btn_elf = QPushButton("浏览…")
        btn_elf.setFixedWidth(76)
        btn_elf.clicked.connect(
            lambda: self.select_file(self.elf_edit, "选择 ELF / AXF 固件", FIRMWARE_FILE_FILTER)
        )
        h1 = QHBoxLayout(); h1.setSpacing(6); h1.addWidget(self.elf_edit, 1); h1.addWidget(btn_elf)
        form.addRow("ELF / AXF 固件", h1)

        backend_section = QLabel("调试后端")
        backend_section.setObjectName("sectionTitle")
        form.addRow(backend_section)

        self.driver_mode_combo = QComboBox()
        self.driver_mode_combo.addItems(["J-Link 原生驱动", "ST-Link / CMSIS-DAP（OpenOCD）"])
        self.driver_mode_combo.currentIndexChanged.connect(self.toggle_engine_fields)
        form.addRow("后端类型", self.driver_mode_combo)

        self.lbl_jl_dir = QLabel("J-Link 安装目录")
        self.jlink_dir_edit = PathComboBox()
        self.jlink_dir_edit.activated.connect(lambda _index: self.static_env_check())
        self.jlink_dir_edit.lineEdit().editingFinished.connect(self.static_env_check)
        self.btn_jl_dir = QPushButton("浏览…")
        self.btn_jl_dir.setFixedWidth(76)
        self.btn_jl_dir.clicked.connect(self.select_jlink_dir)
        self.btn_detect_jlink = QPushButton("扫描电脑")
        self.btn_detect_jlink.setProperty("commandButton", True)
        self.btn_detect_jlink.setFixedWidth(88)
        self.btn_detect_jlink.setToolTip("立即检测常见位置，并在后台扫描本机所有固定磁盘")
        self.btn_detect_jlink.clicked.connect(self.auto_detect_jlink)
        self.h_jl_dir = QHBoxLayout(); self.h_jl_dir.setSpacing(6); self.h_jl_dir.addWidget(self.jlink_dir_edit, 1); self.h_jl_dir.addWidget(self.btn_jl_dir); self.h_jl_dir.addWidget(self.btn_detect_jlink)

        self.lbl_jl_dev = QLabel("目标器件")
        self.jlink_device_edit = QLineEdit("Cortex-M0+")
        self.jlink_device_edit.textEdited.connect(lambda text: self.jlink_device_edit.setProperty("connect_device", text.strip()))
        self.btn_jl_device = QPushButton("选择芯片…")
        self.btn_jl_device.setFixedWidth(92)
        self.btn_jl_device.clicked.connect(self.open_jlink_device_selector)
        self.h_jl_device = QHBoxLayout(); self.h_jl_device.setSpacing(6); self.h_jl_device.addWidget(self.jlink_device_edit, 1); self.h_jl_device.addWidget(self.btn_jl_device)

        form.addRow(self.lbl_jl_dir, self.h_jl_dir)
        form.addRow(self.lbl_jl_dev, self.h_jl_device)

        self.lbl_ocd_exe = QLabel("OpenOCD 程序")
        self.openocd_edit = PathComboBox()
        self.openocd_edit.activated.connect(lambda _index: self.refresh_openocd_cfgs())
        self.openocd_edit.lineEdit().editingFinished.connect(self.refresh_openocd_cfgs)
        self.btn_ocd = QPushButton("浏览…")
        self.btn_ocd.setFixedWidth(76)
        self.btn_ocd.clicked.connect(self.select_openocd_executable)
        self.btn_detect_openocd = QPushButton("扫描电脑")
        self.btn_detect_openocd.setProperty("commandButton", True)
        self.btn_detect_openocd.setFixedWidth(88)
        self.btn_detect_openocd.setToolTip("立即检测常见位置，并在后台扫描本机所有固定磁盘")
        self.btn_detect_openocd.clicked.connect(self.auto_detect_openocd)
        self.h_ocd_exe = QHBoxLayout(); self.h_ocd_exe.setSpacing(6); self.h_ocd_exe.addWidget(self.openocd_edit, 1); self.h_ocd_exe.addWidget(self.btn_ocd); self.h_ocd_exe.addWidget(self.btn_detect_openocd)

        self.lbl_ocd_intf = QLabel("探针配置")
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.btn_browse_intf = QPushButton("浏览…")
        self.btn_browse_intf.setFixedWidth(76)
        self.btn_browse_intf.clicked.connect(lambda: self.select_cfg_file(self.interface_combo, "interface"))
        self.h_ocd_intf = QHBoxLayout(); self.h_ocd_intf.setSpacing(6); self.h_ocd_intf.addWidget(self.interface_combo, 1); self.h_ocd_intf.addWidget(self.btn_browse_intf)

        self.lbl_ocd_targ = QLabel("目标配置")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.btn_browse_target = QPushButton("浏览…")
        self.btn_browse_target.setFixedWidth(76)
        self.btn_browse_target.clicked.connect(lambda: self.select_cfg_file(self.target_combo, "target"))
        self.h_ocd_targ = QHBoxLayout(); self.h_ocd_targ.setSpacing(6); self.h_ocd_targ.addWidget(self.target_combo, 1); self.h_ocd_targ.addWidget(self.btn_browse_target)

        form.addRow(self.lbl_ocd_exe, self.h_ocd_exe)
        form.addRow(self.lbl_ocd_intf, self.h_ocd_intf)
        form.addRow(self.lbl_ocd_targ, self.h_ocd_targ)

        self.log_checkbox = QCheckBox("在独立窗口显示 OpenOCD 日志")
        self.lbl_openocd_reset_hint = QLabel("连接或重连 OpenOCD 可能复位 MCU")
        self.lbl_openocd_reset_hint.setObjectName("fieldHint")
        log_field = QWidget()
        log_layout = QVBoxLayout(log_field)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)
        log_layout.addWidget(self.log_checkbox)
        log_layout.addWidget(self.lbl_openocd_reset_hint)
        form.addRow("调试日志", log_field)

        layout.addLayout(form)
        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.lbl_hardware_status = QLabel("正在检查本机调试环境…")
        self.lbl_hardware_status.setStyleSheet("color: #cca700; font-weight: 600;")
        status_layout.addWidget(self.lbl_hardware_status)
        layout.addWidget(status_panel)
        
        btns = QHBoxLayout()
        self.btn_connect = QPushButton("连接并进入工作站")
        self.btn_connect.setObjectName("primaryButton")
        self.btn_connect.setMinimumHeight(48)
        self.btn_connect.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.btn_connect.clicked.connect(self.handle_connect)
        btns.addWidget(self.btn_connect)
        layout.addLayout(btns)
        self.setLayout(layout)

    def load_profiles(self, selected_id=None):
        selected_id = int(selected_id or self.db.active_profile_id)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        selected_index = 0
        for index, (profile_id, name, is_main) in enumerate(self.db.list_profiles()):
            self.profile_combo.addItem(str(name), (int(profile_id), bool(is_main)))
            if int(profile_id) == selected_id:
                selected_index = index
        self.profile_combo.setCurrentIndex(selected_index)
        self.profile_combo.blockSignals(False)
        self.update_profile_actions()

    def current_profile_record(self):
        data = self.profile_combo.currentData()
        return data if isinstance(data, tuple) and len(data) == 2 else (1, True)

    def update_profile_actions(self):
        _profile_id, is_main = self.current_profile_record()
        self.btn_profile_delete.setEnabled(not is_main)

    def profile_changed(self, index):
        if index < 0:
            return
        profile_id, _is_main = self.current_profile_record()
        if int(profile_id) == int(self.db.active_profile_id):
            self.update_profile_actions()
            return
        self.stop_global_discovery()
        self.db.set_active_profile(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()
        self.update_profile_actions()

    def create_profile(self):
        default_name = f"配置 {len(self.db.list_profiles()) + 1}"
        name, accepted = QInputDialog.getText(self, "新建配置", "配置名称", text=default_name)
        if not accepted:
            return
        try:
            profile_id = self.db.create_profile(name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法新建配置", str(exc))
            return
        self.load_profiles(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()

    def delete_current_profile(self):
        profile_id, is_main = self.current_profile_record()
        if is_main:
            QMessageBox.information(self, "主配置", "主配置固定保留，不能删除。")
            return
        name = self.profile_combo.currentText()
        answer = QMessageBox.question(
            self,
            "删除配置",
            f"确定删除“{name}”及其变量、波形和历史数据吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_profile(profile_id)
        self.load_profiles(1)
        self.load_saved_config()
        self.toggle_engine_fields()

    def clear_current_profile(self):
        profile_id, _is_main = self.current_profile_record()
        name = self.profile_combo.currentText()
        answer = QMessageBox.question(
            self,
            "清空配置",
            f"确定清空“{name}”吗？\n\n"
            "配置名称会保留；数据库中的 UI 状态、工程设置、变量、波形绑定和用于 CSV 导出的历史采样将被删除。\n"
            "已经导出到磁盘的 CSV 文件不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_profile(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()
        self.lbl_hardware_status.setText(f"已清空配置：{name}")

    def toggle_engine_fields(self):
        is_jlink = (self.driver_mode_combo.currentIndex() == 0)
        self.jlink_dir_edit.setEnabled(is_jlink)
        self.btn_jl_dir.setEnabled(is_jlink)
        self.btn_detect_jlink.setEnabled(is_jlink)
        self.jlink_device_edit.setEnabled(is_jlink)
        self.btn_jl_device.setEnabled(is_jlink)
        self.openocd_edit.setEnabled(not is_jlink)
        self.btn_ocd.setEnabled(not is_jlink)
        self.btn_detect_openocd.setEnabled(not is_jlink)
        self.interface_combo.setEnabled(not is_jlink)
        self.btn_browse_intf.setEnabled(not is_jlink)
        self.target_combo.setEnabled(not is_jlink)
        self.btn_browse_target.setEnabled(not is_jlink)
        self.log_checkbox.setEnabled(not is_jlink)
        self.lbl_openocd_reset_hint.setEnabled(not is_jlink)
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
                self.lbl_hardware_status.setText("未检测到 pylink 库，请执行 pip install pylink-square")
                self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")
            else:
                try:
                    jlink_dll_path(self.jlink_dir_edit.text().strip())
                    self.lbl_hardware_status.setText("J-Link 原生运行环境与 DLL 位数匹配")
                    self.lbl_hardware_status.setStyleSheet(f"color: {success_color}; font-weight: bold;")
                except Exception as exc:
                    self.lbl_hardware_status.setText(str(exc))
                    self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")
        else:
            try:
                details = validate_openocd_config(self._openocd_form_config())
                if sys.platform == "win32" and self._openocd_uses_jlink(details):
                    self.lbl_hardware_status.setText(
                        "OpenOCD 的 J-Link 后端需要 libusb 驱动；SEGGER 官方驱动请使用原生 J-Link 模式。"
                    )
                    self.lbl_hardware_status.setStyleSheet(f"color: {warning_color}; font-weight: bold;")
                else:
                    self.lbl_hardware_status.setText("OpenOCD、interface 和 target 配置均有效")
                    self.lbl_hardware_status.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            except Exception as exc:
                self.lbl_hardware_status.setText(str(exc))
                self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")

    def _openocd_form_config(self):
        config = ProjectConfig()
        config.backend_mode = "OPENOCD"
        config.openocd_exe = self.openocd_edit.text().strip()
        config.interface = self._combo_config_value(self.interface_combo)
        config.target = self._combo_config_value(self.target_combo)
        config.show_log_window = self.log_checkbox.isChecked()
        return config

    @staticmethod
    def _combo_config_value(combo):
        index = combo.currentIndex()
        text = combo.currentText().strip()
        if index >= 0 and text == combo.itemText(index):
            return str(combo.itemData(index) or text).strip()
        return text

    @staticmethod
    def _set_combo_config_value(combo, value):
        value = str(value or "").strip()
        for index in range(combo.count()):
            if str(combo.itemData(index) or combo.itemText(index)).casefold() == value.casefold():
                combo.setCurrentIndex(index)
                return
        if value: combo.setEditText(value)

    @staticmethod
    def _openocd_uses_jlink(details):
        if os.path.basename(details["interface"]).casefold() == "jlink.cfg":
            return True
        try:
            with open(details["target"], "r", encoding="utf-8", errors="replace") as stream:
                target_text = stream.read(65536)
        except OSError:
            return False
        return bool(re.search(
            r"(?im)^\s*(?:adapter\s+driver|interface)\s+jlink\b|interface[/\\]jlink\.cfg",
            target_text,
        ))

    @staticmethod
    def _friendly_openocd_error(error):
        message = str(error)
        upper = message.upper()
        if "LIBUSB_ERROR_NOT_SUPPORTED" in upper and "NO J-LINK DEVICE FOUND" in upper:
            message += (
                "\n\n当前 J-Link 使用的是 SEGGER 官方 Windows 驱动，原生 J-Link 模式可以正常使用；"
                "OpenOCD 的 J-Link 后端通过 libusb 访问，无法打开这个驱动。"
                "请切换到“J-Link 原生内核驱动”，或改用 DAPLink/ST-Link 运行 OpenOCD。"
                "不建议为了 OpenOCD 替换 J-Link USB 驱动，否则可能导致 SEGGER DLL/J-Link Commander 失效。"
            )
        return message

    def select_file(self, edit, title, flt):
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if path: edit.setText(os.path.normpath(path))

    @staticmethod
    def _cached_path_list(raw_value):
        try:
            values = json.loads(raw_value or "[]")
        except (TypeError, ValueError):
            values = []
        return [str(value) for value in values if isinstance(value, str) and value.strip()]

    @staticmethod
    def _merge_path_choices(combo, paths, selected=None):
        current = str(selected if selected is not None else combo.currentText()).strip()
        candidates = [current]
        candidates.extend(combo.itemText(index) for index in range(combo.count()))
        candidates.extend(paths)
        unique = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = os.path.normpath(os.path.abspath(os.path.expandvars(candidate)))
            key = os.path.normcase(normalized)
            if key not in seen:
                seen.add(key)
                unique.append(normalized)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(unique)
        if current:
            normalized_current = os.path.normpath(os.path.abspath(os.path.expandvars(current)))
            index = combo.findText(normalized_current, Qt.MatchFlag.MatchFixedString)
            combo.setCurrentIndex(index if index >= 0 else 0)
        elif combo.count():
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def start_global_discovery(self):
        if self.discovery_worker and self.discovery_worker.isRunning():
            return
        worker = DebuggerDiscoveryWorker(self)
        self.discovery_worker = worker
        worker.progress_signal.connect(self._global_discovery_progress)
        worker.finished_signal.connect(self._global_discovery_finished)
        worker.finished.connect(lambda source=worker: self._global_discovery_stopped(source))
        worker.start()
        self.lbl_hardware_status.setText("已加载快速检测结果，正在后台扫描本机固定磁盘…")

    def _global_discovery_progress(self, jlink_paths, openocd_paths, visited, drive):
        self._merge_path_choices(self.jlink_dir_edit, jlink_paths)
        self._merge_path_choices(self.openocd_edit, openocd_paths)
        location = f" · {drive}" if drive else ""
        self.lbl_hardware_status.setText(
            f"正在扫描本机调试工具：{visited:,} 个目录{location} · "
            f"J-Link {len(jlink_paths)} · OpenOCD {len(openocd_paths)}"
        )

    def _global_discovery_finished(self, jlink_paths, openocd_paths):
        self._merge_path_choices(self.jlink_dir_edit, jlink_paths)
        self._merge_path_choices(self.openocd_edit, openocd_paths)
        combined_jlink = [self.jlink_dir_edit.itemText(i) for i in range(self.jlink_dir_edit.count())]
        combined_openocd = [self.openocd_edit.itemText(i) for i in range(self.openocd_edit.count())]
        self.db.save_config("discovered_jlink_paths", json.dumps(combined_jlink, ensure_ascii=False))
        self.db.save_config("discovered_openocd_paths", json.dumps(combined_openocd, ensure_ascii=False))
        self.lbl_hardware_status.setText(
            f"全机扫描完成：J-Link {len(combined_jlink)} 个，OpenOCD {len(combined_openocd)} 个；可在下拉框选择"
        )
        self.static_env_check()

    def _global_discovery_stopped(self, worker):
        if self.discovery_worker is worker:
            self.discovery_worker = None
        worker.deleteLater()

    def stop_global_discovery(self):
        worker = self.discovery_worker
        if not worker:
            return True
        worker.requestInterruption()
        stopped = worker.wait(1500)
        if not stopped:
            for signal in (worker.progress_signal, worker.finished_signal, worker.finished):
                try:
                    signal.disconnect()
                except (TypeError, RuntimeError):
                    pass
            app = QApplication.instance()
            if app is not None:
                worker.setParent(app)
            worker.finished.connect(worker.deleteLater)
            self.discovery_worker = None
        return stopped

    def select_openocd_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 openocd.exe", "", "Executable (*.exe)")
        if path:
            normalized = os.path.normpath(path)
            self._merge_path_choices(self.openocd_edit, [normalized], normalized)
            self.refresh_openocd_cfgs()

    def auto_detect_openocd(self):
        saved = self.db.get_config("openocd_exe", "").strip()
        cached = self._cached_path_list(self.db.get_config("discovered_openocd_paths", "[]"))
        matches = discover_openocd_executables(saved, self.openocd_edit.text().strip())
        matches.extend(path for path in cached if os.path.isfile(path) and path not in matches)
        if matches:
            self._merge_path_choices(self.openocd_edit, matches, matches[0])
            self.refresh_openocd_cfgs()
            source = "数据库配置" if saved and os.path.normcase(matches[0]) == os.path.normcase(os.path.abspath(saved)) else "本机搜索"
            self.lbl_hardware_status.setText(f"已从{source}找到 OpenOCD：{matches[0]}")
            self.static_env_check()
        else:
            self.lbl_hardware_status.setText("快速检测未找到 OpenOCD，正在扫描本机固定磁盘…")
        if self.enable_global_discovery:
            self.start_global_discovery()

    def refresh_openocd_cfgs(self):
        """Show common cfg choices; the browse buttons cover every other cfg."""
        executable = self.openocd_edit.text().strip()
        for combo, subfolder in ((self.interface_combo, "interface"), (self.target_combo, "target")):
            current = self._combo_config_value(combo)
            discovered = discover_openocd_configs(executable, subfolder) if executable else []
            preferred = (
                [
                    ("ST-Link", "stlink.cfg"),
                    ("DAPLink / CMSIS-DAP", "cmsis-dap.cfg"),
                    ("J-Link（OpenOCD/libusb）", "jlink.cfg"),
                ]
                if subfolder == "interface"
                else [
                    ("STM32 F1", "stm32f1x.cfg"),
                    ("STM32 F2", "stm32f2x.cfg"),
                    ("STM32 F3", "stm32f3x.cfg"),
                    ("STM32 F4", "stm32f4x.cfg"),
                    ("STM32 H7", "stm32h7x.cfg"),
                ]
            )
            available = {item.casefold(): item for item in discovered}
            combo.blockSignals(True)
            combo.clear()
            for label, config_name in preferred:
                actual = available.get(config_name.casefold())
                if actual: combo.addItem(f"{label}  ({actual})", actual)
            self._set_combo_config_value(combo, current)
            combo.blockSignals(False)
            combo.setToolTip(
                f"显示 {combo.count()} 个常用项；OpenOCD 内共发现 {len(discovered)} 个 cfg，其他型号请点右侧浏览按钮。"
            )

    def select_jlink_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 SEGGER J-Link 安装根目录")
        if path: 
            normalized = os.path.normpath(path)
            self._merge_path_choices(self.jlink_dir_edit, [normalized], normalized)
            self.static_env_check()

    def auto_detect_jlink(self):
        saved = self.db.get_config("jlink_dll_dir", "").strip()
        cached = self._cached_path_list(self.db.get_config("discovered_jlink_paths", "[]"))
        matches = discover_jlink_directories(saved, self.jlink_dir_edit.text().strip())
        for path in cached:
            try:
                jlink_dll_path(path)
            except (OSError, ValueError):
                continue
            if path not in matches:
                matches.append(path)
        if matches:
            self._merge_path_choices(self.jlink_dir_edit, matches, matches[0])
            source = "数据库配置" if saved and os.path.normcase(matches[0]) == os.path.normcase(os.path.abspath(saved)) else "本机搜索"
            self.lbl_hardware_status.setText(f"已从{source}找到 J-Link：{matches[0]}")
            self.static_env_check()
        else:
            self.lbl_hardware_status.setText("快速检测未找到 J-Link，正在扫描本机固定磁盘…")
        if self.enable_global_discovery:
            self.start_global_discovery()

    def open_jlink_device_selector(self):
        dll_dir = self.jlink_dir_edit.text().strip()
        dialog = JLinkDeviceDialog(dll_dir, self.jlink_device_edit.text().strip(), self)
        dialog.device_selected.connect(self.apply_jlink_device_selection)
        dialog.exec()

    def apply_jlink_device_selection(self, display_name, connect_name):
        self.jlink_device_edit.setText(display_name)
        self.jlink_device_edit.setProperty("connect_device", connect_name)
        if connect_name != display_name:
            self.jlink_device_edit.setToolTip(f"XML 扩展器件；实时调试按内核 {connect_name} 连接")
        else:
            self.jlink_device_edit.setToolTip("")

    def select_cfg_file(self, combo, subfolder):
        exe_path = self.openocd_edit.text().strip()
        initial_dir = os.path.join(os.path.dirname(exe_path), "..", "share", "openocd", "scripts", subfolder)
        if not os.path.isdir(initial_dir): initial_dir = ""
        path, _ = QFileDialog.getOpenFileName(self, f"选择自定义 {subfolder} 配置文件", initial_dir, "Config Files (*.cfg)")
        if path: combo.setEditText(os.path.normpath(path))

    def load_saved_config(self):
        self.elf_edit.setText(self.db.get_config("elf_path"))
        self.openocd_edit.clear()
        saved_openocd = self.db.get_config("openocd_exe", "").strip() or default_openocd_executable()
        cached_openocd = self._cached_path_list(self.db.get_config("discovered_openocd_paths", "[]"))
        remembered_openocd = [saved_openocd]
        remembered_openocd.extend(path for path in cached_openocd if os.path.isfile(path))
        bundled_openocd = default_openocd_executable()
        if os.path.isfile(bundled_openocd):
            remembered_openocd.append(bundled_openocd)
        self._merge_path_choices(self.openocd_edit, remembered_openocd, saved_openocd)
        self.refresh_openocd_cfgs()
        saved_jlink = self.db.get_config("jlink_dll_dir", "C:\\Program Files\\SEGGER\\JLink")
        self.jlink_dir_edit.clear()
        cached_jlink = self._cached_path_list(self.db.get_config("discovered_jlink_paths", "[]"))
        remembered_jlink = [saved_jlink]
        for path in cached_jlink:
            try:
                jlink_dll_path(path)
            except (OSError, ValueError):
                continue
            if path not in remembered_jlink:
                remembered_jlink.append(path)
        self._merge_path_choices(self.jlink_dir_edit, remembered_jlink, saved_jlink)
        self.jlink_device_edit.setText(self.db.get_config("jlink_device", "Cortex-M0+"))
        self.jlink_device_edit.setProperty("connect_device", self.db.get_config("jlink_connect_device", self.jlink_device_edit.text()))
        saved_theme = self.db.get_config("theme_mode", "DARK").upper()
        self.theme_combo.setCurrentIndex(1 if saved_theme == "LIGHT" else 0)
        self.apply_selected_theme()
        
        saved_mode = self.db.get_config("backend_mode", "JLINK")
        self.driver_mode_combo.setCurrentIndex(0 if saved_mode == "JLINK" else 1)
        
        saved_intf = self.db.get_config("last_interface_cfg")
        if saved_intf: self._set_combo_config_value(self.interface_combo, saved_intf)
        saved_targ = self.db.get_config("last_target_cfg")
        if saved_targ: self._set_combo_config_value(self.target_combo, saved_targ)
        self.log_checkbox.setChecked(self.db.get_config("show_openocd_log", "0") == "1")

    def handle_connect(self):
        if hasattr(self, 'detect_timer'): self.detect_timer.stop()
        self.config.elf_path = self.elf_edit.text().strip()
        self.config.backend_mode = "JLINK" if self.driver_mode_combo.currentIndex() == 0 else "OPENOCD"
        self.config.theme_mode = "LIGHT" if self.theme_combo.currentIndex() == 1 else "DARK"

        if not os.path.isfile(self.config.elf_path):
            QMessageBox.warning(self, "配置无效", f"找不到 ELF / AXF 文件：\n{self.config.elf_path or '(未设置)'}")
            return

        if self.config.backend_mode == "OPENOCD":
            self.config.openocd_exe = self.openocd_edit.text().strip()
            self.config.interface = self._combo_config_value(self.interface_combo)
            self.config.target = self._combo_config_value(self.target_combo)
            self.config.show_log_window = self.log_checkbox.isChecked()

            try:
                validate_openocd_config(self.config)
            except Exception as exc:
                message = self._friendly_openocd_error(exc)
                self.lbl_hardware_status.setText(f"OpenOCD 配置无效：{message}")
                QMessageBox.critical(self, "OpenOCD 配置无效", message)
                return
        else: 
            jlink_dir = self.jlink_dir_edit.text().strip()
            self.config.jlink_dll_dir = os.path.dirname(jlink_dir) if os.path.isfile(jlink_dir) else jlink_dir
            self.config.jlink_device = self.jlink_device_edit.text().strip()
            self.config.jlink_connect_device = self.jlink_device_edit.property("connect_device") or self.config.jlink_device
            try:
                if pylink is None:
                    raise RuntimeError("缺少 pylink-square，请先安装依赖")
                jlink_dll_path(self.config.jlink_dll_dir)
                if not self.config.jlink_device or not self.config.jlink_connect_device:
                    raise ValueError("J-Link 芯片型号不能为空")
            except Exception as exc:
                self.lbl_hardware_status.setText(f"J-Link 配置无效：{exc}")
                QMessageBox.critical(self, "J-Link 配置无效", str(exc))
                return

        self.db.save_config("elf_path", self.config.elf_path)
        self.db.save_config("backend_mode", self.config.backend_mode)
        self.db.save_config("theme_mode", self.config.theme_mode)
        if self.config.backend_mode == "OPENOCD":
            self.db.save_config("openocd_exe", self.config.openocd_exe)
            self.db.save_config("last_interface_cfg", self.config.interface)
            self.db.save_config("last_target_cfg", self.config.target)
            self.db.save_config("show_openocd_log", "1" if self.config.show_log_window else "0")
        else:
            self.db.save_config("jlink_dll_dir", self.config.jlink_dll_dir)
            self.db.save_config("jlink_device", self.config.jlink_device)
            self.db.save_config("jlink_connect_device", self.config.jlink_connect_device)

        self.is_connected = True
        self.accept()

    def done(self, result):
        if hasattr(self, "detect_timer"):
            self.detect_timer.stop()
        self.stop_global_discovery()
        super().done(result)
