"""Waveform/FFT rendering, measurements, cursors and plot context menus."""

from ..common import (
    DEFAULT_FFT_SETTINGS,
    QColor,
    QDialog,
    QDoubleValidator,
    QFormLayout,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLineEdit,
    QMutexLocker,
    QPainter,
    QPen,
    QPixmap,
    QPointF,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    Qt,
    WINDOW_LABELS,
    analyze_spectrum,
    bisect,
    html,
    json,
    normalize_fft_settings,
    np,
    pg,
    spectrum_display_values,
    time,
)
from ..widgets import (
    QCheckBox,
    QComboBox,
    QMessageBox,
    RoundedMenu,
)


class _WindowPlotMixin:
    def toggle_freeze(self):
        self.is_frozen = not self.is_frozen
        self.btn_freeze.setChecked(self.is_frozen)
        if self.is_frozen:
            self.btn_freeze.setText("继续")
        else:
            self.btn_freeze.setText("暂停")
            if self.measurement_mode:
                self.measurement_mode = False
                self.btn_measure.setChecked(False)
                self.btn_measure.setText("测量")
                self.btn_measure.setToolTip("冻结波形后依次点击两个采样点，显示横轴与纵轴差值")
                self.clear_measurements()
            for i in range(3):
                if self.v_lines[i]: self.v_lines[i].setVisible(False)
                if self.h_lines[i]: self.h_lines[i].setVisible(False)
                if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                if self.hover_labels[i]: self.hover_labels[i].setVisible(False)

    @staticmethod
    def _padded_y_range(y_min, y_max):
        y_span = float(y_max) - float(y_min)
        if not np.isfinite(y_span) or y_span <= 0:
            padding = max(abs(float(y_min)) * 0.1, 1.0)
        else:
            padding = max(y_span * 0.08, abs(float(y_min)) * 0.02, 0.01)
        return float(y_min) - padding, float(y_max) + padding

    def toggle_measurement_mode(self, enabled):
        """Enter a frozen two-point delta measurement mode."""
        self.measurement_mode = bool(enabled)
        self.btn_measure.setChecked(self.measurement_mode)
        self.clear_measurements()
        if self.measurement_mode:
            if not self.is_frozen:
                self.toggle_freeze()
            for plot_idx in range(3):
                self.v_lines[plot_idx].setVisible(False)
                self.h_lines[plot_idx].setVisible(False)
                self.hover_dots[plot_idx].setVisible(False)
                self.hover_labels[plot_idx].setVisible(False)
            self.btn_measure.setText("测量中")
            self.btn_measure.setToolTip("在曲线上依次点击两个采样点；第三次点击会开始下一次测量")
            self.lbl_measurement_result.setText("测量：请点击第一个采样点")
        else:
            self.btn_measure.setText("测量")
            self.btn_measure.setToolTip("冻结波形后依次点击两个采样点，显示横轴与纵轴差值")
            self.lbl_measurement_result.setText("测量：未开启")

    def clear_measurements(self):
        self.measurement_points = [[], [], []]
        for line in getattr(self, "measurement_lines", []):
            line.setData([], [])
        for dots in getattr(self, "measurement_dots", []):
            dots.setData([], [])
        if hasattr(self, "lbl_measurement_result"):
            self.lbl_measurement_result.setText(
                "测量：请点击第一个采样点" if self.measurement_mode else "测量：未开启"
            )

    def _nearest_visible_sample(self, plot_idx, scene_pos, max_distance=34.0):
        """Return the visible sample closest to a scene click in screen pixels."""
        pw = self.plot_widgets[plot_idx]
        view_box = pw.getViewBox()
        x_mouse = view_box.mapSceneToView(scene_pos).x()
        candidates_to_map = []
        with QMutexLocker(self.buffer_mutex):
            for path in self.plot_active_paths[plot_idx]:
                try:
                    channel_idx = self.ch_paths.index(path)
                except ValueError:
                    continue
                timestamps = self.ch_time_stamps[channel_idx]
                values = self.ch_buffers[channel_idx]
                count = min(len(timestamps), len(values))
                if not count:
                    continue
                index = bisect.bisect_left(timestamps, x_mouse, 0, count)
                candidates = {max(0, min(count - 1, index)), max(0, min(count - 1, index - 1))}
                for candidate in candidates:
                    x_value = float(timestamps[candidate])
                    y_value = float(values[candidate])
                    if not np.isfinite(x_value) or not np.isfinite(y_value):
                        continue
                    candidates_to_map.append((x_value, y_value, plot_idx, path))
        best = None
        best_distance_sq = float(max_distance) ** 2
        for candidate in candidates_to_map:
            sample_scene = view_box.mapViewToScene(QPointF(candidate[0], candidate[1]))
            distance_sq = ((sample_scene.x() - scene_pos.x()) ** 2
                           + (sample_scene.y() - scene_pos.y()) ** 2)
            if distance_sq <= best_distance_sq:
                best_distance_sq = distance_sq
                best = candidate
        return best

    def handle_measurement_click(self, event, plot_idx):
        if (not self.measurement_mode or self.layout_switching
                or self.plot_modes[plot_idx] != "time"
                or event.button() != Qt.MouseButton.LeftButton):
            return
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible():
            return
        point = self._nearest_visible_sample(plot_idx, event.scenePos())
        if point is None:
            return
        points = self.measurement_points[plot_idx]
        if len(points) >= 2:
            points.clear()
        points.append(point)
        self._render_measurement(plot_idx)
        event.accept()

    def _render_measurement(self, plot_idx):
        points = self.measurement_points[plot_idx]
        line = self.measurement_lines[plot_idx]
        dots = self.measurement_dots[plot_idx]
        if not points:
            line.setData([], [])
            dots.setData([], [])
            return
        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        brushes = [pg.mkBrush(self._curve_color(point[3])) for point in points]
        dots.setData(x_values, y_values, brush=brushes)
        if len(points) == 1:
            line.setData([], [])
            self.lbl_measurement_result.setText(
                f"P1 · CH{points[0][2] + 1}   t {points[0][0]:.2f} ms   y {points[0][1]:.6g}"
            )
            self.lbl_measurement_result.setToolTip(points[0][3])
            return
        first, second = points
        delta_x = second[0] - first[0]
        delta_y = second[1] - first[1]
        line.setData(x_values, y_values)
        self.lbl_measurement_result.setText(
            f"Δt {delta_x:+.2f} ms    ΔY {delta_y:+.6g}"
        )
        self.lbl_measurement_result.setToolTip(f"P1: {first[3]}\nP2: {second[3]}")

    def _save_fft_state(self):
        state = {
            "modes": list(self.plot_modes),
            "settings": [normalize_fft_settings(item) for item in self.fft_settings],
        }
        self.db.save_config(
            self.FFT_STATE_KEY,
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        )

    def _apply_plot_mode_visuals(self, plot_idx):
        if plot_idx >= len(getattr(self, "plot_widgets", [])):
            return
        frequency_mode = self.plot_modes[plot_idx] == "fft"
        plot = self.plot_widgets[plot_idx]
        plot.setLabel('bottom', '频率 (Hz)' if frequency_mode else '时间 (ms)')
        if frequency_mode:
            scale = normalize_fft_settings(self.fft_settings[plot_idx])["scale"]
            labels = {
                "percent": "相对基波 (%)",
                "amplitude": "幅值",
                "dbc": "相对基波 (dBc)",
            }
            plot.setLabel('left', labels[scale])
        else:
            plot.setLabel('left', '')
        self.fft_summary_items[plot_idx].setVisible(frequency_mode)
        self.fft_harmonic_markers[plot_idx].setVisible(frequency_mode)
        for overlay in (self.v_lines[plot_idx], self.h_lines[plot_idx],
                        self.hover_dots[plot_idx], self.hover_labels[plot_idx]):
            overlay.setVisible(False)

    def set_plot_mode(self, plot_idx, mode):
        if not 0 <= int(plot_idx) < 3 or mode not in ("time", "fft"):
            return
        if self.plot_modes[plot_idx] == mode:
            return
        if mode == "fft" and self.measurement_mode:
            self.toggle_measurement_mode(False)
        self.plot_modes[plot_idx] = mode
        self.hover_last_point[plot_idx] = None
        self.canvas_fixed_ranges[plot_idx] = None
        self.fft_last_update[plot_idx] = 0.0
        self.fft_results[plot_idx] = {}
        self._apply_plot_mode_visuals(plot_idx)
        self._save_fft_state()
        self.schedule_workspace_save()
        self.update_plot_canvas(force=True)

    def _position_fft_summary(self, plot_idx):
        if (plot_idx >= len(getattr(self, "fft_summary_items", []))
                or self.plot_modes[plot_idx] != "fft"):
            return
        try:
            x_range, y_range = self.plot_widgets[plot_idx].getViewBox().viewRange()
            self.fft_summary_items[plot_idx].setPos(x_range[1], y_range[1])
        except (IndexError, RuntimeError):
            pass

    def configure_fft(self, plot_idx):
        settings = normalize_fft_settings(self.fft_settings[plot_idx])
        dialog = QDialog(self)
        dialog.setWindowTitle(f"CH{plot_idx + 1} · FFT 参数")
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(13)
        form = QFormLayout()

        window_combo = QComboBox()
        for key, label in WINDOW_LABELS.items():
            window_combo.addItem(label, key)
        window_combo.setCurrentIndex(max(0, window_combo.findData(settings["window"])))
        size_combo = QComboBox()
        size_combo.addItem("自动（按已有数据取 2 的幂）", 0)
        for size in (128, 256, 512, 1024, 2048, 4096, 8192):
            size_combo.addItem(f"{size} 点", size)
        size_combo.setCurrentIndex(max(0, size_combo.findData(settings["fft_size"])))
        scale_combo = QComboBox()
        scale_combo.addItem("相对基波百分比", "percent")
        scale_combo.addItem("线性幅值", "amplitude")
        scale_combo.addItem("相对基波 dBc", "dbc")
        scale_combo.setCurrentIndex(max(0, scale_combo.findData(settings["scale"])))
        display_max_edit = QLineEdit(f'{settings["display_max_hz"]:g}')
        fundamental_min_edit = QLineEdit(f'{settings["fundamental_min_hz"]:g}')
        fundamental_max_edit = QLineEdit(f'{settings["fundamental_max_hz"]:g}')
        db_floor_edit = QLineEdit(f'{settings["db_floor"]:g}')
        for edit in (display_max_edit, fundamental_min_edit, fundamental_max_edit, db_floor_edit):
            edit.setValidator(QDoubleValidator(edit))
        harmonic_count = QSpinBox()
        harmonic_count.setRange(2, 30)
        harmonic_count.setValue(settings["harmonic_count"])
        search_bins = QSpinBox()
        search_bins.setRange(0, 8)
        search_bins.setValue(settings["harmonic_search_bins"])
        remove_dc = QCheckBox("去除平均值，避免直流分量遮挡低频信号")
        remove_dc.setChecked(settings["remove_dc"])

        form.addRow("窗函数", window_combo)
        form.addRow("分析点数", size_combo)
        form.addRow("纵轴", scale_combo)
        form.addRow("显示最高频率", display_max_edit)
        form.addRow("基波搜索下限", fundamental_min_edit)
        form.addRow("基波搜索上限", fundamental_max_edit)
        form.addRow("分析谐波次数", harmonic_count)
        form.addRow("谐波峰值搜索 ±频点", search_bins)
        form.addRow("dBc 显示下限", db_floor_edit)
        layout.addLayout(form)
        layout.addWidget(remove_dc)
        note = QLabel(
            "频率填写 0 表示自动使用奈奎斯特频率。Hann 适合一般频谱观察；"
            "Flat Top 更适合比较幅值。基波应限制在预期频段内，避免把较强谐波误认为基波。"
        )
        note.setWordWrap(True)
        note.setObjectName("fieldHint")
        layout.addWidget(note)
        actions = QHBoxLayout()
        defaults = QPushButton("恢复推荐值")
        cancel = QPushButton("取消")
        apply_button = QPushButton("应用")
        apply_button.setObjectName("primaryButton")
        actions.addWidget(defaults)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(apply_button)
        layout.addLayout(actions)

        def restore_defaults():
            window_combo.setCurrentIndex(window_combo.findData(DEFAULT_FFT_SETTINGS["window"]))
            size_combo.setCurrentIndex(size_combo.findData(0))
            scale_combo.setCurrentIndex(scale_combo.findData("percent"))
            display_max_edit.setText("0")
            fundamental_min_edit.setText("0.5")
            fundamental_max_edit.setText("0")
            harmonic_count.setValue(10)
            search_bins.setValue(2)
            db_floor_edit.setText("-100")
            remove_dc.setChecked(True)

        defaults.clicked.connect(restore_defaults)
        cancel.clicked.connect(dialog.reject)
        apply_button.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            candidate = normalize_fft_settings({
                "window": window_combo.currentData(),
                "fft_size": size_combo.currentData(),
                "scale": scale_combo.currentData(),
                "display_max_hz": float(display_max_edit.text() or 0),
                "fundamental_min_hz": float(fundamental_min_edit.text() or 0),
                "fundamental_max_hz": float(fundamental_max_edit.text() or 0),
                "harmonic_count": harmonic_count.value(),
                "harmonic_search_bins": search_bins.value(),
                "db_floor": float(db_floor_edit.text() or -100),
                "remove_dc": remove_dc.isChecked(),
            })
        except ValueError:
            QMessageBox.warning(self, "FFT 参数无效", "频率和 dBc 下限必须是有效数字。")
            return
        if (candidate["fundamental_max_hz"] > 0.0
                and candidate["fundamental_max_hz"] <= candidate["fundamental_min_hz"]):
            QMessageBox.warning(self, "FFT 参数无效", "基波搜索上限必须大于下限；填写 0 可自动使用最高频率。")
            return
        self.fft_settings[plot_idx] = candidate
        self.fft_last_update[plot_idx] = 0.0
        self._apply_plot_mode_visuals(plot_idx)
        self._save_fft_state()
        if self.plot_modes[plot_idx] != "fft":
            self.set_plot_mode(plot_idx, "fft")
        else:
            self.update_plot_canvas(force=True)

    def _format_fft_summary(self, plot_idx, results):
        if not results:
            return "<div style='padding:7px 9px'><b>FFT</b><br>等待有效采样数据</div>"
        rows = []
        first_result = next(iter(results.values()))
        rows.append(
            f"<span style='color:#8e99a8'>FFT {first_result['fft_size']} 点 · "
            f"Δf {first_result['resolution_hz']:.4g} Hz · "
            f"Fs {first_result['sample_rate_hz']:.4g} Hz</span>"
        )
        for path, result in results.items():
            color = self._curve_color(path)
            rows.append(
                f"<b style='color:{color}'>{html.escape(path)}</b>　"
                f"基波 {result['fundamental_frequency_hz']:.5g} Hz　"
                f"幅值 {result['fundamental_amplitude']:.5g}　"
                f"THD {result['thd_percent']:.3g}%"
            )
            harmonic_parts = [
                f"H{item['order']} {item['percent']:.3g}%"
                for item in result["harmonics"][1:]
            ]
            for start in range(0, len(harmonic_parts), 5):
                rows.append("　".join(harmonic_parts[start:start + 5]))
        return "<div style='padding:7px 9px; line-height:1.35'>" + "<br>".join(rows) + "</div>"

    def _update_fft_plot(self, plot_idx):
        now = time.monotonic()
        if now - self.fft_last_update[plot_idx] < 0.25:
            return
        self.fft_last_update[plot_idx] = now
        settings = normalize_fft_settings(self.fft_settings[plot_idx])
        copy_count = settings["fft_size"] or 4096
        snapshots = {}
        with QMutexLocker(self.buffer_mutex):
            for path in self.plot_active_paths[plot_idx]:
                if path not in self.ch_paths:
                    continue
                channel_idx = self.ch_paths.index(path)
                snapshots[path] = (
                    list(self.ch_time_stamps[channel_idx][-copy_count:]),
                    list(self.ch_buffers[channel_idx][-copy_count:]),
                )

        plot = self.plot_widgets[plot_idx]
        curves_map = self.plot_curves_dict[plot_idx]
        for path in list(curves_map):
            if path not in self.plot_active_paths[plot_idx]:
                self.plot_legends[plot_idx].removeItem(path)
                plot.removeItem(curves_map.pop(path))
        results = {}
        waiting = []
        all_y = []
        marker_spots = []
        maximum_frequency = 0.0
        for path, (timestamps, samples) in snapshots.items():
            result = analyze_spectrum(timestamps, samples, settings)
            if not result.get("ok"):
                waiting.append(f"{path}：{result.get('reason', '数据不足')}")
                if path in curves_map:
                    curves_map[path].setData([], [])
                continue
            results[path] = result
            display_max = settings["display_max_hz"] or result["nyquist_hz"]
            display_max = min(display_max, result["nyquist_hz"])
            maximum_frequency = max(maximum_frequency, display_max)
            frequencies = result["frequencies"]
            display_values = spectrum_display_values(
                result["amplitudes"], result["fundamental_amplitude"],
                settings["scale"], settings["db_floor"],
            )
            visible = frequencies <= display_max
            x_values, y_values = frequencies[visible], display_values[visible]
            if path not in curves_map:
                pen = QPen(QColor(self._curve_color(path)), 1.7)
                pen.setCosmetic(True)
                curves_map[path] = plot.plot(pen=pen, antialias=True, name=path)
                curves_map[path].setClipToView(True)
            curves_map[path].setData(x_values, y_values)
            all_y.append(y_values)
            for harmonic in result["harmonics"]:
                if harmonic["frequency_hz"] > display_max:
                    continue
                marker_y = spectrum_display_values(
                    [harmonic["amplitude"]], result["fundamental_amplitude"],
                    settings["scale"], settings["db_floor"],
                )[0]
                marker_spots.append({
                    "pos": (harmonic["frequency_hz"], float(marker_y)),
                    "brush": pg.mkBrush(self._curve_color(path)),
                    "data": (path, harmonic["order"]),
                })
        for path in self.plot_active_paths[plot_idx]:
            if path not in snapshots and path in curves_map:
                curves_map[path].setData([], [])

        self.fft_results[plot_idx] = results
        self.plot_legends[plot_idx].setVisible(bool(self.plot_active_paths[plot_idx]))
        self.fft_harmonic_markers[plot_idx].setData(spots=marker_spots)
        self.fft_harmonic_markers[plot_idx].setVisible(bool(marker_spots))
        if results:
            self.fft_summary_items[plot_idx].setHtml(self._format_fft_summary(plot_idx, results))
            plot.setXRange(0.0, max(maximum_frequency, 1.0), padding=0)
            if settings["scale"] == "dbc":
                plot.setYRange(settings["db_floor"], 5.0, padding=0)
            elif settings["scale"] == "percent":
                finite_max = max((float(np.max(values)) for values in all_y if values.size), default=100.0)
                plot.setYRange(0.0, max(105.0, finite_max * 1.08), padding=0)
            else:
                finite_max = max((float(np.max(values)) for values in all_y if values.size), default=1.0)
                plot.setYRange(0.0, max(finite_max * 1.08, 1e-9), padding=0)
        else:
            message = "<br>".join(html.escape(item) for item in waiting[:4]) or "当前通道暂无变量"
            self.fft_summary_items[plot_idx].setHtml(
                f"<div style='padding:7px 9px'><b>FFT</b><br>{message}</div>"
            )
        self.fft_summary_items[plot_idx].setVisible(True)
        self._position_fft_summary(plot_idx)

    def start_canvas_auto_adapt(self):
        if not any(self.ch_paths):
            QMessageBox.information(self, "一键自适应画布", "请先把至少一个变量绑定到波形通道。")
            return
        self.finish_canvas_auto_adapt()

    def _place_plot_legend_away_from_data(self, plot_idx, x_values, y_values, x_range, y_range):
        """Choose the least occupied plot corner for the compact legend."""
        x_values = np.asarray(x_values, dtype=float)
        y_values = np.asarray(y_values, dtype=float)
        finite = np.isfinite(x_values) & np.isfinite(y_values)
        if not finite.any():
            return
        x_values, y_values = x_values[finite], y_values[finite]
        x_lower, x_upper = x_range
        y_lower, y_upper = y_range
        x_span = max(float(x_upper) - float(x_lower), 1e-12)
        y_span = max(float(y_upper) - float(y_lower), 1e-12)
        x_norm = np.clip((x_values - x_lower) / x_span, 0.0, 1.0)
        y_norm = np.clip((y_values - y_lower) / y_span, 0.0, 1.0)
        scores = {
            "top-left": int(np.count_nonzero((x_norm <= 0.42) & (y_norm >= 0.68))),
            "top-right": int(np.count_nonzero((x_norm >= 0.58) & (y_norm >= 0.68))),
            "bottom-left": int(np.count_nonzero((x_norm <= 0.42) & (y_norm <= 0.32))),
            "bottom-right": int(np.count_nonzero((x_norm >= 0.58) & (y_norm <= 0.32))),
        }
        # Stable tie order avoids the legend jumping between equally empty corners.
        order = ("top-left", "top-right", "bottom-left", "bottom-right")
        corner = min(order, key=lambda name: (scores[name], order.index(name)))
        offsets = {
            "top-left": (10, 8),
            "top-right": (-10, 8),
            "bottom-left": (10, -8),
            "bottom-right": (-10, -8),
        }
        self.plot_legends[plot_idx].setOffset(offsets[corner])
        self.plot_legend_corners[plot_idx] = corner

    def finish_canvas_auto_adapt(self):
        adapted = 0
        with QMutexLocker(self.buffer_mutex):
            for plot_idx in range(3):
                if self.plot_modes[plot_idx] == "fft":
                    continue
                x_values, y_values = [], []
                for path in self.plot_active_paths[plot_idx]:
                    try: channel_idx = self.ch_paths.index(path)
                    except ValueError: continue
                    timestamps = self.ch_time_stamps[channel_idx]
                    values = self.ch_buffers[channel_idx]
                    if not timestamps or not values: continue
                    count = min(len(timestamps), len(values))
                    newest_x = timestamps[count - 1]
                    first = bisect.bisect_left(timestamps, newest_x - self.max_display_duration_ms)
                    if first >= count: continue
                    x_arr = np.asarray(timestamps[first:count], dtype=float)
                    y_arr = np.asarray(values[first:count], dtype=float)
                    finite = np.isfinite(x_arr) & np.isfinite(y_arr)
                    if finite.any():
                        x_values.append(x_arr[finite])
                        y_values.append(y_arr[finite])
                if not x_values: continue
                all_x, all_y = np.concatenate(x_values), np.concatenate(y_values)
                x_upper = float(np.max(all_x))
                x_lower = x_upper - self.max_display_duration_ms
                y_min, y_max = float(np.min(all_y)), float(np.max(all_y))
                y_lower, y_upper = self._padded_y_range(y_min, y_max)
                self.canvas_fixed_ranges[plot_idx] = (x_lower, x_upper, y_lower, y_upper)
                self._place_plot_legend_away_from_data(
                    plot_idx, all_x, all_y, (x_lower, x_upper), (y_lower, y_upper)
                )
                self.plot_widgets[plot_idx].setXRange(x_lower, x_upper, padding=0)
                self.plot_widgets[plot_idx].setYRange(y_lower, y_upper, padding=0)
                adapted += 1
        if adapted:
            self.lbl_status_text.setText(f"已适配 {adapted} 个波形视图")
        else:
            self.lbl_status_text.setText("暂无有效波形数据，稍后再试")

    def reset_canvas_adapt_ranges(self):
        self.canvas_fixed_ranges = [None, None, None]
        for plot_idx, plot_widget in enumerate(getattr(self, 'plot_widgets', [])):
            if self.plot_modes[plot_idx] == "fft":
                continue
            try: plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            except Exception: pass

    @staticmethod
    def _display_plot_data(timestamps, values, start_idx, max_points=4000):
        """Return peak-preserving display data with a bounded render cost."""
        x_values = np.asarray(timestamps[start_idx:], dtype=np.float64)
        y_values = np.asarray(values[start_idx:], dtype=np.float64)
        count = min(x_values.size, y_values.size)
        if count == 0:
            return x_values[:0], y_values[:0]
        x_values, y_values = x_values[:count], y_values[:count]
        finite = np.isfinite(x_values) & np.isfinite(y_values)
        if not finite.all():
            x_values, y_values = x_values[finite], y_values[finite]
            count = x_values.size
        if count <= max_points:
            return x_values, y_values

        bucket_count = max(1, max_points // 2)
        step = max(1, int(np.ceil(count / bucket_count)))
        group_count = int(np.ceil(count / step))
        padded_count = group_count * step
        padded = np.pad(y_values, (0, padded_count - count), mode="edge")
        grouped = padded.reshape(group_count, step)
        base = np.arange(group_count, dtype=np.int64) * step
        minimum = base + np.argmin(grouped, axis=1)
        maximum = base + np.argmax(grouped, axis=1)
        indices = np.column_stack((np.minimum(minimum, maximum),
                                   np.maximum(minimum, maximum))).ravel()
        indices = indices[indices < count]
        return x_values[indices], y_values[indices]

    def update_plot_canvas(self, force=False):
        if (self.is_frozen and not force) or self.layout_switching: return
        layout_id = self.current_layout_id
        for plot_idx in range(3):
            visible = plot_idx == 0 or (plot_idx == 1 and layout_id >= 2) or (plot_idx == 2 and layout_id >= 3)
            if visible and self.plot_modes[plot_idx] == "fft":
                self._update_fft_plot(plot_idx)
        locker = QMutexLocker(self.buffer_mutex)
        for i in range(3):
            if self.plot_modes[i] == "fft":
                continue
            active_paths = self.plot_active_paths[i]
            curves_map = self.plot_curves_dict[i]
            ch_idx = next(
                (self.ch_paths.index(path) for path in active_paths if path in self.ch_paths),
                -1,
            )

            pw = self.plot_widgets[i]
            self.plot_legends[i].setVisible(bool(active_paths))
            if i >= 1 and layout_id < 2: continue
            if i == 2 and layout_id < 3: continue
            if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20: continue
                
            try:  
                if not pw.getPlotItem().scene(): continue
                fixed_range = self.canvas_fixed_ranges[i]
                if fixed_range is not None:
                    # Keep the adapted X span, but anchor it to the newest
                    # sample so the time axis continues scrolling.
                    x_span = max(fixed_range[1] - fixed_range[0], 1.0)
                    if ch_idx != -1 and self.ch_time_stamps[ch_idx]:
                        max_x = self.ch_time_stamps[ch_idx][-1]
                    else:
                        max_x = (time.time() - self.start_time) * 1000.0
                    min_x = max_x - x_span
                    pw.setXRange(min_x, max_x, padding=0)
                elif ch_idx != -1 and self.ch_time_stamps[ch_idx]:
                    max_x = self.ch_time_stamps[ch_idx][-1]  
                    min_x = max_x - self.max_display_duration_ms
                    pw.setXRange(min_x, max_x, padding=0)
                else:
                    now_rel_time = (time.time() - self.start_time) * 1000.0
                    min_x = now_rel_time - self.max_display_duration_ms
                    pw.setXRange(min_x, now_rel_time, padding=0)
                
                curr_keys = list(curves_map.keys())
                for pk in curr_keys:
                    if pk not in active_paths and pk in curves_map:
                        self.plot_legends[i].removeItem(pk)
                        pw.removeItem(curves_map[pk])
                        del curves_map[pk]

                for path in active_paths:
                    try:
                        target_ch = self.ch_paths.index(path)
                    except ValueError:
                        target_ch = -1
                    if target_ch != -1 and self.ch_buffers[target_ch]:
                        t_data = self.ch_time_stamps[target_ch]
                        v_data = self.ch_buffers[target_ch]
                        start_idx = bisect.bisect_left(t_data, min_x)
                        if len(t_data) > start_idx:
                            x_arr, y_arr = self._display_plot_data(t_data, v_data, start_idx)
                            if path not in curves_map:
                                smooth_pen = QPen(QColor(self._curve_color(path)), 2.0)
                                smooth_pen.setCosmetic(True)
                                smooth_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                                smooth_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                                curves_map[path] = pw.plot(pen=smooth_pen, antialias=True, name=path)
                                curves_map[path].setClipToView(True)
                                curves_map[path].setDownsampling(auto=True, method="peak")
                            curves_map[path].setData(x_arr, y_arr)
            except: pass 

    def _nearest_fft_sample(self, plot_idx, scene_pos, max_distance=34.0):
        result_map = self.fft_results[plot_idx]
        if not result_map:
            return None
        settings = normalize_fft_settings(self.fft_settings[plot_idx])
        view_box = self.plot_widgets[plot_idx].getViewBox()
        mouse_frequency = view_box.mapSceneToView(scene_pos).x()
        candidates = []
        for path, result in result_map.items():
            frequencies = result["frequencies"]
            amplitudes = result["amplitudes"]
            display_values = spectrum_display_values(
                amplitudes, result["fundamental_amplitude"],
                settings["scale"], settings["db_floor"],
            )
            index = int(np.searchsorted(frequencies, mouse_frequency))
            for candidate in {max(0, min(len(frequencies) - 1, index)),
                              max(0, min(len(frequencies) - 1, index - 1))}:
                candidates.append({
                    "path": path,
                    "frequency": float(frequencies[candidate]),
                    "display_value": float(display_values[candidate]),
                    "amplitude": float(amplitudes[candidate]),
                    "harmonic": None,
                })
            for harmonic in result["harmonics"]:
                harmonic_display = spectrum_display_values(
                    [harmonic["amplitude"]], result["fundamental_amplitude"],
                    settings["scale"], settings["db_floor"],
                )[0]
                candidates.append({
                    "path": path,
                    "frequency": float(harmonic["frequency_hz"]),
                    "display_value": float(harmonic_display),
                    "amplitude": float(harmonic["amplitude"]),
                    "harmonic": harmonic,
                })

        best = None
        best_distance_sq = float(max_distance) ** 2
        for candidate in candidates:
            scene_point = view_box.mapViewToScene(QPointF(
                candidate["frequency"], candidate["display_value"]
            ))
            distance_sq = ((scene_point.x() - scene_pos.x()) ** 2
                           + (scene_point.y() - scene_pos.y()) ** 2)
            if distance_sq <= best_distance_sq:
                best_distance_sq = distance_sq
                best = candidate
        return best

    def _handle_fft_mouse_move(self, pos, plot_idx):
        plot = self.plot_widgets[plot_idx]
        if plot.isHidden() or not plot.isVisible() or plot.width() <= 20 or plot.height() <= 20:
            return
        view_box = plot.getViewBox()
        if not view_box.sceneBoundingRect().contains(pos):
            return
        nearest = self._nearest_fft_sample(plot_idx, pos)
        if nearest is None:
            self.hover_dots[plot_idx].setVisible(False)
            self.hover_labels[plot_idx].setVisible(False)
            return
        frequency = nearest["frequency"]
        display_value = nearest["display_value"]
        path = nearest["path"]
        point_key = (path, frequency, display_value, bool(nearest["harmonic"]))
        if point_key == self.hover_last_point[plot_idx]:
            return
        self.hover_last_point[plot_idx] = point_key
        self.v_lines[plot_idx].setPos(frequency)
        self.v_lines[plot_idx].setVisible(True)
        self.h_lines[plot_idx].setPos(display_value)
        self.h_lines[plot_idx].setVisible(True)
        self.hover_dots[plot_idx].setData(
            [frequency], [display_value], brush=pg.mkBrush(self._curve_color(path))
        )
        self.hover_dots[plot_idx].setVisible(True)
        scale = normalize_fft_settings(self.fft_settings[plot_idx])["scale"]
        display_text = (
            f"{display_value:.4g}%" if scale == "percent" else
            (f"{display_value:.4g} dBc" if scale == "dbc" else f"{display_value:.6g}")
        )
        harmonic = nearest["harmonic"]
        if harmonic:
            details = (
                f"H{harmonic['order']} · {frequency:.6g} Hz\n"
                f"相对基波 {harmonic['percent']:.4g}% · 幅值 {harmonic['amplitude']:.6g}"
            )
        else:
            details = (
                f"f: {frequency:.6g} Hz · {display_text}\n"
                f"线性幅值 {nearest['amplitude']:.6g}"
            )
        self.hover_labels[plot_idx].setText(f" {path}\n {details} ")
        self.hover_labels[plot_idx].setPos(frequency, display_value)
        self.hover_labels[plot_idx].setVisible(True)

    def handle_plot_mouse_move(self, pos, plot_idx):
        if not self.is_frozen or self.layout_switching: return
        if self.plot_modes[plot_idx] == "fft":
            try:
                self._handle_fft_mouse_move(pos, plot_idx)
            except (IndexError, KeyError, TypeError, ValueError, RuntimeError):
                pass
            return
        if self.measurement_mode:
            return
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20: return

        try:
            if not pw.getPlotItem().scene(): return
            view_box = pw.getViewBox()
            if not view_box.sceneBoundingRect().contains(pos):
                return
            mouse_point = view_box.mapSceneToView(pos)
            if self.v_lines[plot_idx]:
                self.v_lines[plot_idx].setPos(mouse_point.x())
                self.v_lines[plot_idx].setVisible(True)
            if self.h_lines[plot_idx]:
                self.h_lines[plot_idx].setPos(mouse_point.y())
                self.h_lines[plot_idx].setVisible(True)

            nearest = self._nearest_visible_sample(plot_idx, pos)
            if nearest is None:
                self.hover_dots[plot_idx].setVisible(False)
                self.hover_labels[plot_idx].setVisible(False)
                return
            exact_x, exact_y, _display_channel, path = nearest
            point_key = (path, exact_x, exact_y)
            if point_key == self.hover_last_point[plot_idx]:
                return
            self.hover_last_point[plot_idx] = point_key

            if self.hover_dots[plot_idx]:
                self.hover_dots[plot_idx].setData(
                    [exact_x], [exact_y], brush=pg.mkBrush(self._curve_color(path))
                )
                self.hover_dots[plot_idx].setVisible(True)

            if self.hover_labels[plot_idx]:
                self.hover_labels[plot_idx].setText(f" {path}\n t: {exact_x:.1f} ms  ·  v: {exact_y:.4f} ")
                self.hover_labels[plot_idx].setPos(exact_x, exact_y)
                self.hover_labels[plot_idx].setVisible(True)
        except: pass

    def show_plot_context_menu(self, pos, plot_idx):
        """Show only MCU Studio actions; never expose pyqtgraph's internal menu."""
        if self.layout_switching:
            return
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible():
            return

        menu = RoundedMenu(self)
        menu.setStyleSheet(self.popup_menu_style)
        scalar_items = self._debug_scalar_items()
        channel_menu = menu.addMenu(f"添加变量到 CH{plot_idx + 1}")
        if not scalar_items:
            unavailable = channel_menu.addAction("调试窗口中暂无标量变量")
            unavailable.setEnabled(False)
        else:
            for _slot, title, items in scalar_items:
                window_menu = channel_menu.addMenu(title)
                for item in items:
                    path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
                    action = window_menu.addAction(path)
                    action.setCheckable(True)
                    action.setChecked(path in self.plot_active_paths[plot_idx])
                    action.triggered.connect(
                        lambda checked=False, tree_item=item, target_plot=plot_idx:
                        self.bind_tree_item_to_channel(target_plot, tree_item)
                        if checked else self.set_plot_channel_visible(
                            tree_item.data(0, Qt.ItemDataRole.UserRole) or tree_item.text(0),
                            target_plot,
                            False,
                        )
                    )

        menu.addSeparator()
        visible_menu = menu.addMenu("本图显示通道")
        for path in list(self.plot_active_paths[plot_idx]):
            action = visible_menu.addAction(self._color_icon(self._curve_color(path)), path)
            action.setCheckable(True)
            action.setChecked(True)
            action.triggered.connect(
                lambda checked, p=path, target_plot=plot_idx:
                self.set_plot_channel_visible(p, target_plot, checked)
            )
        if not self.plot_active_paths[plot_idx]:
            empty = visible_menu.addAction("当前 CH 暂无变量")
            empty.setEnabled(False)

        color_menu = menu.addMenu("曲线颜色")
        if self.plot_active_paths[plot_idx]:
            for path in self.plot_active_paths[plot_idx]:
                path_menu = color_menu.addMenu(path)
                path_menu.setIcon(self._color_icon(self._curve_color(path)))
                self._populate_curve_color_menu(path_menu, path)
        else:
            empty = color_menu.addAction("当前 CH 暂无变量")
            empty.setEnabled(False)

        menu.addSeparator()
        display_menu = menu.addMenu("显示模式")
        time_action = display_menu.addAction("时域波形")
        time_action.setCheckable(True)
        time_action.setChecked(self.plot_modes[plot_idx] == "time")
        fft_action = display_menu.addAction("频域分析（FFT）")
        fft_action.setCheckable(True)
        fft_action.setChecked(self.plot_modes[plot_idx] == "fft")
        fft_settings_action = menu.addAction("FFT 参数…")
        fft_settings_action.setEnabled(bool(self.plot_active_paths[plot_idx]))
        menu.addSeparator()
        measure_action = menu.addAction("两点差值测量")
        measure_action.setCheckable(True)
        measure_action.setChecked(self.measurement_mode)
        measure_action.setEnabled(self.plot_modes[plot_idx] == "time")
        adapt_action = menu.addAction("自动适应当前波形")
        adapt_action.setEnabled(self.plot_modes[plot_idx] == "time")
        clear_action = menu.addAction("清除测量标记")
        clear_action.setEnabled(any(self.measurement_points))
        menu.addSeparator()
        export_action = menu.addAction("导出采样 CSV…")
        chosen = menu.exec(pw.mapToGlobal(pos))
        if chosen == time_action:
            self.set_plot_mode(plot_idx, "time")
        elif chosen == fft_action:
            self.set_plot_mode(plot_idx, "fft")
        elif chosen == fft_settings_action:
            self.configure_fft(plot_idx)
        elif chosen == measure_action:
            self.toggle_measurement_mode(measure_action.isChecked())
        elif chosen == adapt_action:
            self.start_canvas_auto_adapt()
        elif chosen == clear_action:
            self.clear_measurements()
        elif chosen == export_action:
            self.export_variable_csv()

    def set_plot_channel_visible(self, path, plot_idx, visible):
        active_paths = self.plot_active_paths[plot_idx]
        if visible and path not in active_paths:
            active_paths.append(path)
        elif not visible and path in active_paths:
            active_paths.remove(path)
        if not any(path in paths for paths in self.plot_active_paths):
            self._remove_sampled_path(path)
        self.clear_measurements()
        self.refresh_ui_channel_bar()
        self.schedule_workspace_save()
        self.restart_sampling_thread()

    def _remove_sampled_path(self, path):
        if path not in self.ch_paths:
            return
        with QMutexLocker(self.buffer_mutex):
            if path not in self.ch_paths:
                return
            sample_idx = self.ch_paths.index(path)
            self.ch_paths.pop(sample_idx)
            self.ch_types.pop(sample_idx)
            self.ch_addrs.pop(sample_idx)
            self.ch_buffers.pop(sample_idx)
            self.ch_time_stamps.pop(sample_idx)
        for plot_idx, curves in enumerate(self.plot_curves_dict):
            curve = curves.pop(path, None)
            if curve is not None:
                self.plot_legends[plot_idx].removeItem(path)
                self.plot_widgets[plot_idx].removeItem(curve)

    def _color_icon(self, color):
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(2, 2, 10, 10)
        painter.end()
        return QIcon(pixmap)

    def _curve_color(self, path):
        color = self.curve_colors.get(path)
        if color:
            return color
        saved = self.db.get_config(f"curve_color:{path}", "").strip()
        if saved and QColor(saved).isValid():
            color = QColor(saved).name()
        else:
            used = set(self.curve_colors.values())
            color = next((candidate for candidate in self.curve_palette if candidate not in used), None)
            if color is None:
                color = self.curve_palette[len(self.curve_colors) % len(self.curve_palette)]
            self.db.save_config(f"curve_color:{path}", color)
        self.curve_colors[path] = color
        return color

    def _populate_curve_color_menu(self, menu, path):
        current = self._curve_color(path)
        for color, name in zip(self.curve_palette, self.curve_color_names):
            action = menu.addAction(self._color_icon(color), name)
            action.setCheckable(True)
            action.setChecked(color.casefold() == current.casefold())
            action.triggered.connect(
                lambda _checked=False, variable=path, selected=color:
                self.set_curve_color(variable, selected)
            )

    def set_curve_color(self, path, color):
        normalized = QColor(color).name()
        self.curve_colors[path] = normalized
        self.db.save_config(f"curve_color:{path}", normalized)
        for curves in self.plot_curves_dict:
            curve = curves.get(path)
            if curve is None:
                continue
            pen = QPen(QColor(normalized), 2.0)
            pen.setCosmetic(True)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            curve.setPen(pen)
        self.update_plot_canvas()
