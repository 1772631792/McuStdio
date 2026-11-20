"""Variable slider helpers and inline slider widgets embedded in watch trees."""

from .common import (
    QDialog,
    QDoubleValidator,
    QEvent,
    QFont,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    json,
    math,
    np,
    pyqtSignal,
)
from .widgets import (
    QComboBox,
    RoundedMenu,
)


def automatic_slider_profile(value, type_info=None, type_name=""):
    """Choose practical bounds and a human-scale step from type and value."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if not np.isfinite(value):
        value = 0.0
    info = type_info or {}
    encoding = int(info.get("encoding", 0) or 0)
    size = int(info.get("size", 0) or 0)
    normalized = str(type_name or info.get("name", "")).replace("_t", "").lower()
    is_float = encoding == 0x04 or "float" in normalized or "double" in normalized
    is_bool = encoding == 0x02 or "bool" in normalized
    signed = encoding in (0x05, 0x06) or ("int" in normalized and "uint" not in normalized)
    if is_bool:
        return 0.0, 1.0, 1.0
    if not is_float and size in (1, 2):
        bits = size * 8
        minimum = -(1 << (bits - 1)) if signed else 0
        maximum = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    else:
        span = max(1.0 if is_float else 10.0, abs(value) * 1.25)
        minimum = -span if signed or is_float or value < 0 else 0.0
        maximum = span if value <= span else value * 2.0
        if value < minimum:
            minimum = value * 2.0
    raw_step = max((maximum - minimum) / 200.0, 1e-6 if is_float else 1.0)
    exponent = math.floor(math.log10(raw_step)) if raw_step > 0 else 0
    fraction = raw_step / (10 ** exponent)
    nice = 1 if fraction <= 1 else (2 if fraction <= 2 else (5 if fraction <= 5 else 10))
    step = nice * (10 ** exponent)
    if not is_float:
        step = max(1.0, round(step))
        minimum, maximum = math.floor(minimum), math.ceil(maximum)
    if minimum == maximum:
        maximum = minimum + step
    return float(minimum), float(maximum), float(step)


def variable_write_available(backend_suspended, flash_active, backend_mode,
                             jlink_connected, openocd_connected):
    """Pure write-gating policy shared by UI actions and regression tests."""
    if backend_suspended or flash_active:
        return False
    if str(backend_mode).upper() == "JLINK":
        return bool(jlink_connected)
    return bool(openocd_connected)


def rotating_batch(items, offset, limit=32):
    """Return a bounded fair slice and the next round-robin offset."""
    values = list(items)
    if not values:
        return [], 0
    size = min(max(1, int(limit)), len(values))
    start = int(offset) % len(values)
    selected = [values[(start + index) % len(values)] for index in range(size)]
    return selected, (start + size) % len(values)


class InlineVariableSlider(QWidget):
    """Industrial-style variable writer embedded in a taller debug-tree row."""

    settings_requested = pyqtSignal(str)
    restore_requested = pyqtSignal(str)
    state_changed = pyqtSignal(str, object)

    def __init__(self, main, path, spec, state=None, parent=None):
        super().__init__(parent)
        self.main = main
        self.path = path
        self.spec = spec
        self.integer = not spec["is_float"]
        self.latest_sequence = 0
        self.write_pending = False
        self.user_editing = False
        self.pending_timer = QTimer(self)
        self.pending_timer.setSingleShot(True)
        self.pending_timer.setInterval(100)
        self.pending_timer.timeout.connect(self._submit)
        try:
            current = float(main.latest_values.get(path, 0.0))
        except (TypeError, ValueError):
            current = 0.0
        minimum, maximum, step = automatic_slider_profile(current, spec.get("info"), spec["type"])
        self.live_write = False
        self.auto_range = True
        if isinstance(state, dict):
            try:
                minimum = float(state.get("min", minimum))
                maximum = float(state.get("max", maximum))
                step = abs(float(state.get("step", step)))
                self.live_write = bool(state.get("live_write", False))
                self.auto_range = bool(state.get("auto_range", True))
            except (TypeError, ValueError):
                pass
        if not all(np.isfinite(value) for value in (minimum, maximum, step)) or maximum <= minimum or step <= 0:
            minimum, maximum, step = automatic_slider_profile(current, spec.get("info"), spec["type"])
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.value = min(max(current, minimum), maximum)

        self.setObjectName("inlineVariableSlider")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 7)
        layout.setSpacing(5)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        self.minus_button = QPushButton("−")
        self.minus_button.setObjectName("sliderStepButton")
        self.minus_button.setFixedSize(32, 30)
        self.minus_button.setAutoRepeat(True)
        self.minus_button.setAutoRepeatDelay(350)
        self.minus_button.setAutoRepeatInterval(90)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(120)
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.installEventFilter(self)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(74)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        self.value_label.setObjectName("sliderValueLabel")
        self.plus_button = QPushButton("+")
        self.plus_button.setObjectName("sliderStepButton")
        self.plus_button.setFixedSize(32, 30)
        self.plus_button.setAutoRepeat(True)
        self.plus_button.setAutoRepeatDelay(350)
        self.plus_button.setAutoRepeatInterval(90)
        self.step_label = QLabel(self)
        self.step_label.setObjectName("sliderStepLabel")
        self.step_label.hide()
        self.status_label = QLabel("跟随", self)
        self.status_label.setObjectName("sliderStatusLabel")
        self.status_label.hide()
        controls.addWidget(self.minus_button)
        controls.addWidget(self.value_label, 1)
        controls.addWidget(self.plus_button)
        layout.addLayout(controls)
        layout.addWidget(self.slider)

        dark = bool(getattr(main, "is_dark_theme", False))
        text = "#f2f2f7" if dark else "#172033"
        track = "#45454a" if dark else "#d9e1ea"
        button = "#343438" if dark else "#eef2f7"
        hover = "#414147" if dark else "#dceaff"
        self.setStyleSheet(f"""
            QWidget#inlineVariableSlider {{ background: transparent; border: none; }}
            QPushButton#sliderStepButton {{ background: {button}; color: #0a84ff; border: none;
                border-radius: 8px; font-size: 20px; font-weight: 700; padding: 0; }}
            QPushButton#sliderStepButton:hover {{ background: {hover}; }}
            QPushButton#sliderStepButton:pressed {{ background: #0a84ff; color: white; }}
            QLabel#sliderValueLabel {{ color: {text}; background: transparent; border: none; font-size: 18px; font-weight: 700; }}
            QSlider {{ background: transparent; border: none; min-height: 21px; }}
            QSlider::groove:horizontal {{ height: 8px; background: {track}; border-radius: 4px; }}
            QSlider::sub-page:horizontal {{ background: #0a84ff; border-radius: 4px; }}
            QSlider::handle:horizontal {{ width: 20px; margin: -6px 0; background: white;
                border: 3px solid #0a84ff; border-radius: 10px; }}
            QSlider::handle:horizontal:hover {{ background: #e8f3ff; }}
        """)
        self.minus_button.clicked.connect(lambda: self._step_by(-1))
        self.plus_button.clicked.connect(lambda: self._step_by(1))
        self.slider.valueChanged.connect(self._slider_changed)
        self.slider.actionTriggered.connect(self._slider_action_triggered)
        self.slider.sliderPressed.connect(self._slider_pressed)
        self.slider.sliderReleased.connect(self._slider_released)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.slider.customContextMenuRequested.connect(
            lambda pos: self._show_menu(self.slider.mapTo(self, pos))
        )
        self.main.variable_write_result.connect(self._write_result)
        self._sync_controls()

    def _format(self, value):
        return str(int(round(value))) if self.integer else f"{float(value):.6g}"

    def _slider_steps(self):
        return max(1, min(10000, int(math.ceil((self.maximum - self.minimum) / self.step))))

    def _quantize(self, value):
        value = min(max(float(value), self.minimum), self.maximum)
        steps = round((value - self.minimum) / self.step)
        value = min(self.maximum, self.minimum + steps * self.step)
        return float(round(value)) if self.integer else float(value)

    def _type_limits(self):
        if not self.integer:
            return -math.inf, math.inf
        size = int(self.spec.get("size", 0) or 0)
        if size <= 0:
            return -math.inf, math.inf
        bits = size * 8
        if self.spec.get("signed"):
            return float(-(1 << (bits - 1))), float((1 << (bits - 1)) - 1)
        return 0.0, float((1 << bits) - 1)

    def _expand_for_step(self, target):
        """Grow the manual range in useful chunks while respecting the C type."""
        hard_min, hard_max = self._type_limits()
        target = min(max(float(target), hard_min), hard_max)
        span = max(self.maximum - self.minimum, self.step)
        padding = max(span * 0.25, self.step * 10.0)
        changed = False
        if target < self.minimum and self.minimum > hard_min:
            self.minimum = max(hard_min, min(target, self.minimum - padding))
            changed = True
        if target > self.maximum and self.maximum < hard_max:
            self.maximum = min(hard_max, max(target, self.maximum + padding))
            changed = True
        if changed:
            self.state_changed.emit(self.path, self.export_state())
        return target

    def _value_from_position(self, position):
        value = self.minimum + (self.maximum - self.minimum) * int(position) / self._slider_steps()
        return self._quantize(value)

    def _position_from_value(self, value):
        span = self.maximum - self.minimum
        return 0 if span <= 0 else round((float(value) - self.minimum) / span * self._slider_steps())

    def _sync_controls(self):
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._slider_steps())
        self.slider.setValue(max(0, min(self.slider.maximum(), self._position_from_value(self.value))))
        self.slider.blockSignals(False)
        self.value_label.setText(self._format(self.value))
        self.step_label.setText(f"步进 {self._format(self.step)}")
        self.setToolTip(
            f"{self.path}\n范围 {self._format(self.minimum)} … {self._format(self.maximum)}"
            f" · 步长 {self._format(self.step)}\n"
            f"{'拖动时实时写入' if self.live_write else '松开滑块后写入'}；右键可设置参数"
        )
        self.minus_button.setToolTip(f"减少 {self._format(self.step)}")
        self.plus_button.setToolTip(f"增加 {self._format(self.step)}")

    def _show_status(self, text, color=""):
        self.status_label.setText(text)
        self.value_label.setToolTip(text)
        self.value_label.setStyleSheet(f"color: {color};" if color else "")

    def _slider_changed(self, position):
        self.value = self._value_from_position(position)
        self.value_label.setText(self._format(self.value))
        self._show_status("待写入", "#ff9f0a")
        if self.user_editing and self.live_write:
            self.pending_timer.start()

    def _slider_pressed(self):
        self.user_editing = True
        self.pending_timer.stop()

    def _slider_released(self):
        self.user_editing = False
        self._submit()

    def _slider_action_triggered(self, _action):
        # Groove clicks (including press-and-hold auto-repeat) are discrete
        # adjustments. A queued call runs after QSlider updates its value.
        if not self.user_editing and not self.slider.isSliderDown():
            QTimer.singleShot(0, self._submit)

    def eventFilter(self, watched, event):
        if watched is self.slider and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
                self._step_by(-1)
                return True
            if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
                self._step_by(1)
                return True
        return super().eventFilter(watched, event)

    def _step_by(self, direction):
        self.pending_timer.stop()
        self.user_editing = False
        target = self._expand_for_step(self.value + int(direction) * self.step)
        self.value = self._quantize(target)
        self._sync_controls()
        self._show_status("待写入", "#ff9f0a")
        self._submit()

    def _submit(self):
        self.pending_timer.stop()
        self.latest_sequence = self.main.queue_variable_write(self.path, self.value)
        self.write_pending = bool(self.latest_sequence)
        if not self.latest_sequence:
            self._show_status("未连接，未写入", "#ef5350")
        else:
            self._show_status("写入中", "#0a84ff")

    def _write_result(self, path, sequence, success, _error, value):
        if path != self.path or sequence != self.latest_sequence:
            return
        self.write_pending = False
        self._show_status("写入成功" if success else "写入失败", "#30d158" if success else "#ef5350")
        if success:
            self.update_from_target(value)

    def update_from_target(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if not np.isfinite(value):
            return
        if self.user_editing or self.slider.isSliderDown() or self.pending_timer.isActive() or self.write_pending:
            return
        self.value = value
        if self.auto_range and (value < self.minimum or value > self.maximum):
            self.minimum, self.maximum, self.step = automatic_slider_profile(
                value, self.spec.get("info"), self.spec["type"]
            )
            self.state_changed.emit(self.path, self.export_state())
        self._sync_controls()
        self._show_status("跟随目标值")

    def apply_state(self, state):
        self.minimum = float(state["min"])
        self.maximum = float(state["max"])
        self.step = float(state["step"])
        self.live_write = bool(state.get("live_write", self.live_write))
        self.auto_range = bool(state.get("auto_range", self.auto_range))
        self.value = min(max(self.value, self.minimum), self.maximum)
        self._sync_controls()
        self.state_changed.emit(self.path, self.export_state())

    def export_state(self):
        return {
            "min": self.minimum, "max": self.maximum, "step": self.step,
            "live_write": self.live_write, "auto_range": self.auto_range,
        }

    def _show_menu(self, pos):
        menu = RoundedMenu(self)
        menu.setStyleSheet(self.main.popup_menu_style)
        configure = menu.addAction("设置滑条参数…")
        auto_range = menu.addAction("按当前值自动范围")
        menu.addSeparator()
        restore = menu.addAction("恢复普通数值显示")
        action = menu.exec(self.mapToGlobal(pos))
        if action == configure:
            self.settings_requested.emit(self.path)
        elif action == auto_range:
            current = self.main.latest_values.get(self.path, self.value)
            minimum, maximum, step = automatic_slider_profile(
                current, self.spec.get("info"), self.spec["type"]
            )
            self.apply_state({
                "min": minimum, "max": maximum, "step": step,
                "live_write": self.live_write, "auto_range": True,
            })
        elif action == restore:
            self.restore_requested.emit(self.path)


class VariableSliderRow(QFrame):
    remove_requested = pyqtSignal(str)
    state_changed = pyqtSignal()

    def __init__(self, owner, path, spec, state=None):
        super().__init__()
        self.owner = owner
        self.path = path
        self.spec = spec
        self.integer = not spec["is_float"]
        self.latest_sequence = 0
        self.pending_timer = QTimer(self)
        self.pending_timer.setSingleShot(True)
        self.pending_timer.setInterval(100)
        self.pending_timer.timeout.connect(self._submit)
        current = float(owner.main.latest_values.get(path, 0.0))
        minimum, maximum, step = automatic_slider_profile(current, spec.get("info"), spec["type"])
        if isinstance(state, dict):
            try:
                minimum = float(state.get("min", minimum))
                maximum = float(state.get("max", maximum))
                step = abs(float(state.get("step", step)))
            except (TypeError, ValueError):
                pass
        if maximum <= minimum or step <= 0:
            minimum, maximum, step = automatic_slider_profile(current, spec.get("info"), spec["type"])
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.value = min(max(current, minimum), maximum)
        self.setObjectName("sliderCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        name = QLabel(path)
        name.setToolTip(f"{path}\n{spec['type']} · 0x{spec['address']:08X}")
        name.setStyleSheet("font-weight: 700;")
        self.status = QLabel("就绪")
        self.status.setMinimumWidth(68)
        remove = QPushButton("移除")
        remove.setFixedWidth(54)
        remove.clicked.connect(lambda: self.remove_requested.emit(self.path))
        header.addWidget(name, 1)
        header.addWidget(QLabel(spec["type"]))
        header.addWidget(self.status)
        header.addWidget(remove)
        layout.addLayout(header)

        controls = QHBoxLayout()
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        self.value_edit = QLineEdit()
        self.value_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.value_edit.setValidator(QDoubleValidator(self.value_edit))
        self.value_edit.editingFinished.connect(self._commit_text)
        self.minus.clicked.connect(lambda: self._step_value(-1))
        self.plus.clicked.connect(lambda: self._step_value(1))
        controls.addWidget(self.minus)
        controls.addWidget(self.value_edit, 1)
        controls.addWidget(self.plus)
        layout.addLayout(controls)

        slider_line = QHBoxLayout()
        self.min_edit = QLineEdit(self._format(self.minimum))
        self.max_edit = QLineEdit(self._format(self.maximum))
        for edit in (self.min_edit, self.max_edit):
            edit.setFixedWidth(82)
            edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.setValidator(QDoubleValidator(edit))
            edit.editingFinished.connect(self._bounds_edited)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(self._slider_changed)
        self.slider.sliderReleased.connect(self._submit)
        auto = QPushButton("自动")
        auto.setToolTip("按当前值和变量类型重新计算范围与步长")
        auto.clicked.connect(self._auto_range)
        slider_line.addWidget(self.min_edit)
        slider_line.addWidget(self.slider, 1)
        slider_line.addWidget(self.max_edit)
        slider_line.addWidget(auto)
        layout.addLayout(slider_line)
        self._sync_controls()

    def _format(self, value):
        return str(int(round(value))) if self.integer else f"{float(value):.7g}"

    def _slider_steps(self):
        return max(1, min(2000, int(round((self.maximum - self.minimum) / self.step))))

    def _position_value(self, position):
        steps = self._slider_steps()
        value = self.minimum + (self.maximum - self.minimum) * int(position) / steps
        return round(value) if self.integer else value

    def _value_position(self, value):
        span = self.maximum - self.minimum
        return 0 if span <= 0 else round((float(value) - self.minimum) / span * self._slider_steps())

    def _sync_controls(self):
        self.slider.blockSignals(True)
        self.slider.setRange(0, self._slider_steps())
        self.slider.setValue(max(0, min(self.slider.maximum(), self._value_position(self.value))))
        self.slider.blockSignals(False)
        self.value_edit.setText(self._format(self.value))
        self.min_edit.setText(self._format(self.minimum))
        self.max_edit.setText(self._format(self.maximum))

    def _slider_changed(self, position):
        self.value = self._position_value(position)
        self.value_edit.setText(self._format(self.value))
        self.status.setText("待写入")
        self.pending_timer.start()

    def _step_value(self, direction):
        self.value = min(self.maximum, max(self.minimum, self.value + direction * self.step))
        if self.integer:
            self.value = round(self.value)
        self._sync_controls()
        self._submit()

    def _commit_text(self):
        try:
            value = float(self.value_edit.text().strip())
        except ValueError:
            self._sync_controls()
            return
        if not np.isfinite(value):
            self._sync_controls()
            return
        if value < self.minimum or value > self.maximum:
            span = max(self.maximum - self.minimum, self.step)
            self.minimum = min(self.minimum, value - span * 0.1)
            self.maximum = max(self.maximum, value + span * 0.1)
        self.value = round(value) if self.integer else value
        self._sync_controls()
        self.state_changed.emit()
        self._submit()

    def _bounds_edited(self):
        try:
            minimum, maximum = float(self.min_edit.text()), float(self.max_edit.text())
        except ValueError:
            self._sync_controls()
            return
        if not np.isfinite(minimum) or not np.isfinite(maximum) or maximum <= minimum:
            self._sync_controls()
            return
        self.minimum, self.maximum = minimum, maximum
        self.value = min(max(self.value, minimum), maximum)
        self._sync_controls()
        self.state_changed.emit()

    def _auto_range(self):
        current = self.owner.main.latest_values.get(self.path, self.value)
        self.minimum, self.maximum, self.step = automatic_slider_profile(
            current, self.spec.get("info"), self.spec["type"]
        )
        self.value = min(max(float(current), self.minimum), self.maximum)
        self._sync_controls()
        self.state_changed.emit()

    def _submit(self):
        self.pending_timer.stop()
        self.status.setText("写入中…")
        self.latest_sequence = self.owner.main.queue_variable_write(self.path, self.value)
        if not self.latest_sequence:
            self.status.setText("链路不可用")

    def handle_result(self, sequence, success, error, value):
        if sequence != self.latest_sequence:
            return
        self.status.setText("已写入" if success else "写入失败")
        self.status.setToolTip("" if success else error)
        if success:
            self.owner.main.latest_values[self.path] = value

    def export_state(self):
        return {"path": self.path, "min": self.minimum, "max": self.maximum, "step": self.step}


class VariableSliderDialog(QDialog):
    STATE_KEY = "variable_slider_state_v1"

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.rows = {}
        self.setWindowTitle("变量滑动调节")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setMinimumWidth(360)
        add = QPushButton("添加滑条")
        add.setObjectName("primaryButton")
        refresh = QPushButton("刷新变量")
        add.clicked.connect(self.add_selected)
        refresh.clicked.connect(self.refresh_variables)
        top.addWidget(QLabel("绑定变量"))
        top.addWidget(self.selector, 1)
        top.addWidget(refresh)
        top.addWidget(add)
        layout.addLayout(top)
        note = QLabel("拖动按 100 ms 合并写入；范围与步长按变量类型和当前值自动计算，也可手动调整边界。")
        note.setObjectName("fieldHint")
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch()
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        actions = QHBoxLayout(); actions.addStretch(); actions.addWidget(close_button)
        layout.addLayout(actions)
        self.main.variable_write_result.connect(self._write_result)
        self.refresh_variables()
        self._restore()

    def refresh_variables(self):
        current = self.selector.currentData()
        self.selector.clear()
        for path in self.main.available_slider_variables():
            self.selector.addItem(path, path)
        index = self.selector.findData(current)
        if index >= 0:
            self.selector.setCurrentIndex(index)

    def add_selected(self):
        path = self.selector.currentData()
        if path:
            self.add_path(path)

    def add_path(self, path, state=None):
        if path in self.rows:
            return
        spec = self.main.variable_write_spec(path, for_slider=True)
        if not spec:
            return
        row = VariableSliderRow(self, path, spec, state)
        row.remove_requested.connect(self.remove_path)
        row.state_changed.connect(self._save)
        self.rows[path] = row
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._save()

    def remove_path(self, path):
        row = self.rows.pop(path, None)
        if row:
            row.deleteLater()
            self._save()

    def _restore(self):
        try:
            state = json.loads(self.main.db.get_config(self.STATE_KEY, "[]"))
        except (TypeError, ValueError):
            state = []
        for item in state if isinstance(state, list) else []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                self.add_path(item["path"], item)

    def _save(self):
        state = [row.export_state() for row in self.rows.values()]
        self.main.db.save_config(self.STATE_KEY, json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    def _write_result(self, path, sequence, success, error, value):
        row = self.rows.get(path)
        if row:
            row.handle_result(sequence, success, error, value)
