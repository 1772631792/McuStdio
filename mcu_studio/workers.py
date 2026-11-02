"""Background workers for flashing, probe cleanup, and high-speed sampling."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from .core import (
    connect_openocd_tcl,
    decode_openocd_memory_response,
    decode_sample_value,
    jlink_dll_path,
    launch_openocd,
    receive_tcl_response,
    scalar_size,
    stop_managed_openocd,
    validate_openocd_config,
)

try:
    import pylink
except ImportError:
    pylink = None

class ElfFlashWorker(QThread):
    """Run J-Link Commander and reconnect without blocking the Qt GUI."""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, commander, jlink_dir, elf_path, flash_device, connect_device, parent=None):
        super().__init__(parent)
        self.commander = commander
        self.jlink_dir = jlink_dir
        self.elf_path = elf_path
        self.flash_device = flash_device
        self.connect_device = connect_device

    def _reconnect_without_reset(self):
        if pylink is None: return None
        dll_file = jlink_dll_path(self.jlink_dir)
        link = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
        link.open()
        link.set_tif(pylink.enums.JLinkInterfaces.SWD)
        link.connect(self.connect_device, speed=4000)
        return link

    def run(self):
        script_path = ""
        run_commander = self.commander
        command_device = self.flash_device
        success = False
        message = ""
        link = None
        try:
            if not os.path.isfile(self.commander):
                raise FileNotFoundError(f"找不到 J-Link Commander：{self.commander}")
            if not os.path.isfile(self.elf_path):
                raise FileNotFoundError(f"找不到 ELF：{self.elf_path}")
            if not self.flash_device.strip() or not self.connect_device.strip():
                raise ValueError("J-Link 烧录设备名和连接设备名不能为空")
            if any(character in self.elf_path for character in ('"', "\r", "\n")):
                raise ValueError("ELF 路径不能包含双引号或换行符")
            with tempfile.NamedTemporaryFile(
                "w", suffix=".jlink", encoding="utf-8", newline="\n", delete=False
            ) as script:
                script_path = script.name
                # Reset commands are permitted only because this worker is
                # created by an explicitly confirmed Flash button action.
                script.write("r\n")
                script.write(f'loadfile "{self.elf_path}"\n')
                script.write("r\n")
                script.write("qc\n")
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            process_env = os.environ.copy()
            process_env["PATH"] = self.jlink_dir + os.pathsep + process_env.get("PATH", "")
            command = [run_commander, "-device", command_device]
            command.extend([
                "-if", "SWD",
                "-speed", "4000",
                "-autoconnect", "1",
                "-ExitOnError", "1",
                "-CommanderScript", script_path,
            ])
            result = subprocess.run(
                command,
                cwd=self.jlink_dir,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=180,
                creationflags=creationflags,
            )
            output = result.stdout or ""
            failed = result.returncode != 0 or any(
                token in output.lower() for token in (
                    "error:",
                    "failed to",
                    "cannot connect",
                    "no valid device",
                    "unknown to this software version",
                    "could not find",
                )
            )
            if failed:
                message = output[-4000:] or f"JLink.exe 退出码：{result.returncode}"
            else:
                success = True
                message = f"ELF 已烧录并复位运行：\n{self.elf_path}"
        except subprocess.TimeoutExpired:
            message = "J-Link 烧录超过 180 秒，已停止等待。"
        except Exception as exc:
            message = str(exc)
        finally:
            if script_path:
                try: os.remove(script_path)
                except OSError: pass
            try:
                link = self._reconnect_without_reset()
            except Exception as exc:
                message += f"\n\n烧录后非复位重连失败：{exc}"
            self.finished_signal.emit(success, message, link)


class OpenOcdFlashWorker(QThread):
    """Flash an ELF and restore the managed OpenOCD debug instance."""

    finished_signal = pyqtSignal(bool, str, bool)

    def __init__(self, config, elf_path, parent=None):
        super().__init__(parent)
        self.config = config
        self.elf_path = elf_path

    def run(self):
        success = False
        backend_ready = False
        message = ""
        stopped = False
        try:
            details = validate_openocd_config(self.config)
            if not os.path.isfile(self.elf_path):
                raise FileNotFoundError(f"找不到 ELF：{self.elf_path}")
            if any(character in self.elf_path for character in ('"', "\r", "\n")):
                raise ValueError("ELF 路径不能包含双引号或换行符")

            stopped = True
            stop_managed_openocd(self.config)
            openocd_elf = os.path.abspath(self.elf_path).replace("\\", "/")
            command = [details["executable"], *self.config.get_openocd_args()]
            command.extend(("-c", f'program "{openocd_elf}" verify reset exit'))
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            result = subprocess.run(
                command,
                cwd=os.path.dirname(details["executable"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=180,
                creationflags=creationflags,
            )
            output = result.stdout or ""
            output_lower = output.lower()
            failed = result.returncode != 0 or any(
                token in output_lower for token in (
                    "** programming failed **",
                    "verification failed",
                    "failed to write",
                    "failed to erase",
                    "error: flash",
                )
            )
            if failed:
                message = output[-4000:] or f"OpenOCD 退出码：{result.returncode}"
            else:
                success = True
                message = f"ELF 已通过 OpenOCD 烧录、校验并复位运行：\n{self.elf_path}"
        except subprocess.TimeoutExpired:
            message = "OpenOCD 烧录超过 180 秒，已停止等待。"
        except Exception as exc:
            message = str(exc)
        finally:
            if stopped:
                try:
                    launch_openocd(self.config)
                    backend_ready = True
                except Exception as exc:
                    message += f"\n\n烧录后 OpenOCD 调试后端重启失败：{exc}"
            self.finished_signal.emit(success, message, backend_ready)


class BackendCloseWorker(QThread):
    """Release a native probe handle without ever blocking the Qt GUI thread."""

    finished_signal = pyqtSignal(str)

    def __init__(self, link, jlink_lock=None, parent=None):
        super().__init__(parent)
        self.link = link
        self.jlink_lock = jlink_lock or threading.RLock()

    def run(self):
        error = ""
        try:
            if self.link:
                with self.jlink_lock:
                    self.link.close()
        except Exception as exc:
            error = str(exc)
        finally:
            self.link = None
            self.finished_signal.emit(error)


class BackendReconnectWorker(QThread):
    """Recreate either backend without blocking the GUI thread."""

    finished_signal = pyqtSignal(bool, str, object)

    def __init__(self, config, jlink_lock=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.jlink_lock = jlink_lock or threading.RLock()

    def run(self):
        backend = None
        try:
            if self.config.backend_mode == "OPENOCD":
                stop_managed_openocd(self.config)
                launch_openocd(self.config)
                backend = connect_openocd_tcl(self.config, timeout=0.5)
            else:
                if pylink is None:
                    raise RuntimeError("缺少 pylink-square，无法连接 J-Link")
                dll_file = jlink_dll_path(self.config.jlink_dll_dir)
                backend = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                with self.jlink_lock:
                    backend.open()
                    backend.set_tif(pylink.enums.JLinkInterfaces.SWD)
                    backend.connect(self.config.jlink_connect_device, speed=4000)
            self.finished_signal.emit(True, "", backend)
        except Exception as exc:
            if backend:
                try:
                    backend.close()
                except Exception:
                    pass
            self.finished_signal.emit(False, str(exc), None)


class LowSpeedReadWorker(QThread):
    """Read visible table values without blocking the Qt event loop."""

    finished_signal = pyqtSignal(object, str)

    def __init__(self, config, requests, jlink_instance=None, jlink_lock=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.requests = list(requests)
        self.jlink = jlink_instance
        self.jlink_lock = jlink_lock or threading.RLock()
        self.running = True
        self.active_socket = None

    @staticmethod
    def _openocd_bytes(response, size):
        values = []
        for line in response.splitlines():
            if ":" not in line:
                continue
            for token in line.split(":", 1)[1].split():
                values.append(int(token, 16) & 0xFF)
        if len(values) < size:
            raise RuntimeError("OpenOCD 返回的内存字节不足")
        return bytes(values[:size])

    def run(self):
        results = {}
        error = ""
        sock = None
        try:
            if self.config.backend_mode == "OPENOCD":
                sock = connect_openocd_tcl(self.config, timeout=0.3)
                self.active_socket = sock
                for key, address, size in self.requests:
                    if not self.running:
                        break
                    sock.sendall(f"mdb {hex(address)} {size}\x1a".encode("utf-8"))
                    results[key] = self._openocd_bytes(receive_tcl_response(sock), size)
            else:
                if not self.jlink:
                    raise RuntimeError("J-Link 链路未连接")
                for key, address, size in self.requests:
                    if not self.running:
                        break
                    with self.jlink_lock:
                        results[key] = bytes(self.jlink.memory_read8(address, size))
        except Exception as exc:
            error = str(exc)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            self.active_socket = None
            self.finished_signal.emit(results, error)

    def stop(self, wait_ms=250):
        self.running = False
        sock = self.active_socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            self.active_socket = None
        return self.wait(max(0, int(wait_ms)))


class HighSpeedSamplingWorker(QThread):
    data_received = pyqtSignal(list)

    def __init__(self, config, active_channels, sample_delay, jlink_instance=None, elf_db=None, jlink_lock=None):
        super().__init__()
        self.config = config
        self.channels = active_channels 
        self.sample_delay = sample_delay
        self.jlink = jlink_instance
        self.jlink_lock = jlink_lock or threading.RLock()
        self.elf_db = elf_db
        self.running = True
        self.is_link_alive = False  
        self.active_socket = None

    def run(self):
        s = None
        parsed_channels = []
        for ch_idx, (addr_hex, v_type, path) in self.channels.items():
            addr_int = int(addr_hex, 16)
            info = None
            if self.elf_db and not path.startswith(("0x", "0X")):
                try: info = self.elf_db.resolve(path)[1]
                except Exception: pass
            norm_type = v_type.replace("_t", "").strip().lower()
            inferred_width = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
            width = scalar_size(info) if info else inferred_width
            if width not in (1, 2, 4, 8):
                width = inferred_width
            encoding = int(info.get("encoding", 0) or 0) if info else 0
            parsed_channels.append({
                'ch_idx': ch_idx, 'addr': addr_int, 'width': width, 
                'is_float': encoding == 0x04 if info else "float" in norm_type,
                'is_signed': encoding in (0x05, 0x06) if info else "int" in norm_type and "uint" not in norm_type,
                'byte_read': (width == 2 and addr_int % 2 != 0) or (width >= 4 and addr_int % 4 != 0),
            })

        if self.config.backend_mode == "OPENOCD":
            if not parsed_channels:
                use_block_read = False
                merged_cmd_bytes = b""
            else:
                addrs = [c['addr'] for c in parsed_channels]
                block_start = min(addrs) & ~0x3
                block_end = (max(c['addr'] + c['width'] for c in parsed_channels) + 3) & ~0x3
                word_count = (block_end - block_start) // 4
                use_block_read = (
                    (block_end - block_start) <= 128
                    and len(parsed_channels) > 1
                    and all(c['width'] <= 4 and not c['byte_read'] for c in parsed_channels)
                )
                def read_command(channel):
                    width = channel['width']
                    if channel['byte_read']: return f"mdb {hex(channel['addr'])} {width}"
                    if width == 1: return f"mdb {hex(channel['addr'])}"
                    if width == 2: return f"mdh {hex(channel['addr'])}"
                    if width == 4: return f"mdw {hex(channel['addr'])}"
                    return f"mdb {hex(channel['addr'])} {width}"
                merged_cmd = f"mdw {hex(block_start)} {word_count}\x1a" if use_block_read else " ; ".join(read_command(c) for c in parsed_channels) + "\x1a"
                merged_cmd_bytes = merged_cmd.encode('utf-8')

            def connect_openocd():
                return connect_openocd_tcl(self.config, timeout=0.3)

            try:
                s = connect_openocd()
                self.active_socket = s
                self.is_link_alive = True
            except Exception:
                self.is_link_alive = False

        local_batch = []
        last_flush_time = time.time()

        while self.running:
            t_now = time.time()
            packet = {}
            if self.config.backend_mode == "OPENOCD":
                if not merged_cmd_bytes or not self.is_link_alive:
                    if not merged_cmd_bytes:
                        time.sleep(0.04); continue
                    try:
                        if s: s.close()
                        s = connect_openocd()
                        self.active_socket = s
                        self.is_link_alive = True
                    except Exception:
                        self.is_link_alive = False
                        time.sleep(0.25)
                        continue
                try:
                    s.sendall(merged_cmd_bytes)
                    res = receive_tcl_response(s)
                    packet = decode_openocd_memory_response(
                        res,
                        parsed_channels,
                        block_start if use_block_read else None,
                        word_count if use_block_read else None,
                    )
                except Exception:
                    self.is_link_alive = False
                    if s:
                        try: s.close()
                        except Exception: pass
                    s = None
                    self.active_socket = None
                    time.sleep(0.05)
                    continue
            else:
                if self.jlink:
                    try:
                        with self.jlink_lock:
                            for c in parsed_channels:
                                if c['width'] == 1: raw = self.jlink.memory_read8(c['addr'], 1)[0]
                                elif c['width'] == 2: raw = self.jlink.memory_read16(c['addr'], 1)[0]
                                elif c['width'] == 4: raw = self.jlink.memory_read32(c['addr'], 1)[0]
                                else: raw = int.from_bytes(bytes(self.jlink.memory_read8(c['addr'], c['width'])), "little")
                                packet[c['ch_idx']] = decode_sample_value(raw, c)
                        self.is_link_alive = True
                    except Exception:
                        self.is_link_alive = False
                        time.sleep(0.04); continue
                else:
                    self.is_link_alive = False
                    time.sleep(0.04); continue

            if packet: local_batch.append((t_now, packet))
            # Keep Qt event traffic bounded at high sample rates.  A signal
            # carries a batch, so emitting once per 20 samples can otherwise
            # enqueue hundreds of GUI events per second at 8 kHz.
            if len(local_batch) >= 512 or (t_now - last_flush_time > 0.025):
                if self.running and local_batch: self.data_received.emit(local_batch)
                local_batch, last_flush_time = [], t_now
            if self.sample_delay > 0: time.sleep(self.sample_delay)

        if s: 
            try: s.close()
            except: pass
        self.active_socket = None

    def stop(self, wait_ms=250):
        self.running = False
        sock = self.active_socket
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: sock.close()
            except Exception: pass
            self.active_socket = None
        return self.wait(max(0, int(wait_ms)))
