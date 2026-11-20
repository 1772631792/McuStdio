"""Standalone dialogs: ELF variable picker, J-Link device picker, parser helper."""

from .common import (
    ET,
    QColor,
    QDialog,
    QFont,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTimer,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    Qt,
    jlink_dll_path,
    json,
    os,
    pylink,
    pyqtSignal,
    time,
    type_label,
)
from .widgets import (
    QComboBox,
    RoundedMenu,
)


class ElfVariableDialog(QDialog):
    MAX_VISIBLE_RESULTS = 2000
    MAX_PREVIEW_ARRAY_ELEMENTS = 256
    RECENT_VARIABLES_KEY = "elf_recent_variables_v1"
    symbol_selected_signal = pyqtSignal(str, int)
    reload_requested_signal = pyqtSignal()

    def __init__(self, elf_db, parent=None):
        super().__init__(parent)
        self.elf_db = elf_db
        self.current_source = ""
        self.recent_variables = self._load_recent_variables()
        self.setWindowTitle("ELF 变量浏览器")
        self.resize(1040, 680)
        self.setMinimumSize(820, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("模糊搜索变量名、类型或源文件…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        source_panel = QFrame()
        source_panel.setObjectName("heroPanel")
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(8, 8, 8, 8)
        source_layout.setSpacing(6)
        source_header = QHBoxLayout()
        source_header.setSpacing(6)
        source_title = QLabel("源文件")
        source_title.setObjectName("sectionTitle")
        source_header.addWidget(source_title)
        source_header.addStretch()
        self.source_search = QLineEdit()
        self.source_search.setPlaceholderText("筛选文件…")
        self.source_search.setClearButtonEnabled(True)
        self.source_search.setFixedWidth(125)
        source_header.addWidget(self.source_search)
        source_layout.addLayout(source_header)
        self.source_tree = QTreeWidget()
        self.source_tree.setHeaderHidden(True)
        self.source_tree.setRootIsDecorated(False)
        self.source_tree.setAlternatingRowColors(True)
        self.source_tree.setUniformRowHeights(True)
        source_layout.addWidget(self.source_tree)
        splitter.addWidget(source_panel)

        variable_panel = QFrame()
        variable_panel.setObjectName("heroPanel")
        variable_layout = QVBoxLayout(variable_panel)
        variable_layout.setContentsMargins(8, 8, 8, 8)
        variable_layout.setSpacing(6)
        variable_title = QLabel("变量与类型")
        variable_title.setObjectName("sectionTitle")
        variable_layout.addWidget(variable_title)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["变量", "类型", "来源", "地址"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        variable_layout.addWidget(self.tree)
        splitter.addWidget(variable_panel)
        splitter.setSizes([235, 765])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_add_menu)
        self.tree.itemExpanded.connect(self._expand_item)
        buttons = QHBoxLayout()
        self.elf_status = QLabel()
        self.elf_status.setObjectName("appSubtitle")
        buttons.addWidget(self.elf_status, 1)
        reload_button = QPushButton("重新提取 ELF/AXF")
        reload_button.setToolTip("重新读取当前 ELF/AXF 的符号表和 DWARF，并刷新已有监视变量的地址与结构")
        reload_button.clicked.connect(lambda: self.reload_requested_signal.emit())
        buttons.addWidget(reload_button)
        add = QPushButton("添加到当前调试窗口")
        add.setObjectName("primaryButton")
        add.clicked.connect(lambda: self._accept_item(close_dialog=False))
        buttons.addWidget(add)
        layout.addLayout(buttons)

        self._sorted_symbol_names = []
        self._rebuild_source_index()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self._fill)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.source_search.textChanged.connect(self._fill_source_tree)
        self.source_tree.itemSelectionChanged.connect(self._source_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self._accept_item(close_dialog=False))
        self._fill()

    @staticmethod
    def _is_container(info):
        return bool(info and info.get("kind") in ("struct", "union", "array"))

    def _load_recent_variables(self):
        database = getattr(self.parent(), "db", None)
        if database is None:
            return {}
        try:
            payload = json.loads(database.get_config(self.RECENT_VARIABLES_KEY, "{}"))
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _record_recent_variable(self, root):
        database = getattr(self.parent(), "db", None)
        if database is None:
            return
        entry = self.recent_variables.get(root, {})
        self.recent_variables[root] = {
            "count": int(entry.get("count", 0)) + 1,
            "last": time.time_ns(),
        }
        if len(self.recent_variables) > 512:
            oldest = sorted(
                self.recent_variables,
                key=lambda name: int(self.recent_variables[name].get("last", 0)),
            )[:-512]
            for name in oldest:
                self.recent_variables.pop(name, None)
        database.save_config(
            self.RECENT_VARIABLES_KEY,
            json.dumps(self.recent_variables, ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _fuzzy_match(query, text):
        """Match every whitespace-separated token as an ordered subsequence."""
        folded_text = str(text).casefold()
        for token in str(query).casefold().split():
            position = 0
            for character in token:
                position = folded_text.find(character, position)
                if position < 0:
                    return False
                position += 1
        return True

    def _rebuild_source_index(self):
        self._sorted_symbol_names = sorted(self.elf_db.types, key=str.casefold)
        self._source_counts = {}
        for name in self._sorted_symbol_names:
            source = self.elf_db.types[name].get("source_file", "") or "未知来源"
            self._source_counts[source] = self._source_counts.get(source, 0) + 1

        self._fill_source_tree()

    def _fill_source_tree(self):
        query = self.source_search.text().strip() if hasattr(self, "source_search") else ""
        visible_sources = [
            source for source in sorted(self._source_counts, key=str.casefold)
            if not query or self._fuzzy_match(query, source)
        ]

        previous = self.current_source
        self.source_tree.blockSignals(True)
        self.source_tree.clear()
        all_item = QTreeWidgetItem([f"全部文件  {len(self._sorted_symbol_names)}"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, "")
        self.source_tree.addTopLevelItem(all_item)
        selected_item = all_item
        for source in visible_sources:
            item = QTreeWidgetItem([f"{source}  {self._source_counts[source]}"])
            item.setData(0, Qt.ItemDataRole.UserRole, source)
            item.setToolTip(0, source)
            self.source_tree.addTopLevelItem(item)
            if source == previous:
                selected_item = item
        self.source_tree.setCurrentItem(selected_item)
        self.current_source = selected_item.data(0, Qt.ItemDataRole.UserRole) or ""
        self.source_tree.blockSignals(False)

    def _source_changed(self):
        item = self.source_tree.currentItem()
        self.current_source = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
        self.current_source = self.current_source or ""
        self._fill()

    def _make_variable_item(self, display_name, path, root, info, address, source="", is_static=False):
        storage = "static" if is_static else "global"
        source_text = f"{storage} · {source}" if source else storage
        item = QTreeWidgetItem([display_name, type_label(info), source_text, f"0x{int(address):08X}"])
        item.setData(0, Qt.ItemDataRole.UserRole, root)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, path)
        item.setToolTip(0, path)
        if is_static:
            item.setForeground(2, QColor("#ff9f0a"))
        else:
            item.setForeground(2, QColor("#30b0c7"))
        if self._is_container(info):
            placeholder = QTreeWidgetItem(["正在准备成员…"])
            placeholder.setData(0, Qt.ItemDataRole.UserRole + 2, True)
            item.addChild(placeholder)
        return item

    def _append_child(self, parent, display_name, path, root, info, address):
        child = self._make_variable_item(display_name, path, root, info, address)
        child.setText(2, "")
        parent.addChild(child)

    def _expand_item(self, item):
        if item.childCount() != 1 or not item.child(0).data(0, Qt.ItemDataRole.UserRole + 2):
            return
        path = item.data(0, Qt.ItemDataRole.UserRole + 1)
        root = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            address, info = self.elf_db.resolve(path)
        except Exception:
            item.takeChildren()
            return
        item.takeChildren()
        if info.get("kind") == "array":
            element = info.get("element", {})
            element_size = int(element.get("size", 0) or 0)
            count = int(info.get("count", 0) or 0)
            for index in range(min(count, self.MAX_PREVIEW_ARRAY_ELEMENTS)):
                child_path = f"{path}[{index}]"
                self._append_child(item, f"[{index}]", child_path, root, element, address + index * element_size)
            if count > self.MAX_PREVIEW_ARRAY_ELEMENTS:
                tail = QTreeWidgetItem([f"… 其余 {count - self.MAX_PREVIEW_ARRAY_ELEMENTS} 项未展开"])
                tail.setDisabled(True)
                item.addChild(tail)
        elif info.get("kind") in ("struct", "union"):
            for member in info.get("members", []):
                child_path = f"{path}.{member['name']}"
                self._append_child(
                    item, member["name"], child_path, root, member["type"],
                    address + int(member.get("offset", 0)),
                )

    def _fill(self):
        keyword = self.search.text().strip().casefold()
        self.tree.clear()
        matched = 0
        self.tree.setUpdatesEnabled(False)
        symbol_names = self._sorted_symbol_names
        if not self.current_source:
            symbol_names = sorted(
                symbol_names,
                key=lambda name: (
                    -int(self.recent_variables.get(name, {}).get("last", 0)),
                    -int(self.recent_variables.get(name, {}).get("count", 0)),
                    name.casefold(),
                ),
            )
        for name in symbol_names:
            info = self.elf_db.types.get(name)
            if info is None:
                continue
            storage = "static" if info.get("is_static") else "global"
            source_file = info.get("source_file", "") or "未知来源"
            if self.current_source and source_file != self.current_source:
                continue
            searchable = f"{name} {type_label(info)} {source_file} {storage}".casefold()
            if keyword and not self._fuzzy_match(keyword, searchable):
                continue
            matched += 1
            if matched > self.MAX_VISIBLE_RESULTS:
                continue
            item = self._make_variable_item(
                name, name, name, info, self.elf_db.symbols[name], source_file, bool(info.get("is_static"))
            )
            self.tree.addTopLevelItem(item)
        self.tree.setUpdatesEnabled(True)
        shown = min(matched, self.MAX_VISIBLE_RESULTS)
        result_text = f"{shown}/{matched}" if matched > self.MAX_VISIBLE_RESULTS else str(shown)
        source_text = self.current_source or "全部文件"
        self.elf_status.setText(f"{source_text}  ·  显示 {result_text} 个变量")

    def replace_elf_database(self, elf_db):
        self.elf_db = elf_db
        self._rebuild_source_index()
        self._fill()

    def _show_add_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = RoundedMenu(self)
        menu.setStyleSheet(self.parent().popup_menu_style if hasattr(self.parent(), "popup_menu_style") else "")
        actions = [
            menu.addAction("添加到主调试窗口"),
            menu.addAction("添加到调试窗口 2"),
            menu.addAction("添加到调试窗口 3"),
        ]
        selected = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if selected in actions:
            self._accept_item(actions.index(selected), close_dialog=False)

    def _accept_item(self, target_slot=-1, close_dialog=False):
        item = self.tree.currentItem()
        root = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if root:
            self._record_recent_variable(root)
            self.symbol_selected_signal.emit(root, int(target_slot))
            target_text = "当前调试窗口" if int(target_slot) < 0 else ("主调试窗口" if int(target_slot) == 0 else f"调试窗口 {int(target_slot) + 1}")
            self.elf_status.setText(f"✓  {root} 已添加到{target_text}")
            if close_dialog:
                self.close()



class JLinkDeviceDialog(QDialog):
    """Device selector backed primarily by JFlash.exe's JLinkDevices.xml."""

    device_selected = pyqtSignal(str, str)

    def __init__(self, dll_dir, current_device="", parent=None):
        super().__init__(parent)
        self.dll_dir = os.path.dirname(dll_dir) if os.path.isfile(dll_dir) else dll_dir
        self.devices = []
        self.load_warnings = []
        self.setWindowTitle("SEGGER J-Link 芯片选择")
        self.resize(820, 650)

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.vendor_combo = QComboBox()
        self.vendor_combo.setMinimumWidth(190)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索芯片型号，例如 STM32F103 / PY32F030…")
        filters.addWidget(QLabel("厂商："))
        filters.addWidget(self.vendor_combo)
        filters.addWidget(QLabel("型号："))
        filters.addWidget(self.search_edit, 1)
        layout.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["厂商", "J-Link 芯片型号", "Flash 起始", "Flash 大小", "RAM 大小", "来源"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

        self.status = QLabel("正在读取 SEGGER 器件库…")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        select = QPushButton("使用选中芯片")
        cancel.clicked.connect(self.reject)
        select.clicked.connect(self._choose)
        buttons.addWidget(cancel)
        buttons.addWidget(select)
        layout.addLayout(buttons)

        self.vendor_combo.currentTextChanged.connect(self._filter)
        self.search_edit.textChanged.connect(self._filter)
        self.tree.itemDoubleClicked.connect(lambda *_: self._choose())
        try:
            self._load_devices()
            # Generic core names are valid J-Link fallbacks, but they are not
            # actual entries users normally want to search in the MCU list.
            if current_device and not current_device.upper().startswith("CORTEX-"):
                self.search_edit.setText(current_device)
        except Exception as exc:
            self.status.setText(f"读取 J-Link 芯片库失败：{exc}")

    @staticmethod
    def _size_text(size):
        size = int(size or 0)
        if not size: return "-"
        if size % (1024 * 1024) == 0: return f"{size // (1024 * 1024)} MB"
        if size % 1024 == 0: return f"{size // 1024} KB"
        return f"{size} B"

    def _load_devices(self):
        records = {}
        xml_path = os.path.join(self.dll_dir, "JLinkDevices.xml")
        if os.path.exists(xml_path):
            try:
                root = ET.parse(xml_path).getroot()
                last_name = None
                for element in root.iter():
                    if element.tag == "ChipInfo":
                        name = element.get("Name", "").strip()
                        if not name: continue
                        core = self._core_name(element.get("Core", ""))
                        records[name.lower()] = [element.get("Vendor", "Unspecified"), name, 0, 0,
                                                 self._parse_int(element.get("WorkRAMSize")), "JLinkDevices.xml", core]
                        last_name = name.lower()
                    elif element.tag == "FlashBankInfo" and last_name in records:
                        record = records[last_name]
                        if not record[2]: record[2] = self._parse_int(element.get("BaseAddr"))
                        record[3] += self._parse_int(element.get("MaxSize"))
            except Exception as exc:
                self.load_warnings.append(f"XML: {exc}")
        else:
            self.load_warnings.append(f"未找到 {xml_path}")

        # The DLL list supplements devices that are not present in the XML.
        if pylink is not None:
            try:
                dll_file = jlink_dll_path(self.dll_dir)
                link = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                for index in range(link.num_supported_devices()):
                    info = link.supported_device(index)
                    key = info.name.lower()
                    if key not in records:
                        records[key] = [info.manufacturer or "Unspecified", info.name, int(info.FlashAddr),
                                        int(info.FlashSize), int(info.RAMSize), "J-Link DLL", info.name]
                    else:
                        # Exact DLL support is preferred for connecting; XML-only
                        # extension devices fall back to their declared CPU core.
                        records[key][5] = "JLinkDevices.xml + DLL"
                        records[key][6] = info.name
            except Exception as exc:
                self.load_warnings.append(f"DLL: {exc}")
        if not records:
            raise RuntimeError(f"未找到器件数据库：{xml_path}")
        self.devices = list(records.values())
        vendors = sorted({record[0] for record in self.devices}, key=str.lower)
        self.vendor_combo.blockSignals(True)
        self.vendor_combo.addItem("全部厂商")
        self.vendor_combo.addItems(vendors)
        self.vendor_combo.blockSignals(False)
        self._filter()

    @staticmethod
    def _parse_int(value):
        try: return int(value or "0", 0)
        except (TypeError, ValueError): return 0

    @staticmethod
    def _core_name(value):
        core = (value or "").replace("JLINK_CORE_", "").replace("_", "-")
        aliases = {"CORTEX-M0": "Cortex-M0", "CORTEX-M0+": "Cortex-M0+", "CORTEX-M1": "Cortex-M1",
                   "CORTEX-M3": "Cortex-M3", "CORTEX-M4": "Cortex-M4", "CORTEX-M7": "Cortex-M7",
                   "CORTEX-M23": "Cortex-M23", "CORTEX-M33": "Cortex-M33", "CORTEX-M55": "Cortex-M55"}
        return aliases.get(core, core.title() if core else "")

    def _filter(self):
        vendor = self.vendor_combo.currentText()
        keyword = self.search_edit.text().strip().lower()
        self.tree.clear()
        shown = 0
        for manufacturer, name, flash_addr, flash_size, ram_size, source, connect_name in self.devices:
            if vendor and vendor != "全部厂商" and manufacturer != vendor: continue
            if keyword and keyword not in name.lower(): continue
            item = QTreeWidgetItem([manufacturer, name, f"0x{flash_addr:08X}" if flash_addr else "-",
                                    self._size_text(flash_size), self._size_text(ram_size), source])
            item.setData(0, Qt.ItemDataRole.UserRole, (name, connect_name or name))
            self.tree.addTopLevelItem(item)
            shown += 1
        xml_count = sum(record[5].startswith("JLinkDevices.xml") for record in self.devices)
        xml_path = os.path.join(self.dll_dir, "JLinkDevices.xml")
        warning = f" | 警告: {'; '.join(self.load_warnings)}" if self.load_warnings else ""
        self.status.setText(f"共 {len(self.devices)} 个型号（XML {xml_count} 个），当前显示 {shown} 个 | {xml_path}{warning}")

    def _choose(self):
        item = self.tree.currentItem()
        if not item: return
        name, connect_name = item.data(0, Qt.ItemDataRole.UserRole)
        self.device_selected.emit(name, connect_name)
        self.accept()




class AdvanceCParserDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("高级 C 语言复杂嵌套结构体智能提炼工作台")
        self.resize(750, 550)
        
        layout = QVBoxLayout()
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("例如：\ntypedef struct {\n    float Kp;\n    float Ki;\n} PID_t;\n\nPID_t motor_pid;")
        self.editor.setFont(QFont("Consolas", 11))
        layout.addWidget(self.editor)
        
        btns = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_run = QPushButton("解析并同步至全局变量表")
        btn_run.setStyleSheet("background-color: #1976d2; color: white; font-weight: bold; padding: 6px 16px;")
        btn_run.clicked.connect(self.do_parse)
        
        btns.addStretch()
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_run)
        layout.addLayout(btns)
        self.setLayout(layout)
        self.editor.setText(self.db.get_c_code())

    def do_parse(self):
        code = self.editor.toPlainText().strip()
        if not code: return
        self.db.save_c_code(code)
        self.accept()
