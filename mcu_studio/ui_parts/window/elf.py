"""ELF/AXF parsing, symbol resolution and low-level target memory access."""

from ..common import (
    ElfParseWorker,
    QEventLoop,
    QTimer,
    os,
    receive_tcl_response,
    scalar_size,
    struct,
)
from ..widgets import (
    QMessageBox,
)


class _WindowElfMixin:
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

    def _start_initial_elf_load(self, elf_path):
        """Show the workspace first, then parse the initial ELF asynchronously."""
        if self.elf_reload_in_progress or self.initial_workspace_loaded:
            return
        self.elf_reload_in_progress = True
        self.lbl_status_text.setText("正在后台解析 ELF/AXF…")
        self.btn_elf_browser.setEnabled(False)
        result = {"ok": False, "database": None, "error": ""}
        worker = ElfParseWorker(elf_path, self)
        self.elf_parse_worker = worker

        def receive_result(ok, database, error):
            result.update(ok=bool(ok), database=database, error=str(error or ""))

        def finish_initial_parse():
            self.elf_reload_in_progress = False
            self.btn_elf_browser.setEnabled(True)
            self.elf_parse_worker = None
            worker.deleteLater()
            if result["ok"] and result["database"] is not None:
                self.elf_db = result["database"]
                self.symbol_cache = dict(self.elf_db.symbols)
                print(f"ELF loaded: {len(self.symbol_cache)} symbols, {len(self.elf_db.types)} DWARF variables")
                self.lbl_status_text.setText("ELF/AXF 已加载")
                self._finish_initial_workspace_load(synchronize_elf=True)
            else:
                print(f"❌ ELF 符号表加载异常: {result['error']}")
                self.lbl_status_text.setText("ELF/AXF 解析失败，已保留数据库配置")
                self._finish_initial_workspace_load(synchronize_elf=False)

        worker.result_ready.connect(receive_result)
        worker.finished.connect(finish_initial_parse)
        worker.start()

    def _finish_initial_workspace_load(self, synchronize_elf):
        if self.initial_workspace_loaded:
            return
        self.load_history_from_db(
            synchronize_elf=synchronize_elf,
            remove_missing=synchronize_elf,
        )
        self.restore_workspace_state()
        self.workspace_restore_in_progress = False
        self.initial_workspace_loaded = True
        self.schedule_workspace_save()

    def _parse_elf_in_background(self, elf_path):
        """Parse ELF on a worker while a local event loop keeps the UI responsive."""
        result = {"ok": False, "database": None, "error": "ELF/AXF 解析线程未返回结果"}
        loop = QEventLoop(self)
        worker = ElfParseWorker(elf_path, self)
        self.elf_parse_worker = worker

        def receive_result(ok, database, error):
            result.update(ok=bool(ok), database=database, error=str(error or ""))

        worker.result_ready.connect(receive_result)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()
        worker.wait()
        worker.deleteLater()
        self.elf_parse_worker = None
        return result["ok"], result["database"], result["error"]

    def reload_elf_symbols(self, show_message=True, source="manual", restart_sampling=True):
        """Atomically reload ELF/DWARF and refresh saved trees and channel addresses."""
        if self.elf_reload_in_progress:
            return False, "ELF/AXF 正在后台解析，请稍候"
        elf_path = os.path.abspath(self.config.elf_path) if self.config.elf_path else ""
        if not os.path.isfile(elf_path):
            error = f"找不到 ELF/AXF：\n{elf_path or '(未设置)'}"
            if show_message: QMessageBox.warning(self, "重新提取 ELF/AXF 失败", error)
            return False, error

        self.elf_reload_in_progress = True
        self.lbl_status_text.setText("正在后台解析 ELF/AXF…")
        ok, refreshed, parse_error = self._parse_elf_in_background(elf_path)
        if not ok or refreshed is None:
            self.elf_reload_in_progress = False
            error = f"ELF/AXF 解析失败，已保留原来的变量数据：\n{parse_error}"
            self.lbl_status_text.setText("ELF/AXF 解析失败，已保留原有变量")
            if show_message: QMessageBox.critical(self, "重新提取 ELF/AXF 失败", error)
            return False, error

        self.stop_sampling_thread()

        try:
            self.elf_db = refreshed
            self.symbol_cache = dict(refreshed.symbols)

            for tree in self.debug_trees.values():
                tree.clear()
            self.load_history_from_db(remove_missing=source in ("flash", "manual", "reconnect"))
            unavailable_count = self._rebuild_sampling_channels_from_bindings()

            if hasattr(self, 'adv_parse_dialog') and self.adv_parse_dialog:
                try: self.adv_parse_dialog.replace_elf_database(refreshed)
                except RuntimeError: self.adv_parse_dialog = None
            self.refresh_ui_channel_bar()
            self.reset_canvas_adapt_ranges()
            self.schedule_workspace_save()
            self.elf_last_signature = self._elf_file_signature()
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            self._set_elf_update_pending(False)
        finally:
            self.elf_reload_in_progress = False

        if restart_sampling:
            self.restart_sampling_thread()
        summary = f"已重新提取 ELF/AXF：{len(refreshed.symbols)} 个符号，{len(refreshed.types)} 个 DWARF 变量。"
        if unavailable_count:
            summary += f" {unavailable_count} 个已监视变量暂时不可用，配置已保留。"
        self.lbl_status_text.setText("ELF/AXF 已自动重新提取" if source == "flash" else "ELF/AXF 已重新提取")
        if show_message: QMessageBox.information(self, "重新提取 ELF/AXF 完成", summary)
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
        if signature is None:
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            return
        if signature == self.elf_last_signature:
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            return
        if signature != self.elf_pending_signature:
            self.elf_pending_signature = signature
            self.elf_pending_stable_ticks = 1
            self.lbl_status_text.setText("检测到 ELF/AXF 更新，等待文件写入完成…")
            return
        self.elf_pending_stable_ticks += 1
        if self.elf_pending_stable_ticks < 2:
            return
        # Compilation must not change the active symbol map while the target
        # still runs the previous firmware.  The new ELF becomes authoritative
        # only after a successful flash (or an explicit manual reload).
        self.elf_last_signature = signature
        self.elf_pending_signature = None
        self.elf_pending_stable_ticks = 0
        self._set_elf_update_pending(True)
        self.lbl_status_text.setText("检测到新 ELF/AXF；继续使用旧映射，烧录成功后自动同步")

    def _set_elf_update_pending(self, pending):
        self.elf_update_pending = bool(pending)
        if not hasattr(self, "btn_flash") or self.flash_sequence_active:
            return
        self.btn_flash.setText("烧录*" if self.elf_update_pending else "烧录")
        self.btn_flash.setToolTip(
            "检测到新 ELF/AXF；当前继续按旧映射调试，烧录成功后自动切换并同步变量"
            if self.elf_update_pending else
            "通过当前调试后端烧录 ELF/AXF；只有点击并确认后才会擦写 Flash/复位"
        )

    def type_info_for_path(self, path):
        if path.startswith(("0x", "0X")): return None
        try: return self.elf_db.resolve(path)[1]
        except Exception: return None

    def resolve_saved_variable_address(self, path, offset, base_var):
        """Resolve a saved watch against the current ELF, never a stale cached address."""
        if base_var.startswith(("0x", "0X")):
            return int(base_var, 16) + int(offset)
        try:
            return int(self.elf_db.resolve(path)[0])
        except (KeyError, ValueError, IndexError):
            base_address = self.symbol_cache.get(base_var)
            return None if base_address is None else int(base_address) + int(offset)

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
            with self.jlink_lock:
                return bytes(self.jlink_native_instance.memory_read8(address, size))
        res = self.send_tcl_cmd(f"mdb {hex(address)} {size}")
        if "ERROR" in res.upper() or ":" not in res: raise RuntimeError("链路断开")
        values = []
        for line in res.splitlines():
            if ":" in line:
                values.extend(int(token, 16) & 0xFF for token in line.split(":", 1)[1].split())
        if len(values) < size: raise RuntimeError("OpenOCD 返回的内存字节不足")
        return bytes(values[:size])

    def write_memory_bytes(self, address, data):
        if self.backend_suspended: raise RuntimeError("通信后端已断开")
        if self.config.backend_mode == "JLINK":
            if not self.jlink_native_instance: raise RuntimeError("链路断开")
            with self.jlink_lock:
                self.jlink_native_instance.memory_write8(address, list(data))
            return
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
                    with self.jlink_lock:
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
        except Exception:
            self.openocd_link_alive = False
            if self.tcl_socket:
                try: self.tcl_socket.close()
                except OSError: pass
            self.tcl_socket = None
            QTimer.singleShot(
                0,
                lambda: self._handle_backend_connection_lost("OpenOCD TCL 通信中断"),
            )
            return "ERROR"
