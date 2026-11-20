"""Application shutdown: forced exit, graceful close and bounded retries."""

import tempfile

from ..common import (
    QApplication,
    QThread,
    QTimer,
    __version__,
    os,
    stop_managed_openocd,
    threading,
    time,
)
from ..widgets import (
    QMessageBox,
)


class _WindowCloseMixin:
    def _trace_close(self, message):
        """Append one line to %TEMP%\\mcu_shutdown_trace.log for field reports."""
        try:
            path = os.path.join(tempfile.gettempdir(), "mcu_shutdown_trace.log")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} v{__version__} {message}\n"
                )
        except Exception:
            pass

    def _force_close_from_titlebar(self, event):
        """Disconnect best-effort, then exit without waiting for anything.

        The GUI thread must never wait on the SQLite write lock, a worker thread
        or the vendor DLL here; Windows marks the window "not responding" as soon
        as that happens.  Sampling is flagged to stop, the OpenOCD child process
        is terminated, and the J-Link handle is closed on a daemon thread with a
        350 ms budget.  Anything still busy is released by process exit.
        """
        self._trace_close("force-close: start")
        self.backend_suspended = True
        for timer_name in (
            "timer_table", "timer_plot", "timer_heartbeat", "elf_watch_timer",
            "workspace_save_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        self._trace_close("force-close: timers stopped")

        # Flag the background loops so they stop after their current native call
        # instead of issuing new ones.  Never join them from the GUI thread.
        for attribute in ("worker", "low_speed_worker", "variable_write_worker"):
            background = getattr(self, attribute, None)
            if background is not None:
                setattr(background, "running", False)

        sock = getattr(self, "tcl_socket", None)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            self.tcl_socket = None

        process = getattr(self.config, "openocd_process", None)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        self._trace_close("force-close: backend stopped")

        # Close the probe on a helper thread: JLINKARM_Close can block for a long
        # time when the device was removed or another process grabbed it.
        link = self.jlink_native_instance
        self.jlink_native_instance = None
        if link is not None:
            lock = getattr(self, "jlink_lock", None)

            def _close_link():
                acquired = False
                try:
                    if lock is None:
                        acquired = True
                    else:
                        acquired = lock.acquire(timeout=0.3)
                    if acquired:
                        try:
                            link.close()
                        except Exception:
                            pass
                finally:
                    if acquired and lock is not None:
                        lock.release()

            closer = threading.Thread(
                target=_close_link, daemon=True, name="jlink-close"
            )
            closer.start()
            closer.join(0.35)
        self._trace_close("force-close: jlink released")

        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            app.quit()
        self._trace_close("force-close: exiting")
        if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
            os._exit(0)

    def closeEvent(self, event):
        # Record the decision inputs: a field report of a hung close is
        # otherwise indistinguishable from a slow graceful teardown.
        graceful = bool(getattr(self, "_closing_for_config", False))
        self._trace_close(
            f"closeEvent spontaneous={bool(event.spontaneous())} graceful={graceful} "
            f"flash={self.flash_sequence_active} retries={self._close_retry_count}"
        )
        if not graceful:
            # Title-bar X, Alt+F4, taskbar close, or any internal close that is
            # not the explicit "return to configuration" action: leave now.
            self._close_destination = "exit"
            self._returning_to_config = False
            self._close_retry_count = 0
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
        self._closing_for_config = True
        try:
            self.close()
        finally:
            self._closing_for_config = False
