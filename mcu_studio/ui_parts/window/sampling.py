"""Sampling threads, waveform history and the throttled watch-table refresh."""

from ..common import (
    HighSpeedSamplingWorker,
    LowSpeedReadWorker,
    QMutexLocker,
    QTimer,
    QTreeWidgetItem,
    Qt,
    math,
    re,
    scalar_size,
    struct,
)
from ..sliders import (
    rotating_batch,
)


class _WindowSamplingMixin:
    def handle_high_speed_data(self, batch_data):
        history_rows = []
        locker = QMutexLocker(self.buffer_mutex)
        for timestamp, data_packet in batch_data:
            current_rel_time = (timestamp - self.start_time) * 1000.0
            for ch_idx, val in data_packet.items():
                if not 0 <= int(ch_idx) < len(self.ch_buffers):
                    continue
                if int(ch_idx) < len(self.ch_paths):
                    path = self.ch_paths[int(ch_idx)]
                    self.latest_values[path] = val
                    last_saved = self.history_last_saved_ms.get(path, -math.inf)
                    if current_rel_time - last_saved >= 5.0:
                        history_rows.append((self.history_session_id, current_rel_time, path, float(val)))
                        self.history_last_saved_ms[path] = current_rel_time
                self.ch_buffers[ch_idx].append(val)
                self.ch_time_stamps[ch_idx].append(current_rel_time)
        for i in range(len(self.ch_buffers)):
            if len(self.ch_buffers[i]) > 40000:
                self.ch_buffers[i] = self.ch_buffers[i][-25000:]
                self.ch_time_stamps[i] = self.ch_time_stamps[i][-25000:]
        del locker
        if history_rows:
            self.history_writer.submit(history_rows)

    def _history_write_failed(self, error):
        self.lbl_status_text.setToolTip(f"采样历史写入失败：{error}")
        
    def restart_sampling_thread(self):
        if getattr(self, 'elf_reload_in_progress', False): return
        if not self.stop_sampling_thread():
            self.sampling_restart_pending = True
            return
        self.sampling_restart_pending = False
        if self.backend_suspended:
            return
            
        hz_idx = self.wave_hz_combo.currentIndex()
        if hz_idx == 0:
            try: hz = int(self.custom_hz_edit.text().strip())
            except: hz = 300
        else: hz = [300, 500, 1000, 2000, 4000, 8000][hz_idx]
            
        self.custom_hz_edit.setEnabled(hz_idx == 0)
        delay = 1.0 / hz
        active_channels = {
            index: (self.ch_addrs[index], self.ch_types[index], path)
            for index, path in enumerate(self.ch_paths)
            if self.ch_addrs[index]
        }
        if active_channels:
            worker = HighSpeedSamplingWorker(
                self.config,
                active_channels,
                delay,
                self.jlink_native_instance,
                self.elf_db,
                self.jlink_lock,
            )
            self.worker = worker
            worker.data_received.connect(
                lambda batch, source=worker: self.handle_high_speed_data(batch)
                if source is self.worker else None
            )
            worker.link_lost.connect(self._sampling_link_lost)
            worker.start()

    def stop_sampling_thread(self, wait_ms=250):
        low_speed_stopped = self._stop_low_speed_worker(wait_ms)
        worker = getattr(self, 'worker', None)
        self.worker = None
        if not worker:
            return low_speed_stopped
        stopped = worker.stop(wait_ms)
        if not stopped:
            # Keep ownership until the blocked driver call returns.  This is
            # crucial: destroying a still-running QThread can crash Qt.
            self.retired_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._forget_retired_worker(w))
        else:
            worker.deleteLater()
        return stopped and low_speed_stopped

    def _stop_low_speed_worker(self, wait_ms=250):
        worker = self.low_speed_worker
        self.low_speed_worker = None
        self.low_speed_pending = {}
        if not worker:
            return True
        try:
            worker.finished_signal.disconnect(self._finish_low_speed_read)
        except (TypeError, RuntimeError):
            pass
        stopped = worker.stop(wait_ms)
        if not stopped:
            self.retired_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._forget_retired_worker(w))
        return stopped

    def _forget_retired_worker(self, worker):
        if worker in self.retired_workers:
            self.retired_workers.remove(worker)
        worker.deleteLater()
        if (self.sampling_restart_pending and not self.backend_suspended
                and not any(item.isRunning() for item in self.retired_workers)):
            QTimer.singleShot(0, self.restart_sampling_thread)

    def low_speed_tree_refresh(self):
        if self.layout_switching or self.backend_suspended:
            return
        current_tab = self.current_debug_slot()
        active_tabs = {current_tab} if current_tab is not None else set()
        active_tabs.update(
            slot for slot, window in self.detached_debug_windows.items()
            if window.isVisible()
        )
        db_vars_by_tab = {slot: self.db.get_var_map(slot) for slot in active_tabs}
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        with QMutexLocker(self.buffer_mutex):
            channel_values = [values[-1] if values else None for values in self.ch_buffers]
        connected = self.config.backend_mode == "JLINK" or self.openocd_link_alive
        for plot_idx, paths in enumerate(self.plot_active_paths):
            if not paths:
                labels[plot_idx].setText(f"CH{plot_idx + 1}  空闲")
            elif len(paths) > 1:
                labels[plot_idx].setText(f"CH{plot_idx + 1}  {len(paths)} 条曲线")
            else:
                sample_idx = self.ch_paths.index(paths[0]) if paths[0] in self.ch_paths else -1
                value = channel_values[sample_idx] if sample_idx >= 0 else None
                labels[plot_idx].setText(
                    f"CH{plot_idx + 1}: {self.format_channel_value(value)}"
                    if value is not None and connected else f"CH{plot_idx + 1}: 断开"
                )
            labels[plot_idx].setToolTip("\n".join(paths))

        if self.low_speed_worker:
            return

        pending = {}
        requests = {}
        candidates = []

        def collect_item(item, t_idx):
            if item.data(0, self.SLIDER_CONTROL_ROLE):
                return
            tree = self.debug_trees.get(t_idx)
            if not tree:
                return
            cell_widget = tree.itemWidget(item, 2)
            if cell_widget is not None:
                return

            path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
            v_type = item.text(1)
            if "{结构体/数组}" in item.text(2):
                if item.isExpanded():
                    for idx in range(item.childCount()):
                        collect_item(item.child(idx), t_idx)
                return

            rect = tree.visualItemRect(item)
            if not rect.isValid() or rect.bottom() < 0 or rect.top() > tree.viewport().height():
                return

            if path in self.ch_paths:
                channel_index = self.ch_paths.index(path)
                value = channel_values[channel_index]
                if value is not None and connected:
                    for slider_widget in self._inline_sliders_for_path(path):
                        slider_widget.update_from_target(value)
                item.setText(2, f"{value:.4f}" if value is not None and connected else "断开")
                return

            tab_vars = db_vars_by_tab.get(t_idx, {})
            if path not in tab_vars:
                return
            offset, base_var, _v_type = tab_vars[path]
            absolute_address = self.resolve_saved_variable_address(path, offset, base_var)
            if absolute_address is None:
                item.setText(2, "ELF 未找到")
                return

            type_info = self.type_info_for_path(path)
            size = scalar_size(type_info) if type_info is not None else 0
            if not size:
                norm_type = v_type.replace("_t", "").strip().lower()
                size = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
            read_key = (absolute_address, size)
            candidates.append(
                ((t_idx, path), (item, read_key, type_info, v_type, cell_widget),
                 (read_key, absolute_address, size))
            )

        for tab_index in sorted(active_tabs):
            tree = self.debug_trees.get(tab_index)
            if tree and tree.isVisible():
                for item_index in range(tree.topLevelItemCount()):
                    collect_item(tree.topLevelItem(item_index), tab_index)

        if not candidates:
            return
        # A large watch list must not create one huge driver transaction. Only
        # visible top-level scalars and leaves below expanded containers reach
        # this list; rotate through them in bounded batches for fair refreshes.
        selected, self.low_speed_round_robin_offset = rotating_batch(
            candidates, self.low_speed_round_robin_offset, 32
        )
        for pending_key, pending_value, request in selected:
            pending[pending_key] = pending_value
            requests[request[0]] = request
        self.low_speed_pending = pending
        self.low_speed_worker = LowSpeedReadWorker(
            self.config,
            list(requests.values()),
            self.jlink_native_instance,
            self.jlink_lock,
            self,
        )
        self.low_speed_worker.finished_signal.connect(self._finish_low_speed_read)
        self.low_speed_worker.finished.connect(self.low_speed_worker.deleteLater)
        self.low_speed_worker.start()

    @classmethod
    def _format_low_speed_value(cls, data, type_info, v_type):
        if type_info is not None and scalar_size(type_info):
            value = cls.decode_value(data, type_info)
            return f"{value:.4f}" if isinstance(value, float) else str(value)
        norm_type = v_type.replace("_t", "").strip().lower()
        raw = int.from_bytes(data, "little", signed=False)
        if "float" in norm_type and len(data) == 4:
            return f"{struct.unpack('<f', data)[0]:.4f}"
        signed = "int" in norm_type and "uint" not in norm_type
        value = int.from_bytes(data, "little", signed=signed)
        return str(value if signed else raw)

    def _finish_low_speed_read(self, results, error):
        worker = self.low_speed_worker
        self.low_speed_worker = None
        pending = self.low_speed_pending
        self.low_speed_pending = {}
        for (_tab_index, path), (item, read_key, type_info, v_type, _queued_widget) in pending.items():
            if path in self.ch_paths:
                continue
            try:
                tree = item.treeWidget()
                if tree is None:
                    continue
                cell_widget = tree.itemWidget(item, 2)
                data = results.get(read_key)
                if data is None:
                    if error and cell_widget is None:
                        item.setText(2, "断开")
                    continue
                if type_info is not None and scalar_size(type_info):
                    value = self.decode_value(data, type_info)
                    if isinstance(value, (int, float, bool)):
                        self.latest_values[path] = float(value)
                else:
                    normalized = v_type.replace("_t", "").strip().lower()
                    if "float" in normalized and len(data) == 4:
                        self.latest_values[path] = struct.unpack("<f", data)[0]
                    else:
                        signed = "int" in normalized and "uint" not in normalized
                        self.latest_values[path] = int.from_bytes(data, "little", signed=signed)
                for slider_widget in self._inline_sliders_for_path(path):
                    slider_widget.update_from_target(self.latest_values.get(path))
                if cell_widget is None:
                    item.setText(2, self._format_low_speed_value(data, type_info, v_type))
            except (RuntimeError, ValueError, struct.error, OverflowError):
                continue
        if error:
            self._handle_backend_connection_lost(error)

    def inject_var_to_tree_ui(self, path, v_type, tab_idx):
        tree = self.debug_trees.get(tab_idx)
        if not tree: return
        parts = re.findall(r'^[a-zA-Z_]\w*|\.[a-zA-Z_]\w*|\[\d+\]', path)
        if not parts: return
        current_parent = None
        current_path_accumulator = ""

        for i, part in enumerate(parts):
            current_path_accumulator += part
            display_name = part.lstrip('.')  
            found_child = None
            if current_parent is None:
                for idx in range(tree.topLevelItemCount()):
                    if tree.topLevelItem(idx).text(0) == display_name:
                        found_child = tree.topLevelItem(idx); break
            else:
                for idx in range(current_parent.childCount()):
                    if current_parent.child(idx).text(0) == display_name:
                        found_child = current_parent.child(idx); break

            if not found_child:
                child_type = self.db.get_var_type(current_path_accumulator, tab_idx) or "{未知}"
                if child_type == "{未知}" and i < len(parts) - 1: child_type = "struct/array"

                node_info = self.type_info_for_path(current_path_accumulator)
                is_container = ((node_info is not None and not scalar_size(node_info)) or
                                ("[" in child_type and not re.search(r'\[\d+\]$', current_path_accumulator)) or
                                i < len(parts) - 1 or child_type == "struct/array")
                val_display = "{结构体/数组}" if is_container else "读取中..."
                new_item = QTreeWidgetItem([display_name, child_type, val_display])
                new_item.setData(0, Qt.ItemDataRole.UserRole, current_path_accumulator)
                
                if current_parent is None: tree.addTopLevelItem(new_item)
                else: current_parent.addChild(new_item)
                found_child = new_item
            current_parent = found_child
        if current_parent is not None:
            key = f"{tab_idx}:{path}"
            state = self.inline_slider_states.get(key)
            if state and key not in self.inline_slider_widgets:
                self.enable_inline_slider(tree, current_parent, path, state)
