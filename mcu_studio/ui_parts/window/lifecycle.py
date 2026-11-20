"""Window construction, tab management and keyboard handling."""

from ..common import (
    DEFAULT_FFT_SETTINGS,
    ElfDatabase,
    QApplication,
    QButtonGroup,
    QEvent,
    QFont,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QIntValidator,
    QKeyEvent,
    QLabel,
    QLineEdit,
    QMutex,
    QProgressBar,
    QPushButton,
    QSize,
    QSplitter,
    QStyle,
    QTabWidget,
    QTimer,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
    Qt,
    SampleHistoryWriter,
    VariableWriteWorker,
    __version__,
    connect_openocd_tcl,
    jlink_dll_path,
    json,
    normalize_fft_settings,
    os,
    pg,
    threading,
    time,
)
from ..theme import (
    DARK_POPUP_MENU_STYLE,
    POPUP_MENU_STYLE,
)
from ..widgets import (
    DebugVariableTree,
    DetachableTabBar,
    FloatingDebugWindow,
    QComboBox,
    QMessageBox,
)
from ..dialogs import (
    pylink,
)


class _WindowLifecycleMixin:
    def __init__(self, config, db):
        super().__init__()
        self.config = config
        self.db = db
        self._close_destination = "exit"
        self._returning_to_config = False
        self._close_retry_count = 0
        self._session_closed_emitted = False
        self._shutdown_jlink_started = False
        self.is_dark_theme = getattr(config, 'theme_mode', 'DARK').upper() == 'DARK'
        self.theme_colors = ({
            'plot_bg': '#111315', 'axis': '#4f5660', 'axis_text': '#aab2bd',
            'crosshair': '#7f8790', 'hover_pen': '#f0f0f0', 'hover_text': '#ffffff',
            'hover_fill': '#252526', 'splitter': '#2d2d30', 'panel': '#1f1f1f',
            'panel_border': '#333337', 'neutral_button': '#2a2d2e',
            'neutral_text': '#e5e5e5', 'status_ok': '#4ec96b',
            'measurement': '#bf5af2',
        } if self.is_dark_theme else {
            'plot_bg': '#ffffff', 'axis': '#64748b', 'axis_text': '#374151',
            'crosshair': '#64748b', 'hover_pen': '#1f2937', 'hover_text': '#111827',
            'hover_fill': '#f1f5f9', 'splitter': '#dbe3ec', 'panel': '#ffffff',
            'panel_border': '#dbe3ec', 'neutral_button': '#ffffff',
            'neutral_text': '#1f2937', 'status_ok': '#2e7d32',
            'measurement': '#8e44ad',
        })
        self.popup_menu_style = DARK_POPUP_MENU_STYLE if self.is_dark_theme else POPUP_MENU_STYLE
        self.setWindowTitle(
            f"MCU Live Watch Studio V{__version__} - "
            f"[{'原生J-Link独占' if config.backend_mode=='JLINK' else 'OpenOCD驱动'}]"
        )
        self.resize(1450, 850)
        
        self.elf_db = ElfDatabase()
        self.symbol_cache = {}
        self.worker = None 
        self.low_speed_worker = None
        self.low_speed_pending = {}
        self.retired_workers = []
        self.sampling_restart_pending = False
        self.backend_close_workers = []
        self.reconnect_worker = None
        # The configuration page never claims a probe. The first backend
        # connection starts asynchronously only after the workspace is shown.
        self.backend_suspended = True
        self.backend_disconnect_reason = ""
        self.initial_backend_connect = True
        self.flash_sequence_active = False
        self.elf_reload_in_progress = False
        self.elf_update_pending = False
        self.jlink_native_instance = None 
        self.jlink_lock = threading.RLock()
        self.openocd_link_alive = False  
        self.latest_values = {}
        self.low_speed_round_robin_offset = 0
        try:
            standalone = json.loads(self.db.get_config("standalone_debug_variables_v1", "[]"))
        except (TypeError, ValueError):
            standalone = []
        self.standalone_debug_variables = {
            str(value) for value in standalone if isinstance(value, str) and ":" in value
        }
        try:
            inline_state = json.loads(self.db.get_config(self.INLINE_SLIDER_STATE_KEY, "{}"))
        except (TypeError, ValueError):
            inline_state = {}
        self.inline_slider_states = inline_state if isinstance(inline_state, dict) else {}
        
        # Sampling variables are dynamic.  CH1/CH2/CH3 are display groups,
        # not three hard limits on the number of watched waveforms.
        self.ch_paths = []
        self.ch_types = []
        self.ch_addrs = []
        self.ch_buffers = []
        self.ch_time_stamps = []
        
        self.buffer_mutex = QMutex()
        self.layout_switching = False
        self.current_layout_id = 1
        
        self.plot_active_paths = [[], [], []] 
        self.plot_curves_dict = [{}, {}, {}] 
        
        self.start_time = time.time()          
        self.max_display_duration_ms = 5000    
        self.is_frozen = False
        self.measurement_mode = False
        self.measurement_points = [[], [], []]
        self.curve_palette = [
            '#30d158', '#ff9f0a', '#0a84ff', '#bf5af2',
            '#ff375f', '#64d2ff', '#ff453a', '#ffd60a',
            '#40c8a0', '#5e5ce6', '#ac8e68', '#e66dad',
        ]
        self.curve_color_names = [
            '翠绿', '橙色', '蓝色', '紫色',
            '玫红', '天蓝', '红色', '金黄',
            '青绿', '靛蓝', '棕色', '粉色',
        ]
        self.curve_colors = {}
        self.hover_last_point = [None, None, None]
        self.canvas_fixed_ranges = [None, None, None]
        self.plot_legend_corners = ["top-left", "top-left", "top-left"]
        self.plot_modes = ["time", "time", "time"]
        self.fft_settings = [dict(DEFAULT_FFT_SETTINGS) for _ in range(3)]
        self.fft_last_update = [0.0, 0.0, 0.0]
        self.fft_results = [{}, {}, {}]
        try:
            saved_fft_state = json.loads(self.db.get_config(self.FFT_STATE_KEY, "{}"))
        except (TypeError, ValueError):
            saved_fft_state = {}
        if isinstance(saved_fft_state, dict):
            saved_modes = saved_fft_state.get("modes", [])
            saved_settings = saved_fft_state.get("settings", [])
            for plot_idx in range(3):
                if plot_idx < len(saved_modes) and saved_modes[plot_idx] in ("time", "fft"):
                    self.plot_modes[plot_idx] = saved_modes[plot_idx]
                if plot_idx < len(saved_settings):
                    self.fft_settings[plot_idx] = normalize_fft_settings(saved_settings[plot_idx])
        self.tcl_socket = None
        self.heartbeat_state = False
        self.workspace_restore_in_progress = True
        self.pending_workspace_splitter_sizes = None
        self.initial_workspace_loaded = False

        self.init_ui()
        self.variable_write_worker = VariableWriteWorker(
            self.config, self.jlink_native_instance, self.jlink_lock, self
        )
        self.variable_write_worker.finished_signal.connect(self._variable_write_finished)
        self.variable_write_worker.start()
        self.history_session_id = f"{int(time.time() * 1000)}-{os.getpid()}"
        self.history_last_saved_ms = {}
        self.history_writer = SampleHistoryWriter(
            self.db.path, self.db.active_profile_id, self
        )
        self.history_writer.error_signal.connect(self._history_write_failed)
        self.history_writer.start()
        self.workspace_save_timer = QTimer(self)
        self.workspace_save_timer.setSingleShot(True)
        self.workspace_save_timer.setInterval(250)
        self.workspace_save_timer.timeout.connect(self.save_workspace_state)
        self.main_splitter.splitterMoved.connect(lambda *_: self.schedule_workspace_save())
        self.plot_splitter.splitterMoved.connect(lambda *_: self.schedule_workspace_save())
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
        self.restore_layout_and_type_configs()

        self.timer_table = QTimer()
        self.timer_table.timeout.connect(self.low_speed_tree_refresh)
        self.timer_table.start(200)

        self.timer_plot = QTimer()
        self.timer_plot.timeout.connect(self.update_plot_canvas)
        self.timer_plot.start(30) 

        self.timer_heartbeat = QTimer()
        self.timer_heartbeat.timeout.connect(self.update_hardware_heartbeat)
        self.timer_heartbeat.start(300)

        # Watch the selected ELF itself (not its project directory).  Requiring
        # the same changed signature twice avoids parsing while the linker is
        # still replacing/writing the file.
        self.elf_last_signature = self._elf_file_signature()
        self.elf_pending_signature = None
        self.elf_pending_stable_ticks = 0
        self.elf_watch_timer = QTimer(self)
        self.elf_watch_timer.timeout.connect(self.check_elf_file_changed)
        self.elf_watch_timer.start(1000)

        elf_path = os.path.abspath(self.config.elf_path) if self.config.elf_path else ""
        if os.path.isfile(elf_path):
            QTimer.singleShot(0, lambda path=elf_path: self._start_initial_elf_load(path))
        else:
            self._finish_initial_workspace_load(synchronize_elf=False)
        QTimer.singleShot(0, self._start_initial_backend_connection)

    def _start_initial_backend_connection(self):
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self.lbl_status_text.setText("离屏自检：未占用调试后端")
            return
        self.force_reconnect_engine(initial=True)

    def init_global_link_backend(self):
        if self.backend_suspended:
            return
        if self.config.backend_mode == "OPENOCD":
            try:
                process = getattr(self.config, "openocd_process", None)
                if process is None or process.poll() is not None:
                    raise RuntimeError("OpenOCD 进程未运行")
                self.tcl_socket = connect_openocd_tcl(self.config, timeout=0.5)
                self.openocd_link_alive = True
                print("⚡ 主控 TCL 管道建立成功。")
            except Exception as e:
                self.openocd_link_alive = False
                self.tcl_socket = None
                print(f"⚠️ 管道初始连接未就绪: {e}")
        else:
            try:
                if pylink is None: return
                dll_file = jlink_dll_path(self.config.jlink_dll_dir)
                self.jlink_native_instance = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                with self.jlink_lock:
                    self.jlink_native_instance.open()
                    self.jlink_native_instance.set_tif(pylink.enums.JLinkInterfaces.SWD)
                    self.jlink_native_instance.connect(self.config.jlink_connect_device, speed=4000)
                print("⚡ 原生 J-Link 内核握手成功！")
            except Exception as e:
                self.jlink_native_instance = None
                print(f"❌ 原生 J-Link 内核打开失败: {e}")

    def get_current_tree(self):
        widget = self.tab_widget.currentWidget()
        return widget if isinstance(widget, QTreeWidget) else None

    def current_debug_slot(self):
        tree = self.get_current_tree()
        if tree is None:
            return None
        slot = tree.property("debug_tab_slot")
        return int(slot) if slot is not None else None

    @staticmethod
    def debug_slot_for_tree(tree):
        if not isinstance(tree, QTreeWidget):
            return None
        slot = tree.property("debug_tab_slot")
        return int(slot) if slot is not None else None

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Delete:
            tree = self.get_current_tree()
            if tree:
                item = tree.currentItem()
                if item:
                    root_item = item
                    while root_item.parent(): root_item = root_item.parent()
                    self.remove_variable_root(root_item)
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.ShortcutOverride,
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        ) and event.key() == Qt.Key.Key_Space:
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                self.toggle_freeze()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 6)
        layout.setSpacing(7)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(52)
        self.top_bar = top_bar
        top = QHBoxLayout(top_bar)
        top.setContentsMargins(10, 7, 10, 7)
        top.setSpacing(6)
        self.wave_hz_combo = QComboBox()
        self.wave_hz_combo.addItems([
            "自定义", "500 Hz", "1 kHz", "2 kHz", "4 kHz", "8 kHz"
        ])
        self.wave_hz_combo.setMinimumWidth(105)
        self.wave_hz_combo.currentIndexChanged.connect(self._sampling_setting_changed)
        
        self.custom_hz_edit = QLineEdit("30")
        self.custom_hz_edit.setFixedWidth(62)
        self.custom_hz_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_hz_edit.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        self.custom_hz_edit.setValidator(QIntValidator(1, 8000, self))
        self.custom_hz_edit.editingFinished.connect(self._sampling_setting_changed)
        
        self.btn_freeze = QPushButton("暂停")
        self.btn_freeze.setProperty("plotTool", True)
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setToolTip("全局空格键：暂停/继续波形显示")
        self.btn_freeze.clicked.connect(self.toggle_freeze)

        self.btn_canvas_adapt = QPushButton("适配")
        self.btn_canvas_adapt.setProperty("plotTool", True)
        self.btn_canvas_adapt.setToolTip("立即按当前时间窗内的数据适配纵轴范围")
        self.btn_canvas_adapt.clicked.connect(self.start_canvas_auto_adapt)

        self.btn_measure = QPushButton("测量")
        self.btn_measure.setProperty("plotTool", True)
        self.btn_measure.setCheckable(True)
        self.btn_measure.setToolTip("冻结波形后依次点击两个采样点，显示横轴与纵轴差值")
        self.btn_measure.clicked.connect(self.toggle_measurement_mode)
        
        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setToolTip("立即停止软件读写并在后台释放通信后端；拔掉实际线路卡住时界面不会等待")
        self.btn_disconnect.clicked.connect(self.disconnect_backend)

        self.btn_reconnect = QPushButton("重连")
        self.btn_reconnect.setProperty("commandButton", True)
        self.btn_reconnect.setToolTip("手动重新连接当前通信后端；成功后读取一次最新 ELF/AXF，不主动复位 MCU")
        self.btn_reconnect.clicked.connect(self.force_reconnect_engine)
        
        self.btn_mcu_reset = QPushButton("复位")
        self.btn_mcu_reset.setProperty("commandButton", True)
        self.btn_mcu_reset.setProperty("commandRole", "reset")
        self.btn_mcu_reset.clicked.connect(self.trigger_manual_mcu_reset)

        self.btn_flash = QPushButton("烧录")
        self.btn_flash.setProperty("commandButton", True)
        self.btn_flash.setToolTip("通过当前调试后端烧录 ELF/AXF；只有点击并确认后才会擦写 Flash/复位")
        self.btn_flash.clicked.connect(self.flash_elf_by_user)
        self.btn_flash.setEnabled(True)
        self.btn_disconnect.setProperty("commandButton", True)

        self.btn_back_config = QPushButton()
        self.btn_back_config.setObjectName("backConfigButton")
        self.btn_back_config.setFixedSize(38, 36)
        back_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack)
        if back_icon.isNull():
            self.btn_back_config.setText("←")
            self.btn_back_config.setFont(QFont("Segoe UI Symbol", 16, QFont.Weight.Bold))
        else:
            self.btn_back_config.setIcon(back_icon)
            self.btn_back_config.setIconSize(QSize(18, 18))
        self.btn_back_config.setAccessibleName("返回配置")
        self.btn_back_config.setToolTip("安全停止当前采样并返回配置；不会向 MCU 发送复位或暂停命令")
        self.btn_back_config.clicked.connect(self.return_to_configuration)

        sampling_caption = QLabel("采样")
        sampling_caption.setObjectName("fieldCaption")
        top.addWidget(sampling_caption)
        top.addWidget(self.wave_hz_combo)
        top.addWidget(self.custom_hz_edit)

        action_strip = QFrame()
        action_strip.setObjectName("actionStrip")
        action_layout = QHBoxLayout(action_strip)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(0)
        action_buttons = (self.btn_disconnect, self.btn_reconnect, self.btn_flash, self.btn_mcu_reset)
        for index, button in enumerate(action_buttons):
            position = "first" if index == 0 else ("last" if index == len(action_buttons) - 1 else "middle")
            button.setProperty("segmentPosition", position)
            button.setMinimumSize(78, 36)
            action_layout.addWidget(button)
        self.layout_group = QButtonGroup(self)
        self.rad_map1 = QPushButton("单图")
        self.rad_map2 = QPushButton("双图")
        self.rad_map3 = QPushButton("三图")
        for button in (self.rad_map1, self.rad_map2, self.rad_map3):
            button.setCheckable(True)
            button.setProperty("plotTool", True)
            button.setMinimumWidth(58)
        self.rad_map1.setProperty("viewSegment", "first")
        self.rad_map2.setProperty("viewSegment", "middle")
        self.rad_map3.setProperty("viewSegment", "last")
        self.rad_map1.setToolTip("单图（1 行独占）")
        self.rad_map2.setToolTip("双图（2 行上下均匀）")
        self.rad_map3.setToolTip("三图（3 行上下均匀）")
        self.layout_group.addButton(self.rad_map1, 1)
        self.layout_group.addButton(self.rad_map2, 2)
        self.layout_group.addButton(self.rad_map3, 3)
        self.rad_map1.setChecked(True)
        self.layout_group.idClicked.connect(self.handle_layout_changed)

        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("变量名或物理地址，例如 0x40012440")
        self.input_name.setFixedWidth(260)
        
        self.input_type = QComboBox()
        self.input_type.addItems(["uint8", "int8", "uint16", "int16", "uint32", "int32", "int", "float"])
        self.input_type.setFixedWidth(115)
        self.input_type.currentIndexChanged.connect(lambda idx: self.db.save_config("last_input_type_idx", str(idx)))
        
        btn_add = QPushButton("添加监视")
        btn_add.clicked.connect(self.quick_add_variable)
        
        btn_adv_c = QPushButton("ELF 变量浏览器")
        btn_adv_c.setObjectName("primaryButton")
        btn_adv_c.clicked.connect(self.open_advance_parser_window)
        self.btn_elf_browser = btn_adv_c

        quick_caption = QLabel("变量")
        quick_caption.setObjectName("fieldCaption")
        top.addSpacing(10)
        top.addWidget(quick_caption)
        top.addWidget(self.input_name)
        top.addWidget(self.input_type)
        top.addWidget(btn_add)
        top.addWidget(btn_adv_c)
        top.addStretch()
        top.addWidget(self.btn_back_config)
        top.addSpacing(6)
        top.addWidget(action_strip)
        layout.addWidget(top_bar)
        
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tab_widget = QTabWidget()
        detachable_tab_bar = DetachableTabBar(self.tab_widget)
        self.tab_widget.setTabBar(detachable_tab_bar)
        self.debug_trees = {}
        self.debug_titles = {}
        self.detached_debug_windows = {}
        self.tab_widget.setTabsClosable(False)
        self.tab_widget.setMovable(False)
        self.tab_widget.tabBarDoubleClicked.connect(self.detach_debug_tab)
        detachable_tab_bar.detach_requested.connect(self.detach_debug_tab)
        self.tab_widget.currentChanged.connect(lambda: (self.restart_sampling_thread(), self.schedule_workspace_save()))

        self.create_new_debug_tab("主调试窗口")
        self.create_new_debug_tab("调试窗口 2")
        self.create_new_debug_tab("调试窗口 3")
        self.tab_widget.setCurrentIndex(0)
        self.tab_widget.setToolTip("双击标签或将标签拖出可悬浮；关闭悬浮窗口后自动回到原位置。")
        self.inline_slider_widgets = {}
        self.inline_slider_contexts = {}
        self.main_splitter.addWidget(self.tab_widget)
        
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_splitter.setStyleSheet(f"QSplitter::handle {{ height: 4px; background-color: {self.theme_colors['splitter']}; }}")
        
        self.plot_widgets = []
        self.plot_legends = []
        self.v_lines = []
        self.h_lines = []
        self.hover_dots = []
        self.hover_labels = []
        self.mouse_move_proxies = []
        self.measurement_lines = []
        self.measurement_dots = []
        self.fft_summary_items = []
        self.fft_harmonic_markers = []

        for i in range(3):
            pw = pg.PlotWidget()
            pw.setBackground(self.theme_colors['plot_bg'])
            pw.showGrid(x=True, y=True, alpha=0.12)
            pw.setLabel('bottom', '时间 (ms)')
            pw.getAxis('bottom').enableAutoSIPrefix(False)
            for axis_name in ('left', 'bottom'):
                axis = pw.getAxis(axis_name)
                axis.setPen(pg.mkPen(self.theme_colors['axis']))
                axis.setTextPen(pg.mkPen(self.theme_colors['axis_text']))
            pw.getViewBox().setDefaultPadding(0.0)
            pw.getPlotItem().setMenuEnabled(False)
            pw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            pw.customContextMenuRequested.connect(lambda pos, idx=i: self.show_plot_context_menu(pos, idx))

            legend = pw.addLegend(
                offset=(10, 8),
                labelTextColor=self.theme_colors['axis_text'],
                labelTextSize='10pt',
                brush=pg.mkBrush(28, 28, 30, 205) if self.is_dark_theme else pg.mkBrush(255, 255, 255, 220),
                pen=pg.mkPen(self.theme_colors['panel_border']),
                colCount=3,
            )
            legend.setVisible(False)
            self.plot_legends.append(legend)

            fft_summary = pg.TextItem(
                anchor=(1, 0),
                color=self.theme_colors['axis_text'],
                fill=pg.mkBrush(28, 28, 30, 225) if self.is_dark_theme else pg.mkBrush(255, 255, 255, 235),
                border=pg.mkPen(self.theme_colors['panel_border']),
            )
            fft_summary.setZValue(50)
            fft_summary.setVisible(False)
            pw.addItem(fft_summary)
            self.fft_summary_items.append(fft_summary)

            harmonic_markers = pg.ScatterPlotItem(size=9, symbol='t', pen=pg.mkPen(None))
            harmonic_markers.setZValue(35)
            harmonic_markers.setVisible(False)
            pw.addItem(harmonic_markers)
            self.fft_harmonic_markers.append(harmonic_markers)
            pw.getViewBox().sigRangeChanged.connect(
                lambda *_, idx=i: self._position_fft_summary(idx)
            )

            v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(self.theme_colors['crosshair'], width=1, style=Qt.PenStyle.DashLine))
            h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(self.theme_colors['crosshair'], width=1, style=Qt.PenStyle.DashLine))
            v_line.setVisible(False)
            h_line.setVisible(False)
            pw.addItem(v_line)
            pw.addItem(h_line)
            self.v_lines.append(v_line)
            self.h_lines.append(h_line)

            hover_dot = pg.ScatterPlotItem(size=10, pen=pg.mkPen(self.theme_colors['hover_pen'], width=2))
            hover_dot.setVisible(False)
            pw.addItem(hover_dot)
            self.hover_dots.append(hover_dot)

            hover_label = pg.TextItem(anchor=(0, 1), color=self.theme_colors['hover_text'], fill=self.theme_colors['hover_fill'])
            hover_label.setVisible(False)
            pw.addItem(hover_label)
            self.hover_labels.append(hover_label)
            mouse_proxy = pg.SignalProxy(
                pw.scene().sigMouseMoved,
                delay=0.0,
                rateLimit=60,
                slot=lambda event, idx=i: self.handle_plot_mouse_move(event[0], idx),
            )
            self.mouse_move_proxies.append(mouse_proxy)

            measure_line = pg.PlotDataItem(
                pen=pg.mkPen(self.theme_colors['measurement'], width=2, style=Qt.PenStyle.DashLine)
            )
            measure_line.setZValue(30)
            pw.addItem(measure_line)
            self.measurement_lines.append(measure_line)

            measure_dots = pg.ScatterPlotItem(
                size=12,
                pen=pg.mkPen(self.theme_colors['hover_pen'], width=2),
            )
            measure_dots.setZValue(31)
            pw.addItem(measure_dots)
            self.measurement_dots.append(measure_dots)

            pw.scene().sigMouseClicked.connect(lambda event, idx=i: self.handle_measurement_click(event, idx))

            self.plot_widgets.append(pw)
            self.plot_splitter.addWidget(pw)

        for plot_idx in range(3):
            self._apply_plot_mode_visuals(plot_idx)
            
        plot_panel = QWidget()
        plot_panel_layout = QVBoxLayout(plot_panel)
        plot_panel_layout.setContentsMargins(0, 0, 0, 0)
        plot_panel_layout.setSpacing(5)

        plot_toolbar_widget = QWidget()
        plot_toolbar_widget.setObjectName("plotToolbar")
        plot_toolbar_widget.setFixedHeight(40)
        self.plot_toolbar_widget = plot_toolbar_widget
        plot_toolbar = QHBoxLayout(plot_toolbar_widget)
        plot_toolbar.setContentsMargins(0, 0, 2, 0)
        plot_toolbar.setSpacing(0)
        plot_title = QLabel("实时波形")
        plot_title.setObjectName("plotTitle")
        plot_toolbar.addSpacing(12)
        plot_toolbar.addWidget(plot_title)
        plot_toolbar.addStretch()
        plot_toolbar.addWidget(self.btn_freeze)
        plot_toolbar.addWidget(self.btn_canvas_adapt)
        plot_toolbar.addWidget(self.btn_measure)
        plot_toolbar.addSpacing(10)
        plot_toolbar.addWidget(self.rad_map1)
        plot_toolbar.addWidget(self.rad_map2)
        plot_toolbar.addWidget(self.rad_map3)
        plot_panel_layout.addWidget(plot_toolbar_widget)
        plot_panel_layout.addWidget(self.plot_splitter, 1)

        self.main_splitter.addWidget(plot_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([450, 1000])
        layout.addWidget(self.main_splitter, 1)
        
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(5, 4, 5, 4)
        self.lbl_ch1 = QLabel("CH1  空闲")
        self.lbl_ch2 = QLabel("CH2  空闲")
        self.lbl_ch3 = QLabel("CH3  空闲")
        
        ch_style = ("font-family: 'Consolas'; font-size: 13px; font-weight: bold; padding: 3px 11px; "
                    f"border-radius: 4px; background-color: {self.theme_colors['panel']}; border: 1px solid {self.theme_colors['panel_border']};")
        neutral_channel_style = f"{ch_style} color: {self.theme_colors['neutral_text']};"
        self.lbl_ch1.setStyleSheet(neutral_channel_style)
        self.lbl_ch2.setStyleSheet(neutral_channel_style)
        self.lbl_ch3.setStyleSheet(neutral_channel_style)
        for label in (self.lbl_ch1, self.lbl_ch2, self.lbl_ch3):
            label.setFixedWidth(170)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        bottom_bar.addWidget(self.lbl_ch1)
        bottom_bar.addWidget(self.lbl_ch2)
        bottom_bar.addWidget(self.lbl_ch3)
        bottom_bar.addSpacing(12)
        self.lbl_measurement_result = QLabel("测量：未开启")
        self.lbl_measurement_result.setFixedWidth(300)
        self.lbl_measurement_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_measurement_result.setStyleSheet(
            f"font-family: 'Consolas'; font-size: 12px; font-weight: 700; padding: 3px 10px; "
            f"border-radius: 4px; color: {self.theme_colors['neutral_text']}; "
            f"background-color: {self.theme_colors['panel']}; "
            f"border: 1px solid {self.theme_colors['panel_border']};"
        )
        bottom_bar.addWidget(self.lbl_measurement_result)
        bottom_bar.addStretch()

        self.flash_status_widget = QWidget()
        self.flash_status_widget.setFixedWidth(270)
        flash_status_layout = QHBoxLayout(self.flash_status_widget)
        flash_status_layout.setContentsMargins(0, 0, 0, 0)
        flash_status_layout.setSpacing(8)
        self.lbl_flash_progress = QLabel("FLASH READY")
        self.lbl_flash_progress.setFixedWidth(112)
        self.lbl_flash_progress.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.flash_progress = QProgressBar()
        self.flash_progress.setFixedSize(150, 14)
        self.flash_progress.setTextVisible(False)
        flash_status_layout.addWidget(self.lbl_flash_progress)
        flash_status_layout.addWidget(self.flash_progress)
        
        self.lbl_led = QLabel()
        self.lbl_led.setFixedSize(10, 10)
        self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
        self.lbl_status_text = QLabel("正在连接...")
        self.lbl_status_text.setStyleSheet("color: #8b949e; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")
        
        bottom_bar.addWidget(self.lbl_led)
        bottom_bar.addWidget(self.lbl_status_text)
        bottom_bar.addSpacing(20)
        bottom_bar.addWidget(self.flash_status_widget)
        
        bottom_wrapper = QWidget()
        bottom_wrapper.setFixedHeight(28) 
        bottom_wrapper.setLayout(bottom_bar)
        layout.addWidget(bottom_wrapper)
        
        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)
        self._set_flash_progress("idle")

    def create_new_debug_tab(self, name=None):
        if len(self.debug_trees) >= 3:
            QMessageBox.warning(self, "标签页上限", "最多只允许开启 3 个变量调试窗口。")
            return
        slot = next(index for index in range(3) if index not in self.debug_trees)
        if not name: name = f"调试窗口 {slot + 1}"
            
        tree = DebugVariableTree(self, slot)
        tree.setColumnCount(3)
        tree.setHeaderLabels(["全局变量", "数据类型", "实时值（5 Hz）"])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tree.header().setMinimumSectionSize(70)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.show_context_menu)
        tree.itemDoubleClicked.connect(self.handle_tree_item_double_clicked)
        tree.setToolTip("拖动变量可移动到其他调试窗口；结构体成员拖到空白处会成为独立调节项")

        new_idx = self.tab_widget.addTab(tree, name)
        tree.setProperty("debug_tab_slot", slot)
        self.debug_trees[slot] = tree
        self.debug_titles[slot] = name
        self.tab_widget.setCurrentIndex(new_idx)

    def detach_debug_tab(self, tab_index, global_pos=None):
        tree = self.tab_widget.widget(tab_index)
        slot = self.debug_slot_for_tree(tree)
        if slot is None:
            return
        if slot not in self.debug_trees or slot in self.detached_debug_windows:
            return
        title = self.debug_titles[slot]
        self.tab_widget.removeTab(tab_index)

        window = FloatingDebugWindow(slot, title, tree, self)
        window.reattach_requested.connect(self.attach_debug_tab)
        self.detached_debug_windows[slot] = window
        window.show()
        if global_pos is not None:
            window.move(global_pos.x() - 80, global_pos.y() - 18)
        window.raise_()
        window.activateWindow()

    def attach_debug_tab(self, slot):
        window = self.detached_debug_windows.pop(slot, None)
        if window is None or slot not in self.debug_trees:
            return
        tree = self.debug_trees[slot]
        title = self.debug_titles[slot]
        window._reattaching = True
        window.layout().removeWidget(tree)
        tree.setParent(None)
        insert_index = self.tab_widget.count()
        for index in range(self.tab_widget.count()):
            other_slot = self.debug_slot_for_tree(self.tab_widget.widget(index))
            if other_slot is not None and other_slot > slot:
                insert_index = index
                break
        self.tab_widget.insertTab(insert_index, tree, title)
        tree.show()
        self.tab_widget.setCurrentIndex(insert_index)
        window.close()
        window.deleteLater()
        self.restart_sampling_thread()

    def handle_tab_close_requested(self, index):
        QMessageBox.information(self, "固定调试窗口", "三个调试窗口固定保留；双击标签可以悬浮。")
