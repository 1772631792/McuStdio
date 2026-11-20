"""Layout, persisted workspace state and saved watch-list synchronisation."""

from ..common import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMutexLocker,
    QPushButton,
    QTimer,
    QTreeWidgetItem,
    QVBoxLayout,
    Qt,
    csv,
    json,
    re,
    scalar_size,
    time,
)
from ..widgets import (
    QComboBox,
    QMessageBox,
)


class _WindowWorkspaceMixin:
    def handle_layout_changed(self, layout_id):
        self.layout_switching = True
        self.current_layout_id = layout_id
        self.db.save_config("last_layout_id", str(layout_id))

        # Keep plot items alive while changing layout. Clearing and rebuilding
        # them caused a visible pause and also discarded crosshair overlays.
        for i, pw in enumerate(self.plot_widgets):
            should_visible = (i == 0) or (i == 1 and layout_id >= 2) or (i == 2 and layout_id == 3)
            pw.setVisible(should_visible)
            if not should_visible:
                if self.v_lines[i]: self.v_lines[i].setVisible(False)
                if self.h_lines[i]: self.h_lines[i].setVisible(False)
                if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                if self.hover_labels[i]: self.hover_labels[i].setVisible(False)
        
        container_height = self.plot_splitter.height() or 600
        if layout_id == 1: self.plot_splitter.setSizes([container_height, 0, 0])
        elif layout_id == 2:
            h = container_height // 2
            self.plot_splitter.setSizes([h, h, 0])
        elif layout_id == 3:
            h = container_height // 3
            self.plot_splitter.setSizes([h, h, h])
        self.schedule_workspace_save()
        QTimer.singleShot(0, self._reset_layout_switching_flag)

    def _reset_layout_switching_flag(self):
        self.layout_switching = False
        self.update_plot_canvas()

    def _sampling_setting_changed(self, *_args):
        self.schedule_workspace_save()
        self.restart_sampling_thread()

    def schedule_workspace_save(self):
        if self.workspace_restore_in_progress or not hasattr(self, "workspace_save_timer"):
            return
        self.workspace_save_timer.start()

    def save_workspace_state(self):
        if self.workspace_restore_in_progress or not hasattr(self, "main_splitter"):
            return
        state = {
            "plot_paths": [list(paths) for paths in self.plot_active_paths],
            "main_splitter": self.main_splitter.sizes(),
            "plot_splitter": self.plot_splitter.sizes(),
            "wave_hz_index": self.wave_hz_combo.currentIndex(),
            "custom_hz": self.custom_hz_edit.text().strip(),
            "current_debug_tab": self.tab_widget.currentIndex(),
            "window_size": [self.width(), self.height()],
        }
        self.db.save_config(
            self.WORKSPACE_STATE_KEY,
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _valid_splitter_sizes(value, expected):
        if not isinstance(value, list) or len(value) != expected:
            return None
        try:
            sizes = [max(0, int(item)) for item in value]
        except (TypeError, ValueError):
            return None
        return sizes if any(sizes) else None

    def restore_workspace_state(self):
        try:
            state = json.loads(self.db.get_config(self.WORKSPACE_STATE_KEY, "{}"))
        except (TypeError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}

        hz_index = state.get("wave_hz_index")
        if isinstance(hz_index, int) and 0 <= hz_index < self.wave_hz_combo.count():
            self.wave_hz_combo.blockSignals(True)
            self.wave_hz_combo.setCurrentIndex(hz_index)
            self.wave_hz_combo.blockSignals(False)
        custom_hz = str(state.get("custom_hz", "30"))
        if custom_hz.isdigit() and 1 <= int(custom_hz) <= 8000:
            self.custom_hz_edit.setText(custom_hz)
        self.custom_hz_edit.setEnabled(self.wave_hz_combo.currentIndex() == 0)

        tab_index = state.get("current_debug_tab")
        if isinstance(tab_index, int) and 0 <= tab_index < self.tab_widget.count():
            self.tab_widget.blockSignals(True)
            self.tab_widget.setCurrentIndex(tab_index)
            self.tab_widget.blockSignals(False)

        window_size = state.get("window_size")
        if isinstance(window_size, list) and len(window_size) == 2:
            try:
                width, height = int(window_size[0]), int(window_size[1])
                if width >= 900 and height >= 600:
                    self.resize(width, height)
            except (TypeError, ValueError):
                pass

        main_sizes = self._valid_splitter_sizes(state.get("main_splitter"), 2)
        plot_sizes = self._valid_splitter_sizes(state.get("plot_splitter"), 3)
        if main_sizes:
            self.main_splitter.setSizes(main_sizes)
        if plot_sizes:
            self.plot_splitter.setSizes(plot_sizes)

        db_rows = {row[0]: row for row in self.db.get_all_vars()}
        restored_paths = state.get("plot_paths", [[], [], []])
        if not isinstance(restored_paths, list):
            restored_paths = [[], [], []]
        self.plot_active_paths = [[], [], []]
        self.ch_paths = []
        self.ch_types = []
        self.ch_addrs = []
        self.ch_buffers = []
        self.ch_time_stamps = []
        for plot_idx in range(3):
            paths = restored_paths[plot_idx] if plot_idx < len(restored_paths) else []
            if not isinstance(paths, list):
                continue
            for path in paths:
                if not isinstance(path, str) or path in self.plot_active_paths[plot_idx]:
                    continue
                self.plot_active_paths[plot_idx].append(path)
                self._curve_color(path)
        self._rebuild_sampling_channels_from_bindings(db_rows)
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        if self.ch_paths:
            self.restart_sampling_thread()

    def _rebuild_sampling_channels_from_bindings(self, db_rows=None):
        """Resolve saved plot bindings without discarding temporarily missing ELF variables."""
        if db_rows is None:
            db_rows = {}
            for row in self.db.get_all_vars():
                db_rows.setdefault(row[0], row)

        configured_paths = set(db_rows)
        for plot_idx, paths in enumerate(self.plot_active_paths):
            self.plot_active_paths[plot_idx] = [path for path in paths if path in configured_paths]
            curves = self.plot_curves_dict[plot_idx]
            for path in list(curves):
                if path not in self.plot_active_paths[plot_idx]:
                    try:
                        self.plot_legends[plot_idx].removeItem(path)
                        self.plot_widgets[plot_idx].removeItem(curves[path])
                    except Exception:
                        pass
                    curves.pop(path, None)

        requested_paths = []
        for paths in self.plot_active_paths:
            for path in paths:
                if path not in requested_paths:
                    requested_paths.append(path)

        valid_paths, valid_types, valid_addrs = [], [], []
        for path in requested_paths:
            row = db_rows.get(path)
            if row is None:
                continue
            _name, var_type, offset, base_var, _tab_idx = row
            address = self.resolve_saved_variable_address(path, offset, base_var)
            if address is None:
                continue
            valid_paths.append(path)
            valid_types.append(var_type)
            valid_addrs.append(hex(address))

        self.ch_paths = valid_paths
        self.ch_types = valid_types
        self.ch_addrs = valid_addrs
        self.ch_buffers = [[] for _path in valid_paths]
        self.ch_time_stamps = [[] for _path in valid_paths]

        valid_set = set(valid_paths)
        for curves in self.plot_curves_dict:
            for path, curve in curves.items():
                if path not in valid_set:
                    curve.setData([], [])
        return len(requested_paths) - len(valid_paths)

    def restore_layout_and_type_configs(self):
        try:
            layout_id = int(self.db.get_config("last_layout_id", "1"))
            if layout_id == 1: self.rad_map1.setChecked(True)
            elif layout_id == 2: self.rad_map2.setChecked(True)
            elif layout_id == 3: self.rad_map3.setChecked(True)
            self.handle_layout_changed(layout_id)
            type_idx = int(self.db.get_config("last_input_type_idx", "7"))
            if type_idx < self.input_type.count(): self.input_type.setCurrentIndex(type_idx)
        except: pass

    def load_history_from_db(self, synchronize_elf=True, remove_missing=False):
        if synchronize_elf:
            self.synchronize_saved_variables_with_elf(remove_missing=remove_missing)
        db_vars = self.db.get_all_vars()
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            if not 0 <= t_idx < 3: continue
            if '.' not in path and '[' not in path: self.inject_var_to_tree_ui(path, v_type, t_idx)
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            if '.' in path or '[' in path: self.inject_var_to_tree_ui(path, v_type, t_idx)
        for key in sorted(self.standalone_debug_variables):
            try:
                slot_text, path = key.split(":", 1)
                slot = int(slot_text)
            except (TypeError, ValueError):
                continue
            row = next(
                (item for item in db_vars if int(item[4]) == slot and item[0] == path),
                None,
            )
            if row is not None:
                self.inject_standalone_variable_ui(path, row[1], slot)

    def _save_standalone_debug_variables(self):
        self.db.save_config(
            "standalone_debug_variables_v1",
            json.dumps(sorted(self.standalone_debug_variables), ensure_ascii=False),
        )

    def inject_standalone_variable_ui(self, path, v_type, tab_idx):
        tree = self.debug_trees.get(int(tab_idx))
        if tree is None:
            return
        for index in range(tree.topLevelItemCount()):
            existing = tree.topLevelItem(index)
            if (existing.data(0, Qt.ItemDataRole.UserRole) == path
                    and existing.data(0, int(Qt.ItemDataRole.UserRole) + 1) == "standalone"):
                return
        type_info = self.type_info_for_path(path)
        value_text = "{结构体/数组}" if type_info is not None and not scalar_size(type_info) else "读取中..."
        item = QTreeWidgetItem([path, v_type, value_text])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, "standalone")
        item.setToolTip(0, "从结构体中拖出的独立变量；可直接右键打开滑动调节")
        tree.addTopLevelItem(item)

    def _rebuild_debug_trees(self):
        for tree in self.debug_trees.values():
            tree.clear()
        self.inline_slider_widgets.clear()
        self.inline_slider_contexts.clear()
        self.load_history_from_db(synchronize_elf=False)
        self.restart_sampling_thread()

    def move_debug_variable(self, path, source_slot, target_slot, is_member=False):
        source_slot, target_slot = int(source_slot), int(target_slot)
        rows = self.db.get_all_vars()
        source_rows = [row for row in rows if int(row[4]) == source_slot]
        selected = next((row for row in source_rows if row[0] == path), None)
        if selected is None or target_slot not in self.debug_trees:
            return

        if is_member:
            # A member remains inside its original structure and is copied as
            # an independently tunable top-level watch in the drop target.
            self.db.save_var(path, selected[1], selected[2], selected[3], target_slot)
            self.standalone_debug_variables.add(f"{target_slot}:{path}")
        else:
            root = selected[3] or path
            moving = [
                row for row in source_rows
                if row[3] == root or row[0] == root
                or row[0].startswith(root + ".") or row[0].startswith(root + "[")
            ]
            if source_slot == target_slot:
                return
            for row in moving:
                self.db.save_var(row[0], row[1], row[2], row[3], target_slot)
                self.db.delete_var(row[0], source_slot)
            moved_keys = {
                key for key in self.standalone_debug_variables
                if key.startswith(f"{source_slot}:")
                and key.split(":", 1)[1] in {row[0] for row in moving}
            }
            self.standalone_debug_variables.difference_update(moved_keys)
            self.standalone_debug_variables.update(
                f"{target_slot}:{key.split(':', 1)[1]}" for key in moved_keys
            )
        self._save_standalone_debug_variables()
        self._rebuild_debug_trees()
        self.lbl_status_text.setText(f"已将 {path} 移到{self.debug_titles[target_slot]}")

    def synchronize_saved_variables_with_elf(self, remove_missing=False):
        """Rebuild every named watch from the active ELF for all debug windows."""
        old_rows = self.db.get_all_vars()
        named_roots = {
            (row[3], int(row[4]))
            for row in old_rows
            if row[3] and not row[3].startswith(("0x", "0X"))
        }
        for root, tab_idx in named_roots:
            if root in self.elf_db.types:
                self.db.replace_root_vars(root, tab_idx, self.elf_db.flatten(root))
            elif remove_missing and root not in self.symbol_cache:
                # A successful post-flash/manual parse is authoritative: a
                # missing variable name means the watch no longer exists.
                self.db.replace_root_vars(root, tab_idx, [])
            # File-watcher reloads are non-destructive because they can observe
            # an intermediate ELF while the linker is still replacing it.

    def export_variable_csv(self, preferred_path=None):
        # Copy paired samples under the existing lock; dialogs and disk I/O never hold it.
        with QMutexLocker(self.buffer_mutex):
            snapshots = [
                (
                    next((plot_idx for plot_idx, paths in enumerate(self.plot_active_paths) if path in paths), 0),
                    path,
                    list(zip(self.ch_time_stamps[i], self.ch_buffers[i])),
                )
                for i, path in enumerate(self.ch_paths) if path
            ]
        merged_snapshots = []
        for channel, path, memory_rows in snapshots:
            database_rows = self.db.get_sample_history(path, self.history_session_id)
            if not database_rows and not memory_rows:
                database_rows = self.db.get_sample_history(path)
            merged = {round(float(t), 6): float(value) for t, value in database_rows}
            merged.update({round(float(t), 6): float(value) for t, value in memory_rows})
            merged_snapshots.append((channel, path, sorted(merged.items())))
        snapshots = merged_snapshots
        if not snapshots:
            QMessageBox.information(self, "导出 CSV", "请先将变量绑定到监视通道并开始采样。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("导出变量数据")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        form = QFormLayout()
        selector = QComboBox()
        for channel, path, rows in snapshots:
            selector.addItem(f"CH{channel + 1} · {path}")
        if preferred_path:
            preferred_index = next(
                (index for index, (_channel, path, _rows) in enumerate(snapshots) if path == preferred_path),
                -1,
            )
            if preferred_index >= 0:
                selector.setCurrentIndex(preferred_index)
        form.addRow("选择变量", selector)
        details = QLabel()
        form.addRow("数据范围", details)
        layout.addLayout(form)
        note = QLabel("导出合并 SQLite 历史与当前内存尾部，包含相对时间（ms）和变量值。\n实时采样和后台历史写入会继续运行。")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        save = QPushButton("保存 CSV…")
        save.setObjectName("primaryButton")
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)
        cancel.clicked.connect(dialog.reject)
        save.clicked.connect(dialog.accept)

        def update_details(index):
            rows = snapshots[index][2]
            details.setText(
                f"{len(rows):,} 个采样点 · {rows[0][0]:.3f}–{rows[-1][0]:.3f} ms"
                if rows else "暂无采样数据，请采样后重新打开导出窗口"
            )
            save.setEnabled(bool(rows))

        selector.currentIndexChanged.connect(update_details)
        update_details(0)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        channel, variable, rows = snapshots[selector.currentIndex()]
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', variable)[:100]
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存 CSV", f"CH{channel + 1}_{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV 文件 (*.csv)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.csv'):
            filename += '.csv'
        try:
            with open(filename, 'w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                writer.writerow(['time_ms', variable])
                writer.writerows(rows)
        except (OSError, csv.Error) as exc:
            QMessageBox.warning(self, "导出失败", f"无法保存 CSV 文件：\n{exc}")
            return
        QMessageBox.information(self, "导出完成", f"已导出 {len(rows):,} 个采样点。\n{filename}")
