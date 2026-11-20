"""Application shutdown: forced exit, graceful close and bounded retries."""

from ..common import (
    QApplication,
    QThread,
    QTimer,
    os,
    socket,
    stop_managed_openocd,
)
from ..widgets import (
    QMessageBox,
)


class _WindowCloseMixin:
    def _force_close_from_titlebar(self, event):
        """Best-effort release followed by an unconditional process exit."""
        try:
            self.save_workspace_state()
        except Exception:
            pass
        self.backend_suspended = True
        for timer_name in (
            "timer_table", "timer_plot", "timer_heartbeat", "elf_watch_timer",
            "workspace_save_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.running = False
            worker.stop(0)
        low_speed = getattr(self, "low_speed_worker", None)
        if low_speed is not None:
            low_speed.stop(0)
        writer = getattr(self, "variable_write_worker", None)
        if writer is not None:
            writer.cancel_pending()
            writer.stop(0)
            writer.update_jlink(None)
        history = getattr(self, "history_writer", None)
        if history is not None:
            history.stop(0)
        for sock_name in ("tcl_socket",):
            sock = getattr(self, sock_name, None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
                setattr(self, sock_name, None)
        process = getattr(self.config, "openocd_process", None)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        # Never call into a possibly wedged J-Link DLL from the title-bar close
        # path. Windows releases the native handle when the process exits.
        self.jlink_native_instance = None
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            app.quit()
        if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
            os._exit(0)

    def closeEvent(self, event):
        # A window-system close (title-bar X, Alt+F4, taskbar) is an explicit
        # exit request and must never depend on backend teardown: one wedged
        # vendor DLL call is enough to keep every graceful retry pending
        # forever.  Route it straight to the forced exit.
        if event.spontaneous():
            self._close_destination = "exit"
            self._returning_to_config = False
            self._close_retry_count = 0
            if self.flash_sequence_active:
                answer = QMessageBox.question(
                    self,
                    "烧录进行中",
                    "烧录尚未结束，强制退出可能导致芯片无法启动。\n\n仍要强制退出吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._force_close_from_titlebar(event)
            return
        # Only the explicit "return to configuration" action may take the
        # graceful path; anything else closes the process unconditionally.
        if not self._returning_to_config:
            self._force_close_from_titlebar(event)
            return
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "Flash 烧录尚未完成，为避免中断导致芯片无法启动，请等待烧录结束。")
            event.ignore()
            return
        if self.reconnect_worker and self.reconnect_worker.isRunning():
            QMessageBox.information(self, "正在重连", "通信后端正在后台重连，请等待完成后再关闭程序。")
            event.ignore()
            return
        self.save_workspace_state()
        if hasattr(self, 'elf_watch_timer'): self.elf_watch_timer.stop()
        for window in list(getattr(self, "detached_debug_windows", {}).values()):
            window._reattaching = True
            window.close()
        if hasattr(self, "detached_debug_windows"):
            self.detached_debug_windows.clear()
        self.backend_suspended = True
        # Keep the total close budget short: these waits run on the GUI thread,
        # and a blocked native read only needs to be retired, not awaited.
        self.stop_sampling_thread(wait_ms=300)
        if hasattr(self, "variable_write_worker"):
            self.variable_write_worker.stop(wait_ms=0)
        if hasattr(self, "history_writer"):
            self.history_writer.stop(wait_ms=500)
        if not self._shutdown_jlink_started and self.jlink_native_instance is not None:
            self._shutdown_jlink_started = True
            old_link = self.jlink_native_instance
            self.jlink_native_instance = None
            if hasattr(self, "variable_write_worker"):
                self.variable_write_worker.update_jlink(None)
            self._close_native_link_async(old_link)
        running_threads = {
            thread for thread in self.findChildren(QThread)
            if thread.isRunning()
        }
        running_threads.update(
            thread for thread in self.retired_workers
            if thread.isRunning()
        )
        if running_threads:
            self._close_retry_count += 1
            if self._close_retry_count > self.CLOSE_RETRY_LIMIT:
                # A worker stuck in the vendor DLL will never stop; force the
                # exit instead of retrying this close forever.
                self._force_close_from_titlebar(event)
                return
            self.lbl_status_text.setText("正在安全停止后台通信，请稍候…")
            event.ignore()
            if not getattr(self, "_close_retry_scheduled", False):
                self._close_retry_scheduled = True
                QTimer.singleShot(250, self._retry_close_after_workers)
            return
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
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if not self._session_closed_emitted:
            self._session_closed_emitted = True
            self.session_closed.emit(self._close_destination)
        event.accept()

    def _retry_close_after_workers(self):
        self._close_retry_scheduled = False
        self.close()
