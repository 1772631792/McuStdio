"""Watch tree actions, channel binding, inline sliders and RAM writes."""

from ..common import (
    QDialog,
    QDoubleValidator,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMutexLocker,
    QPushButton,
    QSize,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    Qt,
    json,
    np,
    scalar_size,
    struct,
)
from ..widgets import (
    InPlaceEditLineEdit,
    QCheckBox,
    QMessageBox,
    RoundedMenu,
)
from ..dialogs import (
    ElfVariableDialog,
)
from ..sliders import (
    InlineVariableSlider,
    automatic_slider_profile,
    variable_write_available,
)


class _WindowVariablesMixin:
    def _debug_scalar_items(self):
        """Return watched scalar tree items grouped by their debug window."""
        groups = []
        for slot in sorted(self.debug_trees):
            tree = self.debug_trees[slot]
            items = []

            def visit(item):
                if item.data(0, self.SLIDER_CONTROL_ROLE):
                    return
                if item.childCount():
                    for child_idx in range(item.childCount()):
                        visit(item.child(child_idx))
                    return
                if "{结构体/数组}" not in item.text(2):
                    items.append(item)

            for top_idx in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(top_idx))
            if items:
                groups.append((slot, self.debug_titles.get(slot, f"调试窗口 {slot + 1}"), items))
        return groups

    def show_context_menu(self, pos):
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.get_current_tree()
        if not tree: return
        item = tree.itemAt(pos)
        if not item: return
            
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path: path = item.text(0)
        
        menu = RoundedMenu(self)
        menu.setStyleSheet(self.popup_menu_style)
        is_container = "{结构体/数组}" in item.text(2)
        slider_key = self._inline_slider_key(tree, path)
        has_inline_slider = bool(slider_key and slider_key in self.inline_slider_states)
        inline_slider = self.inline_slider_widgets.get(slider_key)
        channel_actions = []
        export_action = None
        slider_action = None
        slider_restore_action = None
        if not is_container:
            channel_menu = menu.addMenu("添加到波形通道")
            for channel_idx, title in enumerate(("CH1", "CH2", "CH3")):
                action = channel_menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(path in self.plot_active_paths[channel_idx])
                channel_actions.append(action)
            color_menu = menu.addMenu("曲线颜色")
            color_menu.setIcon(self._color_icon(self._curve_color(path)))
            self._populate_curve_color_menu(color_menu, path)
            menu.addSeparator()
            slider_action = menu.addAction("设置滑条参数…" if inline_slider else "打开滑动调节")
            slider_action.setEnabled(self.variable_write_spec(path, for_slider=True) is not None)
            if has_inline_slider:
                slider_restore_action = menu.addAction("恢复普通数值显示")
            export_action = menu.addAction("导出该变量 CSV…")
            export_action.setEnabled(path in self.ch_paths)
            menu.addSeparator()

        m_del = menu.addAction("删除整组变量及其子成员")
        act = menu.exec(tree.viewport().mapToGlobal(pos))

        if act in channel_actions:
            target_plot = channel_actions.index(act)
            if channel_actions[target_plot].isChecked():
                self.bind_tree_item_to_channel(target_plot, item)
            else:
                self.set_plot_channel_visible(path, target_plot, False)
        elif act == m_del:
            root_item = item
            while root_item.parent(): root_item = root_item.parent()
            self.remove_variable_root(root_item, tree)
        elif export_action is not None and act == export_action:
            self.export_variable_csv(path)
        elif slider_action is not None and act == slider_action:
            if inline_slider:
                self.configure_inline_slider(tree, item, inline_slider)
            else:
                self.enable_inline_slider(tree, item, path)
        elif slider_restore_action is not None and act == slider_restore_action:
            self.disable_inline_slider(tree, item, path)

    def bind_tree_item_to_channel(self, plot_idx, item):
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        v_type = item.text(1)
        all_db_vars = {x[0]: (x[2], x[3]) for x in self.db.get_all_vars()}
        if path not in all_db_vars: return
        offset, base_var = all_db_vars[path]
        absolute_address = self.resolve_saved_variable_address(path, offset, base_var)
        if absolute_address is None: return
        
        locker = QMutexLocker(self.buffer_mutex)
        if path in self.ch_paths:
            sample_idx = self.ch_paths.index(path)
            self.ch_types[sample_idx] = v_type
            self.ch_addrs[sample_idx] = hex(absolute_address)
        else:
            self.ch_paths.append(path)
            self.ch_types.append(v_type)
            self.ch_addrs.append(hex(absolute_address))
            self.ch_buffers.append([])
            self.ch_time_stamps.append([])
        if path not in self.plot_active_paths[plot_idx]:
            self.plot_active_paths[plot_idx].append(path)
        del locker

        self._curve_color(path)
        self.clear_measurements()
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        self.schedule_workspace_save()
        self.restart_sampling_thread()

    def unbind_channel(self, ch_idx, restart=True):
        locker = QMutexLocker(self.buffer_mutex)
        path = self.ch_paths[ch_idx]
        if path:
            for p_idx, p_active in enumerate(self.plot_active_paths):
                if path in p_active: p_active.remove(path)
                if path in self.plot_curves_dict[p_idx]:
                    try:
                        self.plot_legends[p_idx].removeItem(path)
                        self.plot_widgets[p_idx].removeItem(self.plot_curves_dict[p_idx][path])
                        del self.plot_curves_dict[p_idx][path]
                    except: pass
        self.ch_paths.pop(ch_idx)
        self.ch_types.pop(ch_idx)
        self.ch_addrs.pop(ch_idx)
        self.ch_buffers.pop(ch_idx)
        self.ch_time_stamps.pop(ch_idx)
        del locker
        self.clear_measurements()
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        self.schedule_workspace_save()
        if restart:
            self.restart_sampling_thread()

    def refresh_ui_channel_bar(self):
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        for i in range(3):
            paths = self.plot_active_paths[i]
            labels[i].setText(
                f"CH{i+1}  空闲" if not paths
                else (f"CH{i+1}  {paths[0]}" if len(paths) == 1 else f"CH{i+1}  {len(paths)} 条曲线")
            )
            labels[i].setToolTip("\n".join(paths))

    @staticmethod
    def format_channel_value(value):
        """Keep the bottom channel display compact and width-stable."""
        return f"{float(value):.6g}"

    def quick_add_variable(self):
        name = self.input_name.text().strip()
        v_type = self.input_type.currentText()
        if not name: return
        current_tab_idx = self.current_debug_slot()
        if current_tab_idx is None: return

        if name.startswith("0x") or name.startswith("0X"):
            self.db.save_var(name, v_type, 0, name, current_tab_idx)
            self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
            self.input_name.clear(); return

        if name not in self.symbol_cache:
            QMessageBox.warning(self, "提示", f"符号名 '{name}' 未在 ELF 中检索到。")
            return
        if name in self.elf_db.types:
            self.add_elf_variable(name)
        else:
            self.db.save_var(name, v_type, 0, name, current_tab_idx)
            self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
        self.input_name.clear()

    def open_advance_parser_window(self):
        self.adv_parse_dialog = ElfVariableDialog(self.elf_db, self)
        self.adv_parse_dialog.symbol_selected_signal.connect(self.add_elf_variable)
        self.adv_parse_dialog.reload_requested_signal.connect(
            lambda: self.reload_elf_symbols(show_message=True, source="manual")
        )
        self.adv_parse_dialog.show()

    def _inline_slider_key(self, tree, path):
        slot = self.debug_slot_for_tree(tree)
        return f"{slot}:{path}" if slot is not None else ""

    def _save_inline_slider_states(self):
        self.db.save_config(
            self.INLINE_SLIDER_STATE_KEY,
            json.dumps(self.inline_slider_states, ensure_ascii=False, separators=(",", ":")),
        )

    def enable_inline_slider(self, tree, item, path, state=None):
        spec = self.variable_write_spec(path, for_slider=True)
        if spec is None:
            return False
        key = self._inline_slider_key(tree, path)
        if not key:
            return False
        existing = self.inline_slider_widgets.get(key)
        if existing is not None:
            tree.setCurrentItem(item)
            return True
        state = state if isinstance(state, dict) else self.inline_slider_states.get(key)
        widget = InlineVariableSlider(self, path, spec, state, tree)
        widget.settings_requested.connect(
            lambda _path, source_tree=tree, source_item=item, source_widget=widget:
            self.configure_inline_slider(source_tree, source_item, source_widget)
        )
        widget.restore_requested.connect(
            lambda _path, source_tree=tree, source_item=item, variable=path:
            self.disable_inline_slider(source_tree, source_item, variable)
        )
        widget.state_changed.connect(
            lambda _path, next_state, state_key=key: self._update_inline_slider_state(state_key, next_state)
        )
        control_item = QTreeWidgetItem()
        control_item.setData(0, self.SLIDER_CONTROL_ROLE, True)
        parent_item = item.parent()
        if parent_item is None:
            insert_at = tree.indexOfTopLevelItem(item) + 1
            tree.insertTopLevelItem(insert_at, control_item)
        else:
            insert_at = parent_item.indexOfChild(item) + 1
            parent_item.insertChild(insert_at, control_item)
        control_item.setFirstColumnSpanned(True)
        control_item.setSizeHint(0, QSize(0, 78))
        tree.setItemWidget(control_item, 0, widget)
        self.inline_slider_widgets[key] = widget
        self.inline_slider_contexts[key] = (tree, item, control_item)
        tree.setCurrentItem(item)
        self.inline_slider_states[key] = widget.export_state()
        self._save_inline_slider_states()
        return True

    def _update_inline_slider_state(self, key, state):
        self.inline_slider_states[key] = dict(state)
        self._save_inline_slider_states()

    def _remove_inline_slider_widget(self, key):
        widget = self.inline_slider_widgets.pop(key, None)
        context = self.inline_slider_contexts.pop(key, None)
        tree, _item, control_item = context if context is not None else (None, None, None)
        if widget is not None:
            if tree is not None and control_item is not None:
                tree.removeItemWidget(control_item, 0)
            widget.deleteLater()
        if control_item is not None:
            parent_item = control_item.parent()
            if parent_item is not None:
                index = parent_item.indexOfChild(control_item)
                if index >= 0:
                    parent_item.takeChild(index)
            elif tree is not None:
                index = tree.indexOfTopLevelItem(control_item)
                if index >= 0:
                    tree.takeTopLevelItem(index)

    def _inline_sliders_for_path(self, path):
        return [widget for widget in self.inline_slider_widgets.values() if widget.path == path]

    def disable_inline_slider(self, tree, item, path):
        key = self._inline_slider_key(tree, path)
        if key:
            self._remove_inline_slider_widget(key)
            self.inline_slider_states.pop(key, None)
            self._save_inline_slider_states()
        if not item.text(2):
            item.setText(2, "读取中...")

    def configure_inline_slider(self, tree, item, widget):
        if not isinstance(widget, InlineVariableSlider):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"滑条参数 · {widget.path}")
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        form = QFormLayout()
        minimum_edit = QLineEdit(widget._format(widget.minimum))
        maximum_edit = QLineEdit(widget._format(widget.maximum))
        step_edit = QLineEdit(widget._format(widget.step))
        for edit in (minimum_edit, maximum_edit, step_edit):
            edit.setValidator(QDoubleValidator(edit))
        form.addRow("最小值", minimum_edit)
        form.addRow("最大值", maximum_edit)
        form.addRow("步长", step_edit)
        layout.addLayout(form)
        live_write_check = QCheckBox("拖动时实时写入（默认关闭，松手后才写入更安全）")
        live_write_check.setChecked(widget.live_write)
        auto_range_check = QCheckBox("目标值超出范围时自动扩展滑条范围")
        auto_range_check.setChecked(widget.auto_range)
        layout.addWidget(live_write_check)
        layout.addWidget(auto_range_check)
        note = QLabel("加减按钮每次写入一个步进值；设备回读不会打断正在进行的调节。")
        note.setObjectName("fieldHint")
        layout.addWidget(note)
        actions = QHBoxLayout()
        auto = QPushButton("按当前值自动计算")
        cancel = QPushButton("取消")
        apply_button = QPushButton("应用")
        apply_button.setObjectName("primaryButton")
        actions.addWidget(auto)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(apply_button)
        layout.addLayout(actions)

        def fill_auto():
            current = self.latest_values.get(widget.path, widget.value)
            minimum, maximum, step = automatic_slider_profile(
                current, widget.spec.get("info"), widget.spec["type"]
            )
            minimum_edit.setText(widget._format(minimum))
            maximum_edit.setText(widget._format(maximum))
            step_edit.setText(widget._format(step))

        auto.clicked.connect(fill_auto)
        cancel.clicked.connect(dialog.reject)
        apply_button.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            state = {
                "min": float(minimum_edit.text()),
                "max": float(maximum_edit.text()),
                "step": float(step_edit.text()),
                "live_write": live_write_check.isChecked(),
                "auto_range": auto_range_check.isChecked(),
            }
        except ValueError:
            QMessageBox.warning(self, "滑条参数无效", "最小值、最大值和步长必须是数字。")
            return
        if (not all(np.isfinite(value) for value in state.values())
                or state["max"] <= state["min"] or state["step"] <= 0):
            QMessageBox.warning(self, "滑条参数无效", "最大值必须大于最小值，步长必须大于 0。")
            return
        widget.apply_state(state)

    def available_slider_variables(self):
        variables = []
        seen = set()
        for path, _v_type, _offset, _base_var, _tab_idx in self.db.get_all_vars():
            if path in seen:
                continue
            seen.add(path)
            spec = self.variable_write_spec(path, for_slider=True)
            if spec:
                variables.append(path)
        return sorted(variables, key=str.casefold)

    def variable_write_spec(self, path, for_slider=False):
        row = next((item for item in self.db.get_all_vars() if item[0] == path), None)
        if row is None:
            return None
        _path, v_type, offset, base_var, _tab_idx = row
        address = self.resolve_saved_variable_address(path, offset, base_var)
        if address is None:
            return None
        info = self.type_info_for_path(path)
        if info is not None:
            if not scalar_size(info) or info.get("kind") == "pointer" or (for_slider and info.get("kind") == "enum"):
                return None
            size = scalar_size(info)
            encoding = int(info.get("encoding", 0) or 0)
            is_float = encoding == 0x04
            signed = encoding in (0x05, 0x06)
        else:
            normalized = v_type.replace("_t", "").strip().lower()
            size = 1 if "int8" in normalized or "char" in normalized else (2 if "int16" in normalized else 4)
            is_float = "float" in normalized
            signed = "int" in normalized and "uint" not in normalized
        if size not in (1, 2, 4, 8):
            return None
        return {
            "path": path, "type": v_type, "address": int(address), "size": size,
            "info": info, "is_float": is_float, "signed": signed,
        }

    def queue_variable_write(self, path, value):
        if not variable_write_available(
            self.backend_suspended,
            self.flash_sequence_active,
            self.config.backend_mode,
            self.jlink_native_instance,
            self.openocd_link_alive,
        ):
            return 0
        spec = self.variable_write_spec(path)
        if spec is None:
            return 0
        try:
            if spec["info"] is not None:
                encoded_value = value if isinstance(value, str) else self._format_write_value(value, spec["is_float"])
                data = self.encode_value(str(encoded_value), spec["info"])
            elif spec["is_float"]:
                data = struct.pack("<f" if spec["size"] == 4 else "<d", float(value))
            else:
                number = int(round(float(value)))
                data = number.to_bytes(spec["size"], "little", signed=spec["signed"])
        except (ValueError, OverflowError, struct.error):
            return 0
        return self.variable_write_worker.submit(path, spec["address"], data, value)

    @staticmethod
    def _format_write_value(value, is_float):
        return f"{float(value):.17g}" if is_float else str(int(round(float(value))))

    def _variable_write_finished(self, path, sequence, success, error, value):
        if success:
            self.latest_values[path] = value
        elif hasattr(self, "lbl_status_text"):
            self.lbl_status_text.setText(f"变量写入失败：{path}")
            self.lbl_status_text.setToolTip(error)
            self._handle_backend_connection_lost(error)
        self.variable_write_result.emit(path, sequence, success, error, value)

    def add_elf_variable(self, root, target_slot=-1):
        current_tab_idx = self.current_debug_slot() if target_slot is None or int(target_slot) < 0 else int(target_slot)
        if current_tab_idx not in self.debug_trees: return
        try: records = self.elf_db.flatten(root)
        except Exception as exc:
            QMessageBox.warning(self, "ELF 变量解析失败", str(exc)); return
        self.db.replace_root_vars(root, current_tab_idx, records)
        tree = self.debug_trees.get(current_tab_idx)
        if tree: tree.setUpdatesEnabled(False)
        try:
            for record in records:
                self.inject_var_to_tree_ui(record["path"], record["type"], current_tab_idx)
        finally:
            if tree: tree.setUpdatesEnabled(True)

    def process_injected_symbol_from_workspace(self, path, v_type, offset):
        # Kept as a compatibility entry point for old callers; extraction is
        # now always based on the root symbol's DWARF description in the ELF.
        self.add_elf_variable(path.split('.')[0].split('[')[0])

    def handle_tree_item_double_clicked(self, item, column):
        if column != 2: return
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.get_current_tree()
        if tree is None: return
        # An inline slider is restored only through its explicit context-menu
        # action.  Never replace it with the ordinary double-click editor.
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        if self._inline_slider_key(tree, path) in self.inline_slider_states:
            return
        val_text = item.text(2)
        v_type = item.text(1)
        
        if "{结构体/数组}" in val_text or "断开" in val_text or "异常" in val_text: return
        type_info = self.type_info_for_path(path)
        if type_info is not None and not scalar_size(type_info): return

        all_db_paths = [x[0] for x in self.db.get_all_vars()]
        has_children = any(p.startswith(path + ".") or p.startswith(path + "[") for p in all_db_paths if p != path)
        if has_children: return
        
        initial_text = val_text
        if type_info and type_info.get("kind") == "enum" and " (" in initial_text: initial_text = initial_text.rsplit(" (", 1)[0]
        editor = InPlaceEditLineEdit(tree, initial_text, v_type, type_info)
        tree.setItemWidget(item, column, editor)
        editor.setFocus()
        editor.editing_finished_signal.connect(lambda text, source_tree=tree: self.finish_tree_item_editing(item, column, text, source_tree))

    def finish_tree_item_editing(self, item, column, new_text, tree=None):
        tree = tree or self.get_current_tree()
        if tree: tree.removeItemWidget(item, column)
        if not new_text: return 
        
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        v_type = item.text(1)
        all_db_vars = {x[0]: (x[2], x[3]) for x in self.db.get_all_vars()}
        if path not in all_db_vars: return
        offset, base_var = all_db_vars[path]
        absolute_address = self.resolve_saved_variable_address(path, offset, base_var)
        if absolute_address is None: return
        addr_hex = hex(absolute_address)
            
        try:
            type_info = self.type_info_for_path(path)
            if type_info is not None:
                self.encode_value(new_text, type_info)
                value = new_text
            else:
                normalized = v_type.replace("_t", "").strip().lower()
                value = float(new_text) if "float" in normalized else int(float(new_text))
            sequence = self.queue_variable_write(path, value)
            if sequence:
                item.setText(2, new_text)
            else:
                item.setText(2, "写入失败")
        except (ValueError, OverflowError, struct.error):
            item.setText(2, "输入无效")

    def remove_variable_root(self, root_item, tree=None):
        tree = tree or self.get_current_tree()
        current_tab_idx = self.debug_slot_for_tree(tree)
        if current_tab_idx is None: return
        root_name = root_item.text(0)
        removed_paths = set()
        for path, _, _, _, t_idx in list(self.db.get_all_vars()):
            if t_idx == current_tab_idx and (path == root_name or path.startswith(root_name + ".") or path.startswith(root_name + "[")):
                self.db.delete_var(path, current_tab_idx)
                removed_paths.add(path)

        # A waveform binding is global. Keep it alive while the same variable
        # still exists in another debug window.
        remaining_paths = {row[0] for row in self.db.get_all_vars()}
        for path in list(self.ch_paths):
            if path in removed_paths and path not in remaining_paths:
                self.unbind_channel(self.ch_paths.index(path), restart=False)

        state_changed = False
        for path in removed_paths:
            key = f"{current_tab_idx}:{path}"
            if key in self.inline_slider_states:
                self.inline_slider_states.pop(key, None)
                state_changed = True
            if key in self.inline_slider_widgets:
                self._remove_inline_slider_widget(key)
        if state_changed:
            self._save_inline_slider_states()

        if tree:
            index = tree.indexOfTopLevelItem(root_item)
            if index >= 0: tree.takeTopLevelItem(index)
        self.restart_sampling_thread()
