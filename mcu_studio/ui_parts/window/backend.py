"""Debugger backend lifecycle: connect, reconnect, flash and heartbeat."""

from ..common import (
    BackendCloseWorker,
    BackendReconnectWorker,
    ET,
    ElfFlashWorker,
    OpenOcdFlashWorker,
    QColor,
    QTimer,
    QVariantAnimation,
    os,
    re,
    socket,
    stop_managed_openocd,
    validate_openocd_config,
)
from ..widgets import (
    QMessageBox,
)


class _WindowBackendMixin:
    def _set_flash_progress(self, state, text="", details=""):
        states = {
            "idle": ("FLASH READY", "#8e8e93", 0),
            "running": ("PROGRAMMING…", "#0a84ff", None),
            "success": ("DOWNLOAD OK", "#34c759", 100),
            "warning": ("FLASH OK · LINK LOST", "#ff9f0a", 100),
            "failed": ("DOWNLOAD FAILED", "#ff453a", 100),
        }
        label, color, value = states[state]
        self.lbl_flash_progress.setText(text or label)
        self.lbl_flash_progress.setStyleSheet(
            f"color: {color}; font-family: 'Consolas'; font-size: 12px; font-weight: 700;"
        )
        track = "#303033" if self.is_dark_theme else "#e5e7eb"
        border = "#48484a" if self.is_dark_theme else "#cbd5e1"
        self.flash_progress.setStyleSheet(
            f"QProgressBar {{ background: {track}; border: 1px solid {border}; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        if value is None:
            self.flash_progress.setRange(0, 0)
        else:
            self.flash_progress.setRange(0, 100)
            self.flash_progress.setValue(value)
        self.flash_status_widget.setToolTip(details)

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
        self.stop_sampling_thread()
        reset_error = ""
        if self.config.backend_mode == "OPENOCD":
            if self.openocd_link_alive and self.tcl_socket:
                if self.send_tcl_cmd("reset run") == "ERROR":
                    reset_error = "OpenOCD 复位命令执行失败"
            else:
                reset_error = "OpenOCD 链路未连接"
        else:
            if self.jlink_native_instance:
                try:
                    with self.jlink_lock:
                        self.jlink_native_instance.reset()
                except Exception as exc:
                    reset_error = str(exc)
            else:
                reset_error = "J-Link 链路未连接"
        if reset_error:
            QMessageBox.warning(self, "复位失败", reset_error)
        self.force_reconnect_engine()

    def flash_elf_by_user(self):
        if self.flash_sequence_active:
            return
        elf_path = os.path.abspath(self.config.elf_path)
        if self.config.backend_mode == "OPENOCD":
            self._flash_elf_with_openocd(elf_path)
            return
        jlink_dir = os.path.dirname(self.config.jlink_dll_dir) if os.path.isfile(self.config.jlink_dll_dir) else self.config.jlink_dll_dir
        commander = os.path.join(jlink_dir, "JLink.exe")
        device = self.config.jlink_device
        _loader_config, loader_error = self.find_flash_loader_config(jlink_dir, device)
        if not os.path.isfile(elf_path):
            QMessageBox.warning(self, "无法烧录", f"找不到 ELF/AXF：\n{elf_path}")
            return
        if not os.path.isfile(commander):
            QMessageBox.warning(self, "无法烧录", f"找不到与 J-Flash 同目录的 JLink.exe：\n{commander}")
            return
        if loader_error:
            QMessageBox.warning(self, "无法烧录", loader_error)
            return
        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("正在烧录…")
        self.lbl_status_text.setText("正在烧录 ELF/AXF（界面仍可响应）")
        self._set_flash_progress("running", details=elf_path)
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.variable_write_worker.update_jlink(None)
        self.pending_flash_args = (
            commander, jlink_dir, elf_path, device, self.config.jlink_connect_device
        )
        if old_link:
            self._close_native_link_async(old_link, next_action="flash")
            return
        self._start_pending_flash()

    def _flash_elf_with_openocd(self, elf_path):
        if not os.path.isfile(elf_path):
            QMessageBox.warning(self, "无法烧录", f"找不到 ELF/AXF：\n{elf_path}")
            return
        try:
            validate_openocd_config(self.config)
        except Exception as exc:
            QMessageBox.warning(self, "无法烧录", str(exc))
            return

        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("正在烧录…")
        self.lbl_status_text.setText("正在通过 OpenOCD 烧录 ELF/AXF（界面仍可响应）")
        self._set_flash_progress("running", details=elf_path)
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        if self.tcl_socket:
            try:
                self.tcl_socket.close()
            except OSError:
                pass
        self.tcl_socket = None
        self.openocd_link_alive = False
        self.openocd_flash_worker = OpenOcdFlashWorker(self.config, elf_path, self)
        self.openocd_flash_worker.finished_signal.connect(self.handle_openocd_flash_finished)
        self.openocd_flash_worker.finished.connect(self.openocd_flash_worker.deleteLater)
        self.openocd_flash_worker.start()

    def _start_pending_flash(self):
        args = getattr(self, 'pending_flash_args', None)
        self.pending_flash_args = None
        if not args:
            return
        self.flash_worker = ElfFlashWorker(
            *args, self
        )
        self.flash_worker.finished_signal.connect(self.handle_flash_finished)
        self.flash_worker.finished.connect(self.flash_worker.deleteLater)
        self.flash_worker.start()

    @staticmethod
    def find_flash_loader_config(jlink_dir, device_name):
        """Validate the selected device's external Flash loader, when declared."""
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
        self.variable_write_worker.update_jlink(link)
        self.flash_sequence_active = False
        self.backend_suspended = False
        self.btn_flash.setText("烧录")
        self.btn_flash.setEnabled(True)
        self.lbl_status_text.setText("烧录完成" if success else "烧录失败")
        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            self._set_flash_progress("success", details=message)
        else:
            self._set_elf_update_pending(self.elf_update_pending)
            self.restart_sampling_thread()
            self._set_flash_progress("failed", details=message)

    def handle_openocd_flash_finished(self, success, message, backend_ready):
        worker = getattr(self, "openocd_flash_worker", None)
        self.openocd_flash_worker = None
        self.flash_sequence_active = False
        self.backend_suspended = not backend_ready
        self.btn_flash.setText("烧录")
        self.btn_flash.setEnabled(True)
        self.btn_reconnect.setEnabled(True)

        if backend_ready:
            self.init_global_link_backend()
            self.variable_write_worker.update_jlink(self.jlink_native_instance)
            backend_ready = self.openocd_link_alive
            self.backend_suspended = not backend_ready
        self.btn_disconnect.setEnabled(backend_ready)
        if backend_ready:
            self.restart_sampling_thread()

        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            if backend_ready:
                self.lbl_status_text.setText("OpenOCD 烧录完成，调试连接已恢复")
                self._set_flash_progress("success", details=message)
            else:
                self.lbl_status_text.setText("烧录完成，OpenOCD 调试连接恢复失败")
                self._set_flash_progress("warning", details=message)
        else:
            self._set_elf_update_pending(self.elf_update_pending)
            self.lbl_status_text.setText("OpenOCD 烧录失败")
            self._set_flash_progress("failed", details=message)

    def force_reconnect_engine(self, initial=False):
        if self.reconnect_worker and self.reconnect_worker.isRunning():
            return
        self.initial_backend_connect = bool(initial)
        self._execute_flash_animation(self.btn_reconnect, "#00b0ff", "")
        self.backend_suspended = True
        self.backend_disconnect_reason = ""
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.btn_mcu_reset.setEnabled(False)
        self.lbl_status_text.setText(f"正在后台重连 {self.config.backend_mode}…")
        if self.config.backend_mode == "OPENOCD":
            if self.tcl_socket: 
                try: self.tcl_socket.close()
                except: pass
            self.tcl_socket = None
            self.openocd_link_alive = False
            self._start_backend_reconnect()
            return

        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.variable_write_worker.update_jlink(None)
        if old_link:
            self._close_native_link_async(old_link, next_action="reconnect")
            self.lbl_status_text.setText("正在后台释放旧 J-Link 后端…")
        else:
            self._start_backend_reconnect()

    def _start_backend_reconnect(self):
        self.reconnect_worker = BackendReconnectWorker(self.config, self.jlink_lock, self)
        self.reconnect_worker.finished_signal.connect(self._backend_reconnect_finished)
        self.reconnect_worker.finished.connect(self.reconnect_worker.deleteLater)
        self.reconnect_worker.start()

    def _backend_reconnect_finished(self, success, error, backend):
        worker = self.reconnect_worker
        self.reconnect_worker = None
        initial_connect = self.initial_backend_connect
        self.initial_backend_connect = False
        if success:
            if self.config.backend_mode == "OPENOCD":
                self.tcl_socket = backend
                self.openocd_link_alive = True
            else:
                self.jlink_native_instance = backend
                self.variable_write_worker.update_jlink(backend)
            self.backend_suspended = False
            self.backend_disconnect_reason = ""
        else:
            self.backend_suspended = True
            self.backend_disconnect_reason = str(error or "通信后端重连失败")
        self.btn_flash.setEnabled(True)
        self.btn_mcu_reset.setEnabled(True)
        reload_ok = None
        reload_message = ""
        if success and not initial_connect:
            reload_ok, reload_message = self.reload_elf_symbols(
                show_message=False,
                source="reconnect",
                restart_sampling=False,
            )
        self._finish_backend_reconnect(reload_ok, reload_message, initial_connect)
        if not success:
            title = f"{self.config.backend_mode} {'连接' if initial_connect else '重连'}失败"
            QMessageBox.critical(self, title, error)

    def disconnect_backend(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录过程中不能断开通信后端。")
            return
        self.backend_suspended = True
        self.backend_disconnect_reason = ""
        self.variable_write_worker.cancel_pending()
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
        self.variable_write_worker.update_jlink(None)
        if old_link:
            self._close_native_link_async(old_link)
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端已断开（未复位 MCU）")
        self.lbl_status_text.setStyleSheet("color: #f0a020; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")

    def _handle_backend_connection_lost(self, reason):
        if self.backend_suspended or self.flash_sequence_active:
            return
        reason = str(reason or "调试后端通信中断")
        self.disconnect_backend()
        self.backend_disconnect_reason = reason
        self.lbl_status_text.setText("检测到调试后端被占用或连接丢失，已自动断开；请手动重连")
        self.lbl_status_text.setToolTip(reason)

    def _sampling_link_lost(self, reason):
        if self.sender() is self.worker:
            self._handle_backend_connection_lost(reason)

    def return_to_configuration(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录尚未完成，请等待结束后再返回配置。")
            return
        if self.reconnect_worker and self.reconnect_worker.isRunning():
            QMessageBox.information(self, "正在重连", "通信后端正在重连，请等待完成后再返回配置。")
            return
        self._close_destination = "config"
        self._returning_to_config = True
        self._closing_for_config = True
        self._close_retry_count = 0
        self.btn_back_config.setEnabled(False)
        self.lbl_status_text.setText("正在安全释放调试连接并返回配置（不会复位 MCU）…")
        try:
            self.close()
        finally:
            self._closing_for_config = False

    def _close_native_link_async(self, link, next_action=""):
        closer = BackendCloseWorker(link, self.jlink_lock, self)
        self.backend_close_workers.append(closer)
        closer.finished_signal.connect(
            lambda error, worker=closer, action=next_action:
            self._native_link_closed(worker, error, action)
        )
        closer.finished.connect(closer.deleteLater)
        closer.start()

    def _native_link_closed(self, worker, error, next_action):
        if worker in self.backend_close_workers:
            self.backend_close_workers.remove(worker)
        if next_action == "reconnect":
            self._start_backend_reconnect()
        elif next_action == "flash":
            self._start_pending_flash()
        elif error:
            self.lbl_status_text.setText(f"后端已停止读写；驱动释放异常：{error}")

    def _finish_backend_reconnect(self, reload_ok=None, reload_message="", initial_connect=False):
        connected = (self.openocd_link_alive if self.config.backend_mode == "OPENOCD"
                     else bool(self.jlink_native_instance))
        self.btn_disconnect.setEnabled(connected)
        self.btn_reconnect.setEnabled(True)
        if connected and initial_connect:
            self.lbl_status_text.setText("通信后端连接完成")
        elif connected and reload_ok is True:
            self.lbl_status_text.setText("通信后端重连完成，已读取最新 ELF/AXF")
        elif connected and reload_ok is False:
            self.lbl_status_text.setText("通信后端重连完成；ELF/AXF 读取失败，保留原变量映射")
        else:
            self.lbl_status_text.setText("通信后端重连完成" if connected else "通信后端重连失败")
        self.lbl_status_text.setToolTip(str(reload_message or ""))
        self.restart_sampling_thread()

    def _execute_flash_animation(self, button, target_color, base_style=""):
        original_style = button.styleSheet()
        anim = QVariantAnimation(button)
        anim.setDuration(220)
        start_color = QColor(target_color).lighter(125)
        end_color = QColor(target_color)
        anim.setStartValue(start_color)
        anim.setEndValue(end_color)
        anim.valueChanged.connect(
            lambda val: button.setStyleSheet(
                f"background-color: {val.name()}; color: #ffffff; border: 1px solid {target_color};"
            )
        )
        anim.finished.connect(lambda: button.setStyleSheet(original_style))
        button._feedback_animation = anim
        anim.start()

    def update_hardware_heartbeat(self):
        is_alive = False
        lost_reason = ""
        if self.backend_suspended:
            is_alive = False
        elif self.config.backend_mode == "OPENOCD":
            process = getattr(self.config, "openocd_process", None)
            process_alive = process is not None and process.poll() is None
            worker_alive = hasattr(self, 'worker') and self.worker and self.worker.isRunning() and self.worker.is_link_alive
            has_worker = hasattr(self, 'worker') and self.worker and self.worker.isRunning()
            is_alive = process_alive and self.openocd_link_alive and (not has_worker or worker_alive)
            if not process_alive:
                self.openocd_link_alive = False
                lost_reason = "OpenOCD 进程已退出，可能由其他下载或调试程序占用了探针"
        else:
            # Do not call into the native DLL from the GUI heartbeat.  Some
            # probe/USB disconnects make target_connected() wait inside the
            # driver and would freeze every widget, including Disconnect.
            worker_alive = bool(self.worker and self.worker.isRunning() and self.worker.is_link_alive)
            is_alive = bool(self.jlink_native_instance) and (not self.worker or worker_alive)
            if not self.jlink_native_instance:
                lost_reason = "J-Link 连接已释放或被其他程序占用"

        if lost_reason and not self.backend_suspended and not self.flash_sequence_active:
            QTimer.singleShot(0, lambda reason=lost_reason: self._handle_backend_connection_lost(reason))

        if is_alive:
            self.heartbeat_state = not self.heartbeat_state
            self.lbl_led.setStyleSheet(f"background-color: {'#00E676' if self.heartbeat_state else '#00b252'}; border-radius: 5px;")
            self.lbl_status_text.setText(f"调试在线 ({self.config.backend_mode})")
            self.lbl_status_text.setStyleSheet(f"color: {self.theme_colors['status_ok']}; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")
        else:
            self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
            reconnecting = bool(self.reconnect_worker and self.reconnect_worker.isRunning())
            if reconnecting:
                status_text = f"正在后台重连 {self.config.backend_mode}…"
            elif self.backend_disconnect_reason:
                status_text = "检测到后端占用或连接丢失，已自动断开；请手动重连"
            else:
                status_text = "通信后端已断开" if self.backend_suspended else "链路断开"
            self.lbl_status_text.setText(status_text)
            self.lbl_status_text.setStyleSheet("color: #ef5350; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")
