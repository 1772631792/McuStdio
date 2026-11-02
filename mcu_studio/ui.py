# MCU Studio user interface
import sys
import socket
import re
import time
import struct
import os
import xml.etree.ElementTree as ET
import bisect
import threading
import numpy as np 

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QPushButton, QComboBox, QLabel, QLineEdit, 
                             QHeaderView, QMenu, QMessageBox, QDialog, QFileDialog, 
                             QFormLayout, QCheckBox, QSplitter, QTextEdit, QRadioButton, QButtonGroup,
                             QTabWidget, QFrame, QTabBar)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, QVariantAnimation, QMutex, QMutexLocker
from PyQt6.QtGui import QFont, QKeyEvent, QIntValidator, QDoubleValidator, QColor, QAction, QPen, QIcon

import pyqtgraph as pg

from .core import (
    ElfDatabase,
    LiveWatchDB,
    ProjectConfig,
    connect_openocd_tcl,
    default_openocd_executable,
    discover_openocd_configs,
    jlink_dll_path,
    launch_openocd,
    receive_tcl_response,
    scalar_size,
    stop_managed_openocd,
    type_label,
    validate_openocd_config,
)
from .workers import BackendCloseWorker, ElfFlashWorker, HighSpeedSamplingWorker

pg.setConfigOptions(useOpenGL=False, antialias=True)

try:
    import pylink
except ImportError:
    pylink = None

DARK_STYLE = """
    QWidget { background-color: #1c1c1e; color: #f2f2f7; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
    QDialog, QMainWindow { background-color: #1c1c1e; }
    QLabel { background-color: transparent; }
    QLabel#appTitle { color: #f3f3f3; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
    QLabel#appSubtitle { color: #8b949e; font-size: 12px; }
    QLabel#sectionTitle { color: #e5e5ea; font-size: 12px; font-weight: 700; padding: 8px 0 4px 8px; border-left: 3px solid #0a84ff; }
    QLabel#fieldCaption { color: #9da5b4; font-size: 12px; font-weight: 700; }
    QLabel#toolbarSeparator { color: #3c3c3c; padding: 0 2px; }
    QFrame#topBar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 9px; }
    QLabel#plotTitle { color: #d1d1d6; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #1f1f1f; border: 1px solid #2d2d30; border-radius: 6px; }
    QFrame#statusPanel { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 10px; }
    QLineEdit, QComboBox, QTextEdit { min-height: 30px; background-color: #2c2c2e; border: 1px solid #48484a; padding: 4px 10px; color: #f2f2f7; border-radius: 8px; selection-background-color: #0a84ff; font-size: 13px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #0a84ff; background-color: #323234; }
    QComboBox::drop-down { border: 0; width: 24px; }
    QComboBox QAbstractItemView { background-color: #2c2c2e; color: #f2f2f7; border: 1px solid #48484a; selection-background-color: #0a84ff; outline: 0; }
    QCheckBox, QRadioButton { spacing: 7px; color: #d4d4d4; font-size: 13px; }
    QRadioButton:checked { color: #64b5ff; font-weight: 700; }
    QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
    QRadioButton::indicator { border: 1px solid #8e8e93; border-radius: 8px; background-color: #1c1c1e; }
    QRadioButton::indicator:checked { border: 4px solid #0a84ff; background-color: #f2f2f7; }
    QPushButton { min-height: 30px; background-color: #2c2c2e; border: 1px solid #48484a; padding: 4px 14px; color: #f2f2f7; border-radius: 8px; font-size: 13px; font-weight: 600; }
    QPushButton:hover { background-color: #3a3a3c; border-color: #636366; }
    QPushButton:pressed { background-color: #48484a; border-color: #0a84ff; }
    QPushButton:disabled { background-color: #1d1d1d; color: #626262; border-color: #2a2a2a; }
    QPushButton#primaryButton { background-color: #0e639c; border-color: #0e639c; color: #ffffff; font-weight: 700; }
    QPushButton#primaryButton:hover { background-color: #1177bb; }
    QPushButton#warningButton { background-color: #ff9f0a; border-color: #ff9f0a; color: #1c1c1e; }
    QPushButton#warningButton:hover { background-color: #ffb340; }
    QPushButton#dangerButton { background-color: #ff453a; border-color: #ff453a; color: #ffffff; }
    QPushButton#dangerButton:hover { background-color: #ff6961; border-color: #ff6961; }
    QFrame#actionStrip { background-color: transparent; border: 0; }
    QPushButton[commandButton="true"] { background-color: #2c2c2e; color: #d1d1d6; border-color: #48484a; }
    QPushButton[commandButton="true"]:hover { background-color: #3a3a3c; color: #ffffff; border-color: #636366; }
    QPushButton[commandRole="reset"]:hover { color: #ff6961; border-color: #ff6961; }
    QPushButton[plotTool="true"] { min-height: 28px; padding: 3px 14px; background-color: #242426; color: #d1d1d6; border-color: #3a3a3c; }
    QPushButton[plotTool="true"]:hover { background-color: #323234; color: #ffffff; border-color: #545458; }
    QPushButton[plotTool="true"]:checked { background-color: #094771; color: #ffffff; border-color: #007acc; }
    QPushButton[viewSegment="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[viewSegment="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[viewSegment="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[segmentPosition="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #1b1b1b; color: #666666; border-color: #292929; }
    QTreeWidget { background-color: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 10px; alternate-background-color: #202022; outline: 0; }
    QTreeWidget::item { height: 30px; border-bottom: 1px solid #242424; }
    QTreeWidget::item:hover { background-color: #2c2c2e; }
    QTreeWidget::item:selected { background-color: #094771; color: #ffffff; }
    QHeaderView::section { background-color: #242426; color: #d1d1d6; padding: 8px; border: 0; border-right: 1px solid #3a3a3c; border-bottom: 1px solid #3a3a3c; font-size: 13px; font-weight: 700; }
    QSplitter::handle { background-color: #2d2d30; }
    QTreeWidget QLineEdit { background-color: #252526 !important; border: 1px solid #007acc !important; color: #ffffff !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 4px; }
    QTabWidget::pane { border: 1px solid #3a3a3c; background-color: #1c1c1e; border-radius: 10px; top: -1px; }
    QTabBar::tab { background-color: #2c2c2e; color: #aeaeb2; border: 1px solid #3a3a3c; padding: 7px 16px; border-radius: 8px; font-weight: 600; margin: 2px; }
    QTabBar::tab:hover { background-color: #3a3a3c; color: #f2f2f7; }
    QTabBar::tab:selected { background-color: #3a3a3c; color: #64b5ff; border-color: #545458; }
    QScrollBar:vertical { background: #181818; width: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: #424242; min-height: 28px; border-radius: 5px; margin: 2px; }
    QScrollBar::handle:vertical:hover { background: #5a5a5a; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QToolTip { background-color: #252526; color: #f0f0f0; border: 1px solid #5a5a5a; padding: 5px; }
"""

LIGHT_STYLE = """
    QWidget { background-color: #f2f2f7; color: #1c1c1e; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
    QDialog, QMainWindow { background-color: #f2f2f7; }
    QLabel { background-color: transparent; }
    QLabel#appTitle { color: #172033; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
    QLabel#appSubtitle { color: #64748b; font-size: 12px; }
    QLabel#sectionTitle { color: #3a3a3c; font-size: 12px; font-weight: 700; padding: 8px 0 4px 8px; border-left: 3px solid #007aff; }
    QLabel#fieldCaption { color: #007aff; font-size: 13px; font-weight: 700; }
    QLabel#toolbarSeparator { color: #cbd5e1; padding: 0 2px; }
    QFrame#topBar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 9px; }
    QLabel#plotTitle { color: #3a3a3c; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #f8fafc; border: 1px solid #dbe3ec; border-radius: 6px; }
    QFrame#statusPanel { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; }
    QLineEdit, QComboBox, QTextEdit { min-height: 30px; background-color: #ffffff; border: 1px solid #c7c7cc; padding: 4px 10px; color: #1c1c1e; border-radius: 8px; font-size: 13px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #1976d2; background-color: #ffffff; }
    QComboBox::drop-down { border: 0; width: 26px; }
    QComboBox QAbstractItemView { background-color: #ffffff; color: #102a43; border: 1px solid #bdcad8; selection-background-color: #d9effc; selection-color: #075985; outline: 0; }
    QCheckBox, QRadioButton { spacing: 7px; color: #19324d; font-size: 13px; }
    QRadioButton:checked { color: #087fbd; font-weight: 700; }
    QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px; }
    QRadioButton::indicator { border: 1px solid #7d8ea3; border-radius: 8px; background-color: #ffffff; }
    QRadioButton::indicator:checked { border: 4px solid #0b86c9; background-color: #ffffff; }
    QPushButton { min-height: 30px; background-color: #ffffff; border: 1px solid #c7c7cc; padding: 4px 14px; color: #1c1c1e; border-radius: 8px; font-size: 13px; font-weight: 600; }
    QPushButton:hover { background-color: #eef6ff; border: 1px solid #60a5fa; }
    QPushButton:pressed { background-color: #dbeafe; }
    QPushButton:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QPushButton#primaryButton { background-color: #007aff; border-color: #007aff; color: #ffffff; font-weight: 700; }
    QPushButton#primaryButton:hover { background-color: #2188ff; }
    QPushButton#warningButton { background-color: #ff9500; border-color: #ff9500; color: #ffffff; }
    QPushButton#dangerButton { background-color: #ff3b30; border-color: #ff3b30; color: #ffffff; }
    QFrame#actionStrip { background-color: transparent; border: 0; }
    QPushButton[commandButton="true"] { background-color: #ffffff; color: #3a3a3c; border-color: #c7c7cc; }
    QPushButton[commandButton="true"]:hover { background-color: #f2f2f7; color: #1c1c1e; border-color: #8e8e93; }
    QPushButton[commandRole="reset"]:hover { color: #ff3b30; border-color: #ff3b30; }
    QPushButton[plotTool="true"] { min-height: 28px; padding: 3px 14px; background-color: #ffffff; color: #3a3a3c; border-color: #d1d1d6; }
    QPushButton[plotTool="true"]:hover { background-color: #f2f2f7; color: #1c1c1e; border-color: #8e8e93; }
    QPushButton[plotTool="true"]:checked { background-color: #007aff; color: #ffffff; border-color: #007aff; }
    QPushButton[viewSegment="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[viewSegment="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[viewSegment="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="first"] { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton[segmentPosition="middle"] { border-radius: 0; border-left: 0; }
    QPushButton[segmentPosition="last"] { border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: 0; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #f1f5f9; color: #94a3b8; border: 1px solid #e2e8f0; }
    QTreeWidget { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; alternate-background-color: #f8f8fa; }
    QTreeWidget::item { height: 30px; border-bottom: 1px solid #e7edf4; }
    QTreeWidget::item:hover { background-color: #eff6ff; }
    QTreeWidget::item:selected { background-color: #dbeafe; color: #0f3f73; }
    QHeaderView::section { background-color: #eaf0f6; color: #36516d; padding: 8px; border: 1px solid #d2dde8; font-size: 13px; font-weight: 700; }
    QSplitter::handle { background-color: #dbe3ec; }
    QTreeWidget QLineEdit { background-color: #ffffff !important; border: 2px solid #1976d2 !important; color: #111827 !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 3px; }
    QTabWidget::pane { border: 1px solid #d1d1d6; background-color: #ffffff; border-radius: 10px; top: -1px; }
    QTabBar::tab { background-color: #e5e5ea; color: #636366; border: 1px solid #d1d1d6; padding: 7px 16px; border-radius: 8px; font-weight: 600; margin: 2px; }
    QTabBar::tab:hover { background-color: #dcdce1; color: #1c1c1e; }
    QTabBar::tab:selected { background-color: #ffffff; color: #007aff; border-color: #b8b8bd; }
    QToolTip { background-color: #ffffff; color: #111827; border: 1px solid #94a3b8; padding: 4px; }
"""

POPUP_MENU_STYLE = """
    QMenu { background-color: #ffffff; color: #1f2937; border: 1px solid #cbd5e1; padding: 6px; font-family: "Microsoft YaHei", "Consolas"; font-size: 13px; font-weight: bold; }
    QMenu::item { padding: 6px 28px 6px 16px; border-radius: 4px; background-color: transparent; color: #1f2937; }
    QMenu::item:selected { background-color: #dbeafe; color: #0f3f73; }
    QMenu::separator { height: 1px; background-color: #dbe3ec; margin: 4px 0px; }
"""

DARK_POPUP_MENU_STYLE = """
    QMenu { background-color: #252526; color: #d4d4d4; border: 1px solid #454545; padding: 5px; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 12px; }
    QMenu::item { padding: 6px 28px 6px 14px; border-radius: 2px; background-color: transparent; }
    QMenu::item:selected { background-color: #094771; color: #ffffff; }
    QMenu::separator { height: 1px; background-color: #454545; margin: 4px 0px; }
"""


def application_theme_stylesheet(theme_mode):
    return LIGHT_STYLE if str(theme_mode).upper() == "LIGHT" else DARK_STYLE


class DetachableTabBar(QTabBar):
    """Tab bar that detaches a page when its tab is dragged outside the bar."""

    detach_requested = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pressed_index = -1
        self._press_global_pos = None
        self._dragging_out = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_index = self.tabAt(event.position().toPoint())
            self._press_global_pos = event.globalPosition().toPoint()
            self._dragging_out = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed_index >= 0 and self._press_global_pos is not None:
            distance = (event.globalPosition().toPoint() - self._press_global_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._dragging_out = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        detach_index = self._pressed_index
        should_detach = (
            event.button() == Qt.MouseButton.LeftButton
            and self._dragging_out
            and detach_index >= 0
            and not self.rect().contains(event.position().toPoint())
        )
        global_pos = event.globalPosition().toPoint()
        self._pressed_index = -1
        self._press_global_pos = None
        self._dragging_out = False
        if should_detach:
            self.detach_requested.emit(detach_index, global_pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FloatingDebugWindow(QDialog):
    reattach_requested = pyqtSignal(int)

    def __init__(self, slot, title, tree, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.slot = slot
        self._reattaching = False
        self.setWindowTitle(f"{title} — 悬浮调试")
        self.resize(620, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        tree.setParent(self)
        layout.addWidget(tree)
        # QTabWidget.removeTab() hides its page.  Reparenting alone does not
        # reliably clear that explicit hidden state, so make the complete
        # interactive watch tree visible in the floating window.
        tree.show()

    def closeEvent(self, event):
        if not self._reattaching:
            self.reattach_requested.emit(self.slot)
        event.accept()

    def keyPressEvent(self, event):
        owner = self.parent()
        tree = owner.debug_trees.get(self.slot) if owner and hasattr(owner, "debug_trees") else None
        if event.key() == Qt.Key.Key_Delete and tree is not None:
            item = tree.currentItem()
            if item is not None:
                while item.parent():
                    item = item.parent()
                owner.remove_variable_root(item, tree)
            return
        if event.key() == Qt.Key.Key_Space and owner and hasattr(owner, "toggle_freeze"):
            owner.toggle_freeze()
            return
        super().keyPressEvent(event)



class ElfVariableDialog(QDialog):
    MAX_VISIBLE_RESULTS = 2000
    symbol_selected_signal = pyqtSignal(str, int)
    reload_requested_signal = pyqtSignal()
    def __init__(self, elf_db, parent=None):
        super().__init__(parent); self.elf_db = elf_db
        self.setWindowTitle("ELF 变量浏览器（符号表 + DWARF）"); self.resize(780, 620)
        layout = QVBoxLayout(self); self.search = QLineEdit(); self.search.setPlaceholderText("搜索 ELF 全局变量…"); layout.addWidget(self.search)
        self.elf_status = QLabel(); layout.addWidget(self.elf_status)
        self.tree = QTreeWidget(); self.tree.setColumnCount(4); self.tree.setHeaderLabels(["变量", "DWARF 类型", "地址", "存储属性"]); self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); layout.addWidget(self.tree)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_add_menu)
        buttons = QHBoxLayout()
        reload_button = QPushButton("重新提取当前 ELF")
        reload_button.setToolTip("重新读取当前 ELF 的符号表和 DWARF，并刷新已有监视变量的地址与结构")
        reload_button.clicked.connect(lambda: self.reload_requested_signal.emit())
        buttons.addWidget(reload_button); buttons.addStretch()
        add = QPushButton("添加到当前调试窗口"); add.clicked.connect(lambda: self._accept_item()); buttons.addWidget(add); layout.addLayout(buttons)
        self._sorted_symbol_names = sorted(self.elf_db.symbols, key=str.casefold)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search_timer.timeout.connect(self._fill)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.tree.itemDoubleClicked.connect(lambda *_: self._accept_item()); self._fill()
    def _fill(self):
        keyword = self.search.text().strip().lower(); self.tree.clear()
        matched = 0
        self.tree.setUpdatesEnabled(False)
        for name in self._sorted_symbol_names:
            info = self.elf_db.types.get(name)
            storage = "static" if info and info.get("is_static") else "global"
            source_file = info.get("source_file", "") if info else ""
            storage_text = f"{storage} · {source_file}" if source_file else storage
            if info is None or (keyword and keyword not in name.lower() and keyword not in type_label(info).lower() and keyword not in storage_text.lower()): continue
            matched += 1
            if matched > self.MAX_VISIBLE_RESULTS: continue
            item = QTreeWidgetItem([name, type_label(info), f"0x{self.elf_db.symbols[name]:08X}", storage_text]); item.setData(0, Qt.ItemDataRole.UserRole, name); self.tree.addTopLevelItem(item)
        self.tree.setUpdatesEnabled(True)
        shown = min(matched, self.MAX_VISIBLE_RESULTS)
        result_text = f"，当前显示 {shown}/{matched} 个匹配项" if matched > self.MAX_VISIBLE_RESULTS else f"，当前显示 {shown} 个匹配项"
        self.elf_status.setText(
            f"ELF：{self.elf_db.path or '未加载'}  |  "
            f"符号 {len(self.elf_db.symbols)} 个，DWARF 变量 {len(self.elf_db.types)} 个{result_text}"
        )
    def replace_elf_database(self, elf_db):
        self.elf_db = elf_db
        self._sorted_symbol_names = sorted(self.elf_db.symbols, key=str.casefold)
        self._fill()
    def _show_add_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.setStyleSheet(self.parent().popup_menu_style if hasattr(self.parent(), "popup_menu_style") else "")
        actions = [
            menu.addAction("添加到主调试窗口"),
            menu.addAction("添加到调试窗口 2"),
            menu.addAction("添加到调试窗口 3"),
        ]
        selected = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if selected in actions:
            self._accept_item(actions.index(selected), close_dialog=False)

    def _accept_item(self, target_slot=-1, close_dialog=True):
        item = self.tree.currentItem()
        if item:
            self.symbol_selected_signal.emit(item.data(0, Qt.ItemDataRole.UserRole), int(target_slot))
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

class LoginConfigDialog(QDialog):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.config = ProjectConfig()
        self.is_connected = False
        self.setWindowTitle("MCU Live Watch Studio")
        self.resize(780, 620)
        self.init_ui()
        self.load_saved_config()
        self.toggle_engine_fields()

        self.detect_timer = QTimer(self)
        self.detect_timer.timeout.connect(self.static_env_check)
        self.detect_timer.start(1000)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        appearance_section = QLabel("外观与固件")
        appearance_section.setObjectName("sectionTitle")
        form.addRow(appearance_section)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["深色模式（默认）", "浅色模式"])
        self.theme_combo.currentIndexChanged.connect(self.apply_selected_theme)
        form.addRow("界面主题", self.theme_combo)
        
        self.elf_edit = QLineEdit()
        btn_elf = QPushButton("浏览…")
        btn_elf.clicked.connect(lambda: self.select_file(self.elf_edit, "选择 ELF 固件", "ELF Files (*.elf)"))
        h1 = QHBoxLayout(); h1.addWidget(self.elf_edit); h1.addWidget(btn_elf)
        form.addRow("ELF 固件", h1)

        backend_section = QLabel("调试后端")
        backend_section.setObjectName("sectionTitle")
        form.addRow(backend_section)

        self.driver_mode_combo = QComboBox()
        self.driver_mode_combo.addItems(["J-Link 原生驱动", "ST-Link / CMSIS-DAP（OpenOCD）"])
        self.driver_mode_combo.currentIndexChanged.connect(self.toggle_engine_fields)
        form.addRow("后端类型", self.driver_mode_combo)

        self.lbl_jl_dir = QLabel("J-Link 安装目录")
        self.jlink_dir_edit = QLineEdit()
        self.btn_jl_dir = QPushButton("浏览…")
        self.btn_jl_dir.clicked.connect(self.select_jlink_dir)
        self.h_jl_dir = QHBoxLayout(); self.h_jl_dir.addWidget(self.jlink_dir_edit); self.h_jl_dir.addWidget(self.btn_jl_dir)

        self.lbl_jl_dev = QLabel("目标器件")
        self.jlink_device_edit = QLineEdit("Cortex-M0+")
        self.jlink_device_edit.textEdited.connect(lambda text: self.jlink_device_edit.setProperty("connect_device", text.strip()))
        self.btn_jl_device = QPushButton("选择芯片…")
        self.btn_jl_device.clicked.connect(self.open_jlink_device_selector)
        self.h_jl_device = QHBoxLayout(); self.h_jl_device.addWidget(self.jlink_device_edit); self.h_jl_device.addWidget(self.btn_jl_device)

        self.lbl_jl_svd = QLabel("SVD（可选）")
        self.svd_edit = QLineEdit()
        self.svd_edit.setPlaceholderText("可留空：不影响 ELF 烧录、DWARF 变量或波形调试")
        self.svd_edit.setToolTip("SVD 只描述外设寄存器名称/位域；J-Link Flash 算法来自所选 DLL/XML，与 SVD 无关。")
        self.btn_svd = QPushButton("浏览…")
        self.btn_svd.clicked.connect(lambda: self.select_file(self.svd_edit, "选择外设 SVD 描述文件", "SVD Files (*.svd *.txt)"))
        self.btn_clear_svd = QPushButton("清空")
        self.btn_clear_svd.setToolTip("清除上一颗芯片遗留的 SVD；不影响烧录和调试")
        self.btn_clear_svd.clicked.connect(self.svd_edit.clear)
        self.h_jl_svd = QHBoxLayout(); self.h_jl_svd.addWidget(self.svd_edit); self.h_jl_svd.addWidget(self.btn_svd); self.h_jl_svd.addWidget(self.btn_clear_svd)

        form.addRow(self.lbl_jl_dir, self.h_jl_dir)
        form.addRow(self.lbl_jl_dev, self.h_jl_device)
        form.addRow(self.lbl_jl_svd, self.h_jl_svd)

        self.lbl_ocd_exe = QLabel("OpenOCD 程序")
        self.openocd_edit = QLineEdit()
        self.openocd_edit.editingFinished.connect(self.refresh_openocd_cfgs)
        self.btn_ocd = QPushButton("浏览…")
        self.btn_ocd.clicked.connect(self.select_openocd_executable)
        self.h_ocd_exe = QHBoxLayout(); self.h_ocd_exe.addWidget(self.openocd_edit); self.h_ocd_exe.addWidget(self.btn_ocd)

        self.lbl_ocd_intf = QLabel("探针配置")
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.btn_browse_intf = QPushButton("浏览…")
        self.btn_browse_intf.clicked.connect(lambda: self.select_cfg_file(self.interface_combo, "interface"))
        self.h_ocd_intf = QHBoxLayout(); self.h_ocd_intf.addWidget(self.interface_combo); self.h_ocd_intf.addWidget(self.btn_browse_intf)

        self.lbl_ocd_targ = QLabel("目标配置")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.btn_browse_target = QPushButton("浏览…")
        self.btn_browse_target.clicked.connect(lambda: self.select_cfg_file(self.target_combo, "target"))
        self.h_ocd_targ = QHBoxLayout(); self.h_ocd_targ.addWidget(self.target_combo); self.h_ocd_targ.addWidget(self.btn_browse_target)

        form.addRow(self.lbl_ocd_exe, self.h_ocd_exe)
        form.addRow(self.lbl_ocd_intf, self.h_ocd_intf)
        form.addRow(self.lbl_ocd_targ, self.h_ocd_targ)

        self.log_checkbox = QCheckBox("在独立窗口显示 OpenOCD 日志")
        form.addRow("调试日志", self.log_checkbox)

        layout.addLayout(form)
        status_panel = QFrame()
        status_panel.setObjectName("statusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.lbl_hardware_status = QLabel("正在检查本机调试环境…")
        self.lbl_hardware_status.setStyleSheet("color: #cca700; font-weight: 600;")
        status_layout.addWidget(self.lbl_hardware_status)
        layout.addWidget(status_panel)
        
        btns = QHBoxLayout()
        self.btn_connect = QPushButton("连接并进入工作站")
        self.btn_connect.setObjectName("primaryButton")
        self.btn_connect.setMinimumHeight(48)
        self.btn_connect.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.btn_connect.clicked.connect(self.handle_connect)
        btns.addWidget(self.btn_connect)
        layout.addLayout(btns)
        self.setLayout(layout)

    def toggle_engine_fields(self):
        is_jlink = (self.driver_mode_combo.currentIndex() == 0)
        self.jlink_dir_edit.setEnabled(is_jlink)
        self.btn_jl_dir.setEnabled(is_jlink)
        self.jlink_device_edit.setEnabled(is_jlink)
        self.btn_jl_device.setEnabled(is_jlink)
        self.svd_edit.setEnabled(is_jlink)
        self.btn_svd.setEnabled(is_jlink)
        self.btn_clear_svd.setEnabled(is_jlink)

        self.openocd_edit.setEnabled(not is_jlink)
        self.btn_ocd.setEnabled(not is_jlink)
        self.interface_combo.setEnabled(not is_jlink)
        self.btn_browse_intf.setEnabled(not is_jlink)
        self.target_combo.setEnabled(not is_jlink)
        self.btn_browse_target.setEnabled(not is_jlink)
        self.log_checkbox.setEnabled(not is_jlink)
        self.static_env_check()

    def apply_selected_theme(self):
        theme_mode = "LIGHT" if self.theme_combo.currentIndex() == 1 else "DARK"
        self.config.theme_mode = theme_mode
        app = QApplication.instance()
        if app: app.setStyleSheet(application_theme_stylesheet(theme_mode))
        self.db.save_config("theme_mode", theme_mode)
        if hasattr(self, 'lbl_hardware_status'): self.static_env_check()

    def static_env_check(self):
        is_jlink_mode = (self.driver_mode_combo.currentIndex() == 0)
        is_dark = self.config.theme_mode == "DARK"
        error_color = "#ff6b6b" if is_dark else "#c62828"
        success_color = "#69db7c" if is_dark else "#2e7d32"
        info_color = "#64b5f6" if is_dark else "#0277bd"
        warning_color = "#ffd166" if is_dark else "#b45309"
        if is_jlink_mode:
            if pylink is None:
                self.lbl_hardware_status.setText("未检测到 pylink 库，请执行 pip install pylink-square")
                self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")
            else:
                try:
                    jlink_dll_path(self.jlink_dir_edit.text().strip())
                    self.lbl_hardware_status.setText("J-Link 原生运行环境与 DLL 位数匹配")
                    self.lbl_hardware_status.setStyleSheet(f"color: {success_color}; font-weight: bold;")
                except Exception as exc:
                    self.lbl_hardware_status.setText(str(exc))
                    self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")
        else:
            try:
                details = validate_openocd_config(self._openocd_form_config())
                if sys.platform == "win32" and self._openocd_uses_jlink(details):
                    self.lbl_hardware_status.setText(
                        "OpenOCD 的 J-Link 后端需要 libusb 驱动；SEGGER 官方驱动请使用原生 J-Link 模式。"
                    )
                    self.lbl_hardware_status.setStyleSheet(f"color: {warning_color}; font-weight: bold;")
                else:
                    self.lbl_hardware_status.setText("OpenOCD、interface 和 target 配置均有效")
                    self.lbl_hardware_status.setStyleSheet(f"color: {success_color}; font-weight: bold;")
            except Exception as exc:
                self.lbl_hardware_status.setText(str(exc))
                self.lbl_hardware_status.setStyleSheet(f"color: {error_color}; font-weight: bold;")

    def _openocd_form_config(self):
        config = ProjectConfig()
        config.backend_mode = "OPENOCD"
        config.openocd_exe = self.openocd_edit.text().strip()
        config.interface = self._combo_config_value(self.interface_combo)
        config.target = self._combo_config_value(self.target_combo)
        config.show_log_window = self.log_checkbox.isChecked()
        return config

    @staticmethod
    def _combo_config_value(combo):
        index = combo.currentIndex()
        text = combo.currentText().strip()
        if index >= 0 and text == combo.itemText(index):
            return str(combo.itemData(index) or text).strip()
        return text

    @staticmethod
    def _set_combo_config_value(combo, value):
        value = str(value or "").strip()
        for index in range(combo.count()):
            if str(combo.itemData(index) or combo.itemText(index)).casefold() == value.casefold():
                combo.setCurrentIndex(index)
                return
        if value: combo.setEditText(value)

    @staticmethod
    def _openocd_uses_jlink(details):
        if os.path.basename(details["interface"]).casefold() == "jlink.cfg":
            return True
        try:
            with open(details["target"], "r", encoding="utf-8", errors="replace") as stream:
                target_text = stream.read(65536)
        except OSError:
            return False
        return bool(re.search(
            r"(?im)^\s*(?:adapter\s+driver|interface)\s+jlink\b|interface[/\\]jlink\.cfg",
            target_text,
        ))

    @staticmethod
    def _friendly_openocd_error(error):
        message = str(error)
        upper = message.upper()
        if "LIBUSB_ERROR_NOT_SUPPORTED" in upper and "NO J-LINK DEVICE FOUND" in upper:
            message += (
                "\n\n当前 J-Link 使用的是 SEGGER 官方 Windows 驱动，原生 J-Link 模式可以正常使用；"
                "OpenOCD 的 J-Link 后端通过 libusb 访问，无法打开这个驱动。"
                "请切换到“J-Link 原生内核驱动”，或改用 DAPLink/ST-Link 运行 OpenOCD。"
                "不建议为了 OpenOCD 替换 J-Link USB 驱动，否则可能导致 SEGGER DLL/J-Link Commander 失效。"
            )
        return message

    def select_file(self, edit, title, flt):
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if path: edit.setText(os.path.normpath(path))

    def select_openocd_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 openocd.exe", "", "Executable (*.exe)")
        if path:
            self.openocd_edit.setText(os.path.normpath(path))
            self.refresh_openocd_cfgs()

    def refresh_openocd_cfgs(self):
        """Show common cfg choices; the browse buttons cover every other cfg."""
        executable = self.openocd_edit.text().strip()
        for combo, subfolder in ((self.interface_combo, "interface"), (self.target_combo, "target")):
            current = self._combo_config_value(combo)
            discovered = discover_openocd_configs(executable, subfolder) if executable else []
            preferred = (
                [
                    ("ST-Link", "stlink.cfg"),
                    ("DAPLink / CMSIS-DAP", "cmsis-dap.cfg"),
                    ("J-Link（OpenOCD/libusb）", "jlink.cfg"),
                ]
                if subfolder == "interface"
                else [
                    ("STM32 F1", "stm32f1x.cfg"),
                    ("STM32 F2", "stm32f2x.cfg"),
                    ("STM32 F3", "stm32f3x.cfg"),
                    ("STM32 F4", "stm32f4x.cfg"),
                    ("STM32 H7", "stm32h7x.cfg"),
                ]
            )
            available = {item.casefold(): item for item in discovered}
            combo.blockSignals(True)
            combo.clear()
            for label, config_name in preferred:
                actual = available.get(config_name.casefold())
                if actual: combo.addItem(f"{label}  ({actual})", actual)
            self._set_combo_config_value(combo, current)
            combo.blockSignals(False)
            combo.setToolTip(
                f"显示 {combo.count()} 个常用项；OpenOCD 内共发现 {len(discovered)} 个 cfg，其他型号请点右侧浏览按钮。"
            )

    def select_jlink_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 SEGGER J-Link 安装根目录")
        if path: 
            self.jlink_dir_edit.setText(os.path.normpath(path))
            self.static_env_check()

    def open_jlink_device_selector(self):
        dll_dir = self.jlink_dir_edit.text().strip()
        dialog = JLinkDeviceDialog(dll_dir, self.jlink_device_edit.text().strip(), self)
        dialog.device_selected.connect(self.apply_jlink_device_selection)
        dialog.exec()

    def apply_jlink_device_selection(self, display_name, connect_name):
        previous_device = self.jlink_device_edit.text().strip()
        self.jlink_device_edit.setText(display_name)
        self.jlink_device_edit.setProperty("connect_device", connect_name)
        if connect_name != display_name:
            self.jlink_device_edit.setToolTip(f"XML 扩展器件；实时调试按内核 {connect_name} 连接")
        else:
            self.jlink_device_edit.setToolTip("")
        if previous_device and previous_device.casefold() != display_name.casefold() and self.svd_edit.text().strip():
            self.svd_edit.clear()
            self.svd_edit.setPlaceholderText(f"已从 {previous_device} 切换到 {display_name}；旧 SVD 已清除（SVD 不影响烧录）")

    def select_cfg_file(self, combo, subfolder):
        exe_path = self.openocd_edit.text().strip()
        initial_dir = os.path.join(os.path.dirname(exe_path), "..", "share", "openocd", "scripts", subfolder)
        if not os.path.isdir(initial_dir): initial_dir = ""
        path, _ = QFileDialog.getOpenFileName(self, f"选择自定义 {subfolder} 配置文件", initial_dir, "Config Files (*.cfg)")
        if path: combo.setEditText(os.path.normpath(path))

    def load_saved_config(self):
        self.elf_edit.setText(self.db.get_config("elf_path"))
        saved_openocd = self.db.get_config("openocd_exe", "").strip() or default_openocd_executable()
        self.openocd_edit.setText(saved_openocd)
        self.refresh_openocd_cfgs()
        self.jlink_dir_edit.setText(self.db.get_config("jlink_dll_dir", "C:\\Program Files\\SEGGER\\JLink"))
        self.jlink_device_edit.setText(self.db.get_config("jlink_device", "Cortex-M0+"))
        self.jlink_device_edit.setProperty("connect_device", self.db.get_config("jlink_connect_device", self.jlink_device_edit.text()))
        self.svd_edit.setText(self.db.get_config("svd_path"))
        saved_theme = self.db.get_config("theme_mode", "DARK").upper()
        self.theme_combo.setCurrentIndex(1 if saved_theme == "LIGHT" else 0)
        self.apply_selected_theme()
        
        saved_mode = self.db.get_config("backend_mode", "JLINK")
        self.driver_mode_combo.setCurrentIndex(0 if saved_mode == "JLINK" else 1)
        
        saved_intf = self.db.get_config("last_interface_cfg")
        if saved_intf: self._set_combo_config_value(self.interface_combo, saved_intf)
        saved_targ = self.db.get_config("last_target_cfg")
        if saved_targ: self._set_combo_config_value(self.target_combo, saved_targ)

    def handle_connect(self):
        if hasattr(self, 'detect_timer'): self.detect_timer.stop()
        self.config.elf_path = self.elf_edit.text().strip()
        self.config.backend_mode = "JLINK" if self.driver_mode_combo.currentIndex() == 0 else "OPENOCD"
        self.config.theme_mode = "LIGHT" if self.theme_combo.currentIndex() == 1 else "DARK"

        if not os.path.isfile(self.config.elf_path):
            QMessageBox.warning(self, "配置无效", f"找不到 ELF 文件：\n{self.config.elf_path or '(未设置)'}")
            return

        if self.config.backend_mode == "OPENOCD":
            self.config.openocd_exe = self.openocd_edit.text().strip()
            self.config.interface = self._combo_config_value(self.interface_combo)
            self.config.target = self._combo_config_value(self.target_combo)
            self.config.show_log_window = self.log_checkbox.isChecked()

            try:
                details = validate_openocd_config(self.config)
                driver_note = ""
                if sys.platform == "win32" and self._openocd_uses_jlink(details):
                    driver_note = (
                        "\n\n注意：Windows 下 OpenOCD 的 J-Link 后端需要 libusb 驱动。"
                        "如果当前使用 SEGGER 官方驱动，请取消并选择原生 J-Link 模式。"
                    )
                answer = QMessageBox.warning(
                    self,
                    "确认启动 OpenOCD",
                    "OpenOCD 加载 target cfg 时会初始化目标芯片；是否触发复位取决于所选 cfg、"
                    "调试器和接线。\n\n请确认当前硬件处于安全状态，并且 target cfg 与芯片完全匹配。"
                    + driver_note,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                stop_managed_openocd(self.config)
                launch_openocd(self.config)
            except Exception as exc:
                message = self._friendly_openocd_error(exc)
                self.lbl_hardware_status.setText(f"OpenOCD 启动失败：{message}")
                QMessageBox.critical(self, "OpenOCD 启动失败", message)
                return
        else: 
            jlink_dir = self.jlink_dir_edit.text().strip()
            self.config.jlink_dll_dir = os.path.dirname(jlink_dir) if os.path.isfile(jlink_dir) else jlink_dir
            self.config.jlink_device = self.jlink_device_edit.text().strip()
            self.config.jlink_connect_device = self.jlink_device_edit.property("connect_device") or self.config.jlink_device
            self.config.svd_path = self.svd_edit.text().strip()
            try:
                if pylink is None:
                    raise RuntimeError("缺少 pylink-square，请先安装依赖")
                jlink_dll_path(self.config.jlink_dll_dir)
                if not self.config.jlink_device or not self.config.jlink_connect_device:
                    raise ValueError("J-Link 芯片型号不能为空")
            except Exception as exc:
                self.lbl_hardware_status.setText(f"J-Link 配置无效：{exc}")
                QMessageBox.critical(self, "J-Link 配置无效", str(exc))
                return

        self.db.save_config("elf_path", self.config.elf_path)
        self.db.save_config("backend_mode", self.config.backend_mode)
        self.db.save_config("theme_mode", self.config.theme_mode)
        if self.config.backend_mode == "OPENOCD":
            self.db.save_config("openocd_exe", self.config.openocd_exe)
            self.db.save_config("last_interface_cfg", self.config.interface)
            self.db.save_config("last_target_cfg", self.config.target)
        else:
            self.db.save_config("jlink_dll_dir", self.config.jlink_dll_dir)
            self.db.save_config("jlink_device", self.config.jlink_device)
            self.db.save_config("jlink_connect_device", self.config.jlink_connect_device)
            self.db.save_config("svd_path", self.config.svd_path)

        self.is_connected = True
        self.accept()


class InPlaceEditLineEdit(QLineEdit):
    editing_finished_signal = pyqtSignal(str)

    def __init__(self, parent, initial_text, val_type, type_info=None):
        super().__init__(parent)
        self.setText(initial_text)
        if type_info and type_info.get("kind") == "enum":
            self.setPlaceholderText("可输入枚举名或数值")
        elif type_info and (type_info.get("kind") == "pointer" or int(type_info.get("encoding", 0) or 0) == 0x02):
            self.setPlaceholderText("可输入 true/false 或 0x 数值")
        elif (type_info and int(type_info.get("encoding", 0) or 0) == 0x04) or "float" in val_type or "double" in val_type:
            self.setValidator(QDoubleValidator(self))
        else:
            self.setValidator(QIntValidator(-2147483648, 2147483647, self))
        self.selectAll()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editing_finished_signal.emit(self.text().strip())
        elif event.key() == Qt.Key.Key_Escape:
            self.editing_finished_signal.emit("")  
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.editing_finished_signal.emit(self.text().strip())
        super().focusOutEvent(event)




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


class MCUStudioWindow(QMainWindow):
    def __init__(self, config, db):
        super().__init__()
        self.config = config
        self.db = db
        self.is_dark_theme = getattr(config, 'theme_mode', 'DARK').upper() == 'DARK'
        self.theme_colors = ({
            'plot_bg': '#111315', 'axis': '#4f5660', 'axis_text': '#aab2bd',
            'crosshair': '#7f8790', 'hover_pen': '#f0f0f0', 'hover_text': '#ffffff',
            'hover_fill': '#252526', 'splitter': '#2d2d30', 'panel': '#1f1f1f',
            'panel_border': '#333337', 'neutral_button': '#2a2d2e',
            'neutral_text': '#e5e5e5', 'status_ok': '#4ec96b',
            'channels': ['#36c96b', '#f0a020', '#36b7d9'],
        } if self.is_dark_theme else {
            'plot_bg': '#ffffff', 'axis': '#64748b', 'axis_text': '#374151',
            'crosshair': '#64748b', 'hover_pen': '#1f2937', 'hover_text': '#111827',
            'hover_fill': '#f1f5f9', 'splitter': '#dbe3ec', 'panel': '#ffffff',
            'panel_border': '#dbe3ec', 'neutral_button': '#ffffff',
            'neutral_text': '#1f2937', 'status_ok': '#2e7d32',
            'channels': ['#008a3b', '#d97706', '#0284c7'],
        })
        self.popup_menu_style = DARK_POPUP_MENU_STYLE if self.is_dark_theme else POPUP_MENU_STYLE
        self.setWindowTitle(f"MCU Live Watch Studio - [{'原生J-Link独占' if config.backend_mode=='JLINK' else 'OpenOCD驱动'}]")
        self.resize(1450, 850)
        
        self.elf_db = ElfDatabase()
        self.symbol_cache = {}
        self.worker = None 
        self.retired_workers = []
        self.backend_close_workers = []
        self.backend_suspended = False
        self.flash_sequence_active = False
        self.elf_reload_in_progress = False
        self.jlink_native_instance = None 
        self.jlink_lock = threading.RLock()
        self.openocd_link_alive = False  
        
        self.ch_paths = [None, None, None]     
        self.ch_types = [None, None, None]     
        self.ch_addrs = [None, None, None]     
        self.ch_buffers = [[], [], []]         
        self.ch_time_stamps = [[], [], []]     
        
        self.buffer_mutex = QMutex()
        self.layout_switching = False
        self.current_layout_id = 1
        
        self.plot_active_paths = [[], [], []] 
        self.plot_curves_dict = [{}, {}, {}] 
        
        self.start_time = time.time()          
        self.max_display_duration_ms = 5000    
        self.is_frozen = False
        self.canvas_fixed_ranges = [None, None, None]
        self.tcl_socket = None
        self.heartbeat_state = False

        self.init_global_link_backend()
        self.load_elf_symbols()  
        self.init_ui()
        
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

        self.load_history_from_db()

        # Watch the selected ELF itself (not its project directory).  Requiring
        # the same changed signature twice avoids parsing while the linker is
        # still replacing/writing the file.
        self.elf_last_signature = self._elf_file_signature()
        self.elf_pending_signature = None
        self.elf_pending_stable_ticks = 0
        self.elf_watch_timer = QTimer(self)
        self.elf_watch_timer.timeout.connect(self.check_elf_file_changed)
        self.elf_watch_timer.start(1000)

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

    def load_elf_symbols(self):  
        try:
            if self.config.elf_path and os.path.exists(self.config.elf_path):
                self.elf_db.load(self.config.elf_path)
                self.symbol_cache = dict(self.elf_db.symbols)
                print(f"ELF loaded: {len(self.symbol_cache)} symbols, {len(self.elf_db.types)} DWARF variables")
                return
            print(f"⚡ ELF 符号表加载成功，共包含 {len(self.symbol_cache)} 个有效全局变量。")
        except Exception as e:
            print(f"❌ ELF 符号表加载异常: {e}")

    def reload_elf_symbols(self, show_message=True, source="manual"):
        """Atomically reload ELF/DWARF and refresh saved trees and channel addresses."""
        elf_path = os.path.abspath(self.config.elf_path) if self.config.elf_path else ""
        if not os.path.isfile(elf_path):
            error = f"找不到 ELF：\n{elf_path or '(未设置)'}"
            if show_message: QMessageBox.warning(self, "重新提取 ELF 失败", error)
            return False, error

        try:
            refreshed = ElfDatabase()
            refreshed.load(elf_path)
        except Exception as exc:
            error = f"ELF 解析失败，已保留原来的变量数据：\n{exc}"
            if show_message: QMessageBox.critical(self, "重新提取 ELF 失败", error)
            return False, error

        self.stop_sampling_thread()

        self.elf_reload_in_progress = True
        try:
            self.elf_db = refreshed
            self.symbol_cache = dict(refreshed.symbols)

            # Symbols removed by the new firmware must not keep stale addresses.
            for path, _v_type, _offset, base_var, tab_idx in list(self.db.get_all_vars()):
                if not base_var.startswith(("0x", "0X")) and base_var not in self.symbol_cache:
                    self.db.delete_var(path, tab_idx)

            for tree in self.debug_trees.values():
                tree.clear()
            self.load_history_from_db()

            current_rows = {row[0]: row for row in self.db.get_all_vars()}
            for ch_idx, path in enumerate(list(self.ch_paths)):
                row = current_rows.get(path) if path else None
                if row is None:
                    if path:
                        for active in self.plot_active_paths:
                            if path in active: active.remove(path)
                        for plot_idx, curves in enumerate(self.plot_curves_dict):
                            curve = curves.pop(path, None)
                            if curve is not None:
                                try: self.plot_widgets[plot_idx].removeItem(curve)
                                except Exception: pass
                    self.ch_paths[ch_idx] = self.ch_types[ch_idx] = self.ch_addrs[ch_idx] = None
                else:
                    _name, v_type, offset, base_var, _tab_idx = row
                    base_address = int(base_var, 16) if base_var.startswith(("0x", "0X")) else self.symbol_cache.get(base_var)
                    if base_address is None:
                        self.ch_paths[ch_idx] = self.ch_types[ch_idx] = self.ch_addrs[ch_idx] = None
                    else:
                        self.ch_types[ch_idx] = v_type
                        self.ch_addrs[ch_idx] = hex(base_address + offset)
                self.ch_buffers[ch_idx] = []
                self.ch_time_stamps[ch_idx] = []

            if hasattr(self, 'adv_parse_dialog') and self.adv_parse_dialog:
                try: self.adv_parse_dialog.replace_elf_database(refreshed)
                except RuntimeError: self.adv_parse_dialog = None
            self.refresh_ui_channel_bar()
            self.reset_canvas_adapt_ranges()
            self.elf_last_signature = self._elf_file_signature()
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
        finally:
            self.elf_reload_in_progress = False

        self.restart_sampling_thread()
        summary = f"已重新提取 ELF：{len(refreshed.symbols)} 个符号，{len(refreshed.types)} 个 DWARF 变量。"
        self.lbl_status_text.setText("ELF 已自动重新提取" if source == "flash" else "ELF 已重新提取")
        if show_message: QMessageBox.information(self, "重新提取 ELF 完成", summary)
        return True, summary

    def _elf_file_signature(self):
        try:
            stat = os.stat(os.path.abspath(self.config.elf_path))
            return stat.st_mtime_ns, stat.st_size
        except (OSError, TypeError, ValueError):
            return None

    def check_elf_file_changed(self):
        if self.elf_reload_in_progress or self.flash_sequence_active:
            return
        signature = self._elf_file_signature()
        if signature is None or signature == self.elf_last_signature:
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            return
        if signature != self.elf_pending_signature:
            self.elf_pending_signature = signature
            self.elf_pending_stable_ticks = 1
            self.lbl_status_text.setText("检测到 ELF 更新，等待文件写入完成…")
            return
        self.elf_pending_stable_ticks += 1
        if self.elf_pending_stable_ticks < 2:
            return
        ok, message = self.reload_elf_symbols(show_message=False, source="watch")
        if ok:
            self.lbl_status_text.setText("ELF 变化已自动同步到调试窗口")
        else:
            # Keep the old database and retry only after a short, stable delay.
            self.elf_pending_stable_ticks = 0
            self.lbl_status_text.setText(f"ELF 自动提取失败，将重试：{message.splitlines()[0]}")

    def type_info_for_path(self, path):
        if path.startswith(("0x", "0X")): return None
        try: return self.elf_db.resolve(path)[1]
        except Exception: return None

    @staticmethod
    def decode_value(data, info):
        size, encoding, kind = len(data), int(info.get("encoding", 0) or 0), info.get("kind")
        if kind == "pointer": return f"0x{int.from_bytes(data, 'little'):0{size * 2}X}"
        if encoding == 0x04 and size in (4, 8): return struct.unpack("<f" if size == 4 else "<d", data)[0]
        number = int.from_bytes(data, "little", signed=encoding in (0x05, 0x06))
        if kind == "enum":
            enum_name = info.get("values", {}).get(number)
            return f"{enum_name} ({number})" if enum_name else number
        if encoding == 0x02: return bool(number)
        return number

    @staticmethod
    def encode_value(text, info):
        size, encoding = scalar_size(info), int(info.get("encoding", 0) or 0)
        if not size: raise ValueError("该节点不是可写标量")
        if info.get("kind") == "enum":
            reverse = {name: value for value, name in info.get("values", {}).items()}
            number = reverse[text] if text in reverse else int(text, 0)
            return int(number).to_bytes(size, "little", signed=number < 0)
        if encoding == 0x04 and size in (4, 8): return struct.pack("<f" if size == 4 else "<d", float(text))
        if encoding == 0x02 and text.lower() in ("true", "false"): number = 1 if text.lower() == "true" else 0
        else: number = int(text, 0)
        return number.to_bytes(size, "little", signed=encoding in (0x05, 0x06))

    def read_memory_bytes(self, address, size):
        if self.backend_suspended: raise RuntimeError("通信后端已断开")
        if self.config.backend_mode == "JLINK":
            if not self.jlink_native_instance: raise RuntimeError("链路断开")
            with self.jlink_lock:
                return bytes(self.jlink_native_instance.memory_read8(address, size))
        res = self.send_tcl_cmd(f"mdb {hex(address)} {size}")
        if "ERROR" in res.upper() or ":" not in res: raise RuntimeError("链路断开")
        values = []
        for line in res.splitlines():
            if ":" in line:
                values.extend(int(token, 16) & 0xFF for token in line.split(":", 1)[1].split())
        if len(values) < size: raise RuntimeError("OpenOCD 返回的内存字节不足")
        return bytes(values[:size])

    def write_memory_bytes(self, address, data):
        if self.backend_suspended: raise RuntimeError("通信后端已断开")
        if self.config.backend_mode == "JLINK":
            if not self.jlink_native_instance: raise RuntimeError("链路断开")
            with self.jlink_lock:
                self.jlink_native_instance.memory_write8(address, list(data))
            return
        for offset, value in enumerate(data): self.send_tcl_cmd(f"mwb {hex(address + offset)} {value}")

    def send_write_command(self, addr_hex, val_int, width_bytes, is_float=False):
        if self.backend_suspended:
            return
        try:
            addr = int(addr_hex, 16)
            if self.config.backend_mode == "OPENOCD":
                if not self.openocd_link_alive: return
                if is_float:
                    self.send_tcl_cmd(f"mww {addr_hex} {val_int}")
                elif width_bytes == 1:
                    self.send_tcl_cmd(f"mwb {addr_hex} {val_int & 0xFF}")
                elif width_bytes == 2:
                    self.send_tcl_cmd(f"mwh {addr_hex} {val_int & 0xFFFF}")
                else:
                    self.send_tcl_cmd(f"mww {addr_hex} {val_int & 0xFFFFFFFF}")
            else:
                if self.jlink_native_instance:
                    with self.jlink_lock:
                        if width_bytes == 1: self.jlink_native_instance.memory_write8(addr, [val_int & 0xFF])
                        elif width_bytes == 2: self.jlink_native_instance.memory_write16(addr, [val_int & 0xFFFF])
                        else: self.jlink_native_instance.memory_write32(addr, [val_int & 0xFFFFFFFF])
        except Exception:
            pass

    def send_tcl_cmd(self, cmd):
        if self.backend_suspended or not self.tcl_socket or not self.openocd_link_alive: return "ERROR"
        try:
            self.tcl_socket.sendall(f"{cmd}\x1a".encode('utf-8'))
            res = receive_tcl_response(self.tcl_socket)
            if "ERROR" in res: raise Exception()
            return res
        except Exception:
            self.openocd_link_alive = False
            if self.tcl_socket:
                try: self.tcl_socket.close()
                except OSError: pass
            self.tcl_socket = None
            return "ERROR"

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
        elif event.key() == Qt.Key.Key_Space:
            self.toggle_freeze()
        else:
            super().keyPressEvent(event)

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 6)
        layout.setSpacing(7)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        self.top_bar = top_bar
        top = QHBoxLayout(top_bar)
        top.setContentsMargins(10, 7, 10, 7)
        top.setSpacing(6)
        self.wave_hz_combo = QComboBox()
        self.wave_hz_combo.addItems([
            "自定义", "500 Hz", "1 kHz", "2 kHz", "4 kHz", "8 kHz"
        ])
        self.wave_hz_combo.setMinimumWidth(105)
        self.wave_hz_combo.currentIndexChanged.connect(self.restart_sampling_thread)
        
        self.custom_hz_edit = QLineEdit("30")
        self.custom_hz_edit.setFixedWidth(62)
        self.custom_hz_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_hz_edit.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
        self.custom_hz_edit.setValidator(QIntValidator(1, 8000, self))
        self.custom_hz_edit.editingFinished.connect(self.restart_sampling_thread)
        
        self.btn_freeze = QPushButton("锁定")
        self.btn_freeze.setProperty("plotTool", True)
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setToolTip("锁定/恢复波形时间轴滚动（空格键）")
        self.btn_freeze.clicked.connect(self.toggle_freeze)

        self.btn_canvas_adapt = QPushButton("适配")
        self.btn_canvas_adapt.setProperty("plotTool", True)
        self.btn_canvas_adapt.setToolTip("立即按当前时间窗内的数据适配纵轴范围")
        self.btn_canvas_adapt.clicked.connect(self.start_canvas_auto_adapt)
        
        self.btn_disconnect = QPushButton("断开")
        self.btn_disconnect.setToolTip("立即停止软件读写并在后台释放通信后端；拔掉实际线路卡住时界面不会等待")
        self.btn_disconnect.clicked.connect(self.disconnect_backend)

        self.btn_reconnect = QPushButton("重连")
        self.btn_reconnect.setProperty("commandButton", True)
        self.btn_reconnect.setToolTip("重新启动并连接当前配置的通信后端，不复位 MCU")
        self.btn_reconnect.clicked.connect(self.force_reconnect_engine)
        
        self.btn_mcu_reset = QPushButton("复位")
        self.btn_mcu_reset.setProperty("commandButton", True)
        self.btn_mcu_reset.setProperty("commandRole", "reset")
        self.btn_mcu_reset.clicked.connect(self.trigger_manual_mcu_reset)

        self.btn_flash = QPushButton("烧录")
        self.btn_flash.setProperty("commandButton", True)
        self.btn_flash.setToolTip("只有点击并确认后才会擦写 Flash/复位；普通调试不复位 MCU")
        self.btn_flash.clicked.connect(self.flash_elf_by_user)
        self.btn_flash.setEnabled(self.config.backend_mode == "JLINK")
        self.btn_disconnect.setProperty("commandButton", True)

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

        quick_caption = QLabel("变量")
        quick_caption.setObjectName("fieldCaption")
        top.addSpacing(10)
        top.addWidget(quick_caption)
        top.addWidget(self.input_name)
        top.addWidget(self.input_type)
        top.addWidget(btn_add)
        top.addWidget(btn_adv_c)
        top.addStretch()
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
        self.tab_widget.currentChanged.connect(lambda: self.restart_sampling_thread())

        self.create_new_debug_tab("主调试窗口")
        self.create_new_debug_tab("调试窗口 2")
        self.create_new_debug_tab("调试窗口 3")
        self.tab_widget.setCurrentIndex(0)
        self.tab_widget.setToolTip("双击标签或将标签拖出可悬浮；关闭悬浮窗口后自动回到原位置。")
        self.main_splitter.addWidget(self.tab_widget)
        
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_splitter.setStyleSheet(f"QSplitter::handle {{ height: 4px; background-color: {self.theme_colors['splitter']}; }}")
        
        self.plot_widgets = []
        self.ch_colors = self.theme_colors['channels']
        self.v_lines = []
        self.h_lines = []
        self.hover_dots = []
        self.hover_labels = []

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
            pw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            pw.customContextMenuRequested.connect(lambda pos, idx=i: self.show_plot_context_menu(pos, idx))

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
            pw.scene().sigMouseMoved.connect(lambda pos, idx=i: self.handle_plot_mouse_move(pos, idx))

            self.plot_widgets.append(pw)
            self.plot_splitter.addWidget(pw)
            
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
        self.lbl_ch1.setStyleSheet(f"{ch_style} color: {self.ch_colors[0]};")
        self.lbl_ch2.setStyleSheet(f"{ch_style} color: {self.ch_colors[1]};")
        self.lbl_ch3.setStyleSheet(f"{ch_style} color: {self.ch_colors[2]};")
        
        bottom_bar.addWidget(self.lbl_ch1)
        bottom_bar.addWidget(self.lbl_ch2)
        bottom_bar.addWidget(self.lbl_ch3)
        bottom_bar.addStretch()
        
        self.lbl_led = QLabel()
        self.lbl_led.setFixedSize(10, 10)
        self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
        self.lbl_status_text = QLabel("正在连接...")
        self.lbl_status_text.setStyleSheet("color: #8b949e; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")
        
        bottom_bar.addWidget(self.lbl_led)
        bottom_bar.addWidget(self.lbl_status_text)
        
        bottom_wrapper = QWidget()
        bottom_wrapper.setFixedHeight(28) 
        bottom_wrapper.setLayout(bottom_bar)
        layout.addWidget(bottom_wrapper)
        
        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

    def create_new_debug_tab(self, name=None):
        if len(self.debug_trees) >= 3:
            QMessageBox.warning(self, "标签页上限", "最多只允许开启 3 个变量调试窗口。")
            return
        slot = next(index for index in range(3) if index not in self.debug_trees)
        if not name: name = f"调试窗口 {slot + 1}"
            
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["全局变量", "数据类型", "实时值（5 Hz）"])
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.show_context_menu)
        tree.itemDoubleClicked.connect(self.handle_tree_item_double_clicked)

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

    def toggle_freeze(self):
        self.is_frozen = not self.is_frozen
        self.btn_freeze.setChecked(self.is_frozen)
        if self.is_frozen:
            self.btn_freeze.setText("恢复")
        else:
            self.btn_freeze.setText("锁定")
            for i in range(3):
                if self.v_lines[i]: self.v_lines[i].setVisible(False)
                if self.h_lines[i]: self.h_lines[i].setVisible(False)
                if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                if self.hover_labels[i]: self.hover_labels[i].setVisible(False)

    def start_canvas_auto_adapt(self):
        if not any(self.ch_paths):
            QMessageBox.information(self, "一键自适应画布", "请先把至少一个变量绑定到波形通道。")
            return
        self.finish_canvas_auto_adapt()

    def finish_canvas_auto_adapt(self):
        adapted = 0
        with QMutexLocker(self.buffer_mutex):
            for plot_idx in range(3):
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
                y_span = y_max - y_min
                y_padding = max(y_span * 0.08, abs(y_min) * 0.02, 0.01)
                if not np.isfinite(y_span) or y_span <= 0:
                    y_padding = max(abs(y_min) * 0.1, 1.0)
                y_lower, y_upper = y_min - y_padding, y_max + y_padding
                self.canvas_fixed_ranges[plot_idx] = (x_lower, x_upper, y_lower, y_upper)
                self.plot_widgets[plot_idx].setXRange(x_lower, x_upper, padding=0)
                self.plot_widgets[plot_idx].setYRange(y_lower, y_upper, padding=0)
                adapted += 1
        if adapted:
            self.lbl_status_text.setText(f"已适配 {adapted} 个波形视图")
        else:
            self.lbl_status_text.setText("暂无有效波形数据，稍后再试")

    def reset_canvas_adapt_ranges(self):
        self.canvas_fixed_ranges = [None, None, None]
        for plot_widget in getattr(self, 'plot_widgets', []):
            try: plot_widget.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
            except Exception: pass

    def trigger_manual_mcu_reset(self):
        answer = QMessageBox.question(
            self, "确认复位 MCU",
            "这是唯一的手动复位操作。\n\n确定要立即复位并运行 MCU 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return
        reset_text = "#ff8a80" if self.is_dark_theme else "#c62828"
        self._execute_flash_animation(self.btn_mcu_reset, "#e53935", f"color: {reset_text};")
        self.stop_sampling_thread()
        reset_error = ""
        if self.config.backend_mode == "OPENOCD":
            if self.openocd_link_alive and self.tcl_socket:
                if self.send_tcl_cmd("reset run") == "ERROR":
                    reset_error = "OpenOCD 复位命令执行失败"
            else:
                reset_error = "OpenOCD 链路未连接"
        else:
            if self.jlink_native_instance:
                try:
                    with self.jlink_lock:
                        self.jlink_native_instance.reset()
                except Exception as exc:
                    reset_error = str(exc)
            else:
                reset_error = "J-Link 链路未连接"
        if reset_error:
            QMessageBox.warning(self, "复位失败", reset_error)
        self.force_reconnect_engine()

    def flash_elf_by_user(self):
        if self.config.backend_mode != "JLINK":
            QMessageBox.warning(self, "无法烧录", "当前不是 J-Link 模式。")
            return
        elf_path = os.path.abspath(self.config.elf_path)
        jlink_dir = os.path.dirname(self.config.jlink_dll_dir) if os.path.isfile(self.config.jlink_dll_dir) else self.config.jlink_dll_dir
        commander = os.path.join(jlink_dir, "JLink.exe")
        device = self.config.jlink_device
        _loader_config, loader_error = self.find_flash_loader_config(jlink_dir, device)
        if not os.path.isfile(elf_path):
            QMessageBox.warning(self, "无法烧录", f"找不到 ELF：\n{elf_path}")
            return
        if not os.path.isfile(commander):
            QMessageBox.warning(self, "无法烧录", f"找不到与 J-Flash 同目录的 JLink.exe：\n{commander}")
            return
        if loader_error:
            QMessageBox.warning(self, "无法烧录", loader_error)
            return
        answer = QMessageBox.warning(
            self, "确认烧录 ELF",
            f"芯片：{device}\nELF：{elf_path}\n\n"
            "烧录会擦写 Flash，并在完成后复位运行 MCU。\n"
            "普通连接、采样和重连不会执行此操作。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes: return

        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("⏳ 正在烧录…")
        self.lbl_status_text.setText("正在烧录 ELF（界面仍可响应）")
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.stop_sampling_thread()
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.pending_flash_args = (
            commander, jlink_dir, elf_path, device, self.config.jlink_connect_device
        )
        if old_link:
            self._close_native_link_async(old_link, next_action="flash")
            return
        self._start_pending_flash()

    def _start_pending_flash(self):
        args = getattr(self, 'pending_flash_args', None)
        self.pending_flash_args = None
        if not args:
            return
        self.flash_worker = ElfFlashWorker(
            *args, self
        )
        self.flash_worker.finished_signal.connect(self.handle_flash_finished)
        self.flash_worker.start()

    @staticmethod
    def find_flash_loader_config(jlink_dir, device_name):
        """Validate the selected device's external Flash loader, when declared."""
        xml_path = os.path.join(jlink_dir, "JLinkDevices.xml")
        if not os.path.isfile(xml_path): return None, ""
        try:
            root = ET.parse(xml_path).getroot()
        except Exception as exc:
            return None, f"无法解析器件库：\n{xml_path}\n\n{exc}"
        chip_attrs = None
        flash_attrs = None
        for device in root.iter("Device"):
            chip = device.find("ChipInfo")
            if chip is None or chip.get("Name", "").casefold() != device_name.casefold(): continue
            flash = device.find("FlashBankInfo")
            chip_attrs = dict(chip.attrib)
            flash_attrs = dict(flash.attrib) if flash is not None else None
            break
        if not chip_attrs or not flash_attrs or not flash_attrs.get("Loader"):
            # DLL-only devices manage their algorithms internally.
            return None, ""

        loader = flash_attrs["Loader"].strip()
        normalized = loader.replace("/", os.sep).replace("\\", os.sep)
        direct = os.path.normpath(os.path.join(jlink_dir, normalized))
        loader_path = direct if os.path.isfile(direct) else ""

        basename = os.path.basename(normalized)
        matches = []
        if not loader_path:
            devices_root = os.path.join(jlink_dir, "Devices")
            if os.path.isdir(devices_root):
                for root_dir, _dirs, files in os.walk(devices_root):
                    for filename in files:
                        if filename.casefold() == basename.casefold(): matches.append(os.path.join(root_dir, filename))
        if len(matches) == 1: loader_path = matches[0]
        elif len(matches) > 1:
            # Prefer a directory whose path contains the selected device family.
            family = re.match(r"[A-Za-z]+\d+", device_name)
            if family:
                preferred = [path for path in matches if family.group(0).casefold() in path.casefold()]
                if len(preferred) == 1: loader_path = preferred[0]
            if not loader_path:
                return None, f"Flash 算法 {basename} 存在多个同名文件，无法唯一确定：\n" + "\n".join(matches)
        if not loader_path:
            return None, f"芯片 {device_name} 需要 Flash 算法：\n{loader}\n\n但在当前 J-Link 目录下未找到：\n{jlink_dir}"
        config = {
            "vendor": chip_attrs.get("Vendor", "Custom"),
            "ram_addr": int(chip_attrs.get("WorkRAMAddr", "0"), 0),
            "ram_size": int(chip_attrs.get("WorkRAMSize", "0"), 0),
            "core": chip_attrs.get("Core", "JLINK_CORE_CORTEX_M0"),
            "flash_name": flash_attrs.get("Name", "Internal Flash"),
            "flash_addr": int(flash_attrs.get("BaseAddr", "0"), 0),
            "flash_size": int(flash_attrs.get("MaxSize", "0"), 0),
            "loader_path": os.path.abspath(loader_path),
            "loader_type": flash_attrs.get("LoaderType", "FLASH_ALGO_TYPE_OPEN"),
        }
        return config, ""

    def handle_flash_finished(self, success, message, link):
        self.jlink_native_instance = link
        self.flash_sequence_active = False
        self.backend_suspended = False
        self.btn_flash.setText("烧录")
        self.btn_flash.setEnabled(self.config.backend_mode == "JLINK")
        self.lbl_status_text.setText("烧录完成" if success else "烧录失败")
        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            QMessageBox.information(self, "烧录完成", message)
        else:
            self.restart_sampling_thread()
            QMessageBox.critical(self, "烧录失败", message)

    def force_reconnect_engine(self):
        self._execute_flash_animation(self.btn_reconnect, "#00b0ff", "")
        self.backend_suspended = True
        self.stop_sampling_thread()
        if self.config.backend_mode == "OPENOCD":
            if self.tcl_socket: 
                try: self.tcl_socket.close()
                except: pass
            self.tcl_socket = None
            self.openocd_link_alive = False
            try:
                stop_managed_openocd(self.config)
                launch_openocd(self.config)
                self.backend_suspended = False
                self.init_global_link_backend()
            except Exception as exc:
                self.backend_suspended = True
                self.lbl_status_text.setText("OpenOCD 重连失败")
                QMessageBox.critical(self, "OpenOCD 重连失败", str(exc))
            self._finish_backend_reconnect()
            return

        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        if old_link:
            self._close_native_link_async(old_link, next_action="reconnect")
            self.lbl_status_text.setText("正在后台释放旧 J-Link 后端…")
        else:
            self.backend_suspended = False
            self.init_global_link_backend()
            self._finish_backend_reconnect()

    def disconnect_backend(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录过程中不能断开通信后端。")
            return
        self.backend_suspended = True
        self.stop_sampling_thread()
        if self.tcl_socket:
            try: self.tcl_socket.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: self.tcl_socket.close()
            except Exception: pass
        self.tcl_socket = None
        self.openocd_link_alive = False
        if self.config.backend_mode == "OPENOCD":
            try: stop_managed_openocd(self.config)
            except Exception: pass
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        if old_link:
            self._close_native_link_async(old_link)
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端已断开（未复位 MCU）")
        self.lbl_status_text.setStyleSheet("color: #f0a020; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")

    def _close_native_link_async(self, link, next_action=""):
        closer = BackendCloseWorker(link, self.jlink_lock, self)
        self.backend_close_workers.append(closer)
        closer.finished_signal.connect(
            lambda error, worker=closer, action=next_action:
            self._native_link_closed(worker, error, action)
        )
        closer.start()

    def _native_link_closed(self, worker, error, next_action):
        if worker in self.backend_close_workers:
            self.backend_close_workers.remove(worker)
        worker.deleteLater()
        if next_action == "reconnect":
            self.backend_suspended = False
            self.init_global_link_backend()
            self._finish_backend_reconnect()
        elif next_action == "flash":
            self._start_pending_flash()
        elif error:
            self.lbl_status_text.setText(f"后端已停止读写；驱动释放异常：{error}")

    def _finish_backend_reconnect(self):
        connected = (self.openocd_link_alive if self.config.backend_mode == "OPENOCD"
                     else bool(self.jlink_native_instance))
        self.btn_disconnect.setEnabled(connected)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端重连完成" if connected else "通信后端重连失败")
        self.restart_sampling_thread()

    def _execute_flash_animation(self, button, target_color, base_style=""):
        original_style = button.styleSheet()
        anim = QVariantAnimation(button)
        anim.setDuration(220)
        start_color = QColor(target_color).lighter(125)
        end_color = QColor(target_color)
        anim.setStartValue(start_color)
        anim.setEndValue(end_color)
        anim.valueChanged.connect(
            lambda val: button.setStyleSheet(
                f"background-color: {val.name()}; color: #ffffff; border: 1px solid {target_color};"
            )
        )
        anim.finished.connect(lambda: button.setStyleSheet(original_style))
        button._feedback_animation = anim
        anim.start()

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
        QTimer.singleShot(0, self._reset_layout_switching_flag)

    def _reset_layout_switching_flag(self):
        self.layout_switching = False
        self.update_plot_canvas()

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

    def load_history_from_db(self):
        # Refresh saved variable layouts from this ELF so stale source-derived
        # offsets in an existing history database cannot be reused.
        old_rows = self.db.get_all_vars()
        roots = {row[3] for row in old_rows if row[3] in self.elf_db.types}
        for root in roots:
            for tab_idx in {row[4] for row in old_rows if row[3] == root}:
                self.db.replace_root_vars(root, tab_idx, self.elf_db.flatten(root))
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

    def handle_high_speed_data(self, batch_data):
        locker = QMutexLocker(self.buffer_mutex)
        for timestamp, data_packet in batch_data:
            current_rel_time = (timestamp - self.start_time) * 1000.0
            for ch_idx, val in data_packet.items():
                self.ch_buffers[ch_idx].append(val)
                self.ch_time_stamps[ch_idx].append(current_rel_time)
        for i in range(3):
            if len(self.ch_buffers[i]) > 40000:
                self.ch_buffers[i] = self.ch_buffers[i][-25000:]
                self.ch_time_stamps[i] = self.ch_time_stamps[i][-25000:]

    def update_plot_canvas(self):
        if self.is_frozen or self.layout_switching: return
        layout_id = self.current_layout_id
        locker = QMutexLocker(self.buffer_mutex)
        for i in range(3):
            active_paths = self.plot_active_paths[i]
            curves_map = self.plot_curves_dict[i]
            ch_idx = -1
            for path in active_paths:
                for c_i in range(3):
                    if self.ch_paths[c_i] == path:
                        ch_idx = c_i; break
                if ch_idx != -1: break

            pw = self.plot_widgets[i]
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
                        pw.removeItem(curves_map[pk])
                        del curves_map[pk]
                
                for path in active_paths:
                    target_ch = -1
                    for c_i in range(3):
                        if self.ch_paths[c_i] == path: target_ch = c_i; break
                    if target_ch != -1 and self.ch_buffers[target_ch]:
                        t_data = self.ch_time_stamps[target_ch]
                        v_data = self.ch_buffers[target_ch]
                        start_idx = bisect.bisect_left(t_data, min_x)
                        if len(t_data) > start_idx:
                            x_arr = np.array(t_data[start_idx:], dtype=np.float64)
                            y_arr = np.array(v_data[start_idx:], dtype=np.float64)
                            if path not in curves_map:
                                smooth_pen = QPen(QColor(self.ch_colors[target_ch]), 2.0)
                                smooth_pen.setCosmetic(True)
                                smooth_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                                smooth_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                                curves_map[path] = pw.plot(pen=smooth_pen, antialias=True)
                            curves_map[path].setData(x_arr, y_arr)
            except: pass 

    def handle_plot_mouse_move(self, pos, plot_idx):
        if not self.is_frozen or self.layout_switching: return
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20: return

        active_paths = self.plot_active_paths[plot_idx]
        target_ch = -1
        for path in active_paths:
            for c_i in range(3):
                if self.ch_paths[c_i] == path: target_ch = c_i; break
            if target_ch != -1: break

        locker = QMutexLocker(self.buffer_mutex)
        if target_ch == -1 or not self.ch_buffers[target_ch]: return
        t_data = self.ch_time_stamps[target_ch]
        v_data = self.ch_buffers[target_ch]

        try:
            if not pw.getPlotItem().scene(): return
            mouse_point = pw.getViewBox().mapSceneToView(pos)
            x_mouse = mouse_point.x()
            idx = bisect.bisect_left(t_data, x_mouse)
            if idx == 0: closest_idx = 0
            elif idx >= len(t_data): closest_idx = len(t_data) - 1
            else: closest_idx = idx if (t_data[idx] - x_mouse) < (x_mouse - t_data[idx-1]) else (idx - 1)

            if closest_idx >= len(t_data): return
            exact_x = t_data[closest_idx]
            exact_y = v_data[closest_idx]

            if self.v_lines[plot_idx]: self.v_lines[plot_idx].setPos(exact_x)
            if self.h_lines[plot_idx]: self.h_lines[plot_idx].setPos(exact_y)
            if self.v_lines[plot_idx]: self.v_lines[plot_idx].setVisible(True)
            if self.h_lines[plot_idx]: self.h_lines[plot_idx].setVisible(True)

            if self.hover_dots[plot_idx]:
                self.hover_dots[plot_idx].setData([exact_x], [exact_y], brush=pg.mkBrush(self.ch_colors[target_ch]))
                self.hover_dots[plot_idx].setVisible(True)

            if self.hover_labels[plot_idx]:
                self.hover_labels[plot_idx].setText(f" t: {exact_x:.1f} ms\n v: {exact_y:.4f} ")
                self.hover_labels[plot_idx].setPos(exact_x, exact_y)
                self.hover_labels[plot_idx].setVisible(True)
        except: pass

    def show_plot_context_menu(self, pos, plot_idx):
        if self.layout_switching: return
        available_paths = [p for p in self.ch_paths if p is not None]
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible(): return
        try:
            menu = pw.getPlotItem().getContextMenus(None)
            menu.setStyleSheet(self.popup_menu_style)
            y_val_menu = None
            for act in menu.actions():
                if act.menu() and "Y_Val" in act.menu().title():
                    y_val_menu = act.menu(); y_val_menu.clear(); break
            if not y_val_menu:
                y_val_menu = QMenu("📈 Y_Val 数据源定义", menu)
                menu.insertMenu(menu.actions()[0] if menu.actions() else None, y_val_menu)
            y_val_menu.setStyleSheet(self.popup_menu_style)
            
            if not available_paths:
                action = QAction("⚠️ 暂无高速通道绑定的变量", y_val_menu)
                action.setEnabled(False)
                y_val_menu.addAction(action)
            else:
                for path in available_paths:
                    is_selected = path in self.plot_active_paths[plot_idx]
                    action = QAction(f"{'●   ' if is_selected else '○   '}{path}", y_val_menu)
                    action.triggered.connect(lambda checked, p=path, p_idx=plot_idx, current_state=is_selected: 
                        self.commit_vofa_layer_selection(p, p_idx, current_state))
                    y_val_menu.addAction(action)
            menu.exec(pw.mapToGlobal(pos))
        except: pass

    def commit_vofa_layer_selection(self, path, plot_idx, current_state):
        if not current_state:
            if path not in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].append(path)
        else:
            if path in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].remove(path)
        self.restart_sampling_thread()

    def show_context_menu(self, pos):
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.get_current_tree()
        if not tree: return
        item = tree.itemAt(pos)
        if not item: return
            
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path: path = item.text(0)
        
        menu = QMenu()
        menu.setStyleSheet(self.popup_menu_style)
        bound_ch_idx = -1
        for i in range(3):
            if self.ch_paths[i] == path: bound_ch_idx = i; break

        is_container = "{结构体/数组}" in item.text(2)
        if not is_container:
            if bound_ch_idx != -1:
                menu.addAction(f"❌ 取消该变量在 通道 {bound_ch_idx+1} 的波形监视")
            else:
                m1 = menu.addAction("🟢 绑定至 示波器通道 1")
                m2 = menu.addAction("🟡 绑定至 示波器通道 2")
                m3 = menu.addAction("🔵 绑定至 示波器通道 3")
            menu.addSeparator()
            
        m_del = menu.addAction("🗑️ 从监视列表中删除该整组变量及其全部子成员 (Delete)")
        act = menu.exec(tree.viewport().mapToGlobal(pos))
        
        if not is_container and bound_ch_idx != -1 and act and "取消" in act.text(): 
            self.unbind_channel(bound_ch_idx)
        elif not is_container and 'm1' in locals() and act == m1: self.bind_tree_item_to_channel(0, item)
        elif not is_container and 'm2' in locals() and act == m2: self.bind_tree_item_to_channel(1, item)
        elif not is_container and 'm3' in locals() and act == m3: self.bind_tree_item_to_channel(2, item)
        elif act == m_del:
            root_item = item
            while root_item.parent(): root_item = root_item.parent()
            self.remove_variable_root(root_item, tree)

    def bind_tree_item_to_channel(self, ch_idx, item):
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        v_type = item.text(1)
        all_db_vars = {x[0]: (x[2], x[3]) for x in self.db.get_all_vars()}
        if path not in all_db_vars: return
        offset, base_var = all_db_vars[path]
        
        if base_var.startswith("0x") or base_var.startswith("0X"):
            absolute_address = int(base_var, 16) + offset
        else:
            base_addr = self.symbol_cache.get(base_var)
            if base_addr is None: return
            absolute_address = base_addr + offset
        
        for i in range(3):
            if self.ch_paths[i] == path: self.unbind_channel(i)
        
        locker = QMutexLocker(self.buffer_mutex)
        self.ch_paths[ch_idx] = path
        self.ch_types[ch_idx] = v_type
        self.ch_addrs[ch_idx] = hex(absolute_address)
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        if path not in self.plot_active_paths[ch_idx]: self.plot_active_paths[ch_idx].append(path)
        del locker

        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        self.restart_sampling_thread()

    def unbind_channel(self, ch_idx, restart=True):
        locker = QMutexLocker(self.buffer_mutex)
        path = self.ch_paths[ch_idx]
        if path:
            for p_idx, p_active in enumerate(self.plot_active_paths):
                if path in p_active: p_active.remove(path)
                if path in self.plot_curves_dict[p_idx]:
                    try:
                        self.plot_widgets[p_idx].removeItem(self.plot_curves_dict[p_idx][path])
                        del self.plot_curves_dict[p_idx][path]
                    except: pass
        self.ch_paths[ch_idx] = None
        self.ch_types[ch_idx] = None
        self.ch_addrs[ch_idx] = None
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        del locker
        self.refresh_ui_channel_bar()
        self.reset_canvas_adapt_ranges()
        if restart:
            self.restart_sampling_thread()

    def refresh_ui_channel_bar(self):
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        for i in range(3):
            labels[i].setText(f"CH{i+1}  {self.ch_paths[i]}" if self.ch_paths[i] else f"CH{i+1}  空闲")

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
        val_text = item.text(2)
        v_type = item.text(1)
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        
        if "{结构体/数组}" in val_text or "断开" in val_text or "异常" in val_text: return
        type_info = self.type_info_for_path(path)
        if type_info is not None and not scalar_size(type_info): return

        all_db_paths = [x[0] for x in self.db.get_all_vars()]
        has_children = any(p.startswith(path + ".") or p.startswith(path + "[") for p in all_db_paths if p != path)
        if has_children: return
        
        tree = self.sender()
        if not isinstance(tree, QTreeWidget):
            tree = self.get_current_tree()
        if tree is None: return
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
        
        if base_var.startswith("0x") or base_var.startswith("0X"):
            addr_hex = hex(int(base_var, 16) + offset)
        else:
            base_addr = self.symbol_cache.get(base_var)
            if base_addr is None: return
            addr_hex = hex(base_addr + offset)
            
        try:
            type_info = self.type_info_for_path(path)
            if type_info is not None:
                data = self.encode_value(new_text, type_info)
                self.write_memory_bytes(int(addr_hex, 16), data)
                item.setText(2, new_text)
                return
            norm_type = v_type.replace("_t", "").strip()
            width = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
            if "float" in norm_type:
                raw_int = struct.unpack('<I', struct.pack('<f', float(new_text)))[0]
                self.send_write_command(addr_hex, raw_int, 4, is_float=True)
            else:
                self.send_write_command(addr_hex, int(float(new_text)), width)
            item.setText(2, new_text)
        except: pass

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
        for channel_idx, path in enumerate(list(self.ch_paths)):
            if path in removed_paths and path not in remaining_paths:
                self.unbind_channel(channel_idx, restart=False)

        if tree:
            index = tree.indexOfTopLevelItem(root_item)
            if index >= 0: tree.takeTopLevelItem(index)
        self.restart_sampling_thread()
        
    def restart_sampling_thread(self):
        if getattr(self, 'elf_reload_in_progress', False): return
        self.stop_sampling_thread()
        if self.backend_suspended:
            return
            
        hz_idx = self.wave_hz_combo.currentIndex()
        if hz_idx == 0:
            try: hz = int(self.custom_hz_edit.text().strip())
            except: hz = 300
        else: hz = [300, 500, 1000, 2000, 4000, 8000][hz_idx]
            
        self.custom_hz_edit.setEnabled(hz_idx == 0)
        delay = 1.0 / hz
        active_channels = {}
        for i in range(3):
            if self.ch_addrs[i]: active_channels[i] = (self.ch_addrs[i], self.ch_types[i], self.ch_paths[i])
        if active_channels:
            self.worker = HighSpeedSamplingWorker(
                self.config,
                active_channels,
                delay,
                self.jlink_native_instance,
                self.elf_db,
                self.jlink_lock,
            )
            self.worker.data_received.connect(self.handle_high_speed_data)
            self.worker.start()

    def stop_sampling_thread(self, wait_ms=250):
        worker = getattr(self, 'worker', None)
        self.worker = None
        if not worker:
            return True
        stopped = worker.stop(wait_ms)
        if not stopped:
            # Keep ownership until the blocked driver call returns.  This is
            # crucial: destroying a still-running QThread can crash Qt.
            self.retired_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._forget_retired_worker(w))
        else:
            worker.deleteLater()
        return stopped

    def _forget_retired_worker(self, worker):
        if worker in self.retired_workers:
            self.retired_workers.remove(worker)
        worker.deleteLater()

    def low_speed_tree_refresh(self):
        if self.layout_switching or self.backend_suspended: return
        current_tab = self.current_debug_slot()
        active_tabs = {current_tab} if current_tab is not None else set()
        active_tabs.update(
            slot for slot, window in self.detached_debug_windows.items()
            if window.isVisible()
        )
        db_vars_by_tab = {slot: self.db.get_var_map(slot) for slot in active_tabs}
        read_cache = {}
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        locker = QMutexLocker(self.buffer_mutex)
        
        for i in range(3):
            if self.ch_paths[i]:
                labels[i].setText(f"CH{i+1}: {self.ch_buffers[i][-1]:.2f}" if (self.ch_buffers[i] and (self.config.backend_mode == "JLINK" or self.openocd_link_alive)) else f"CH{i+1}: 断开")
            else: labels[i].setText(f"CH{i+1}  空闲")
        del locker

        def refresh_item(item, t_idx):
            tree = self.debug_trees.get(t_idx)
            if not tree or tree.itemWidget(item, 2) is not None: return

            path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
            v_type = item.text(1)
            if "{结构体/数组}" in item.text(2):
                if item.isExpanded():
                    for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                return

            # Values outside the viewport do not need a hardware transaction.
            # They are refreshed as soon as the user scrolls them into view.
            rect = tree.visualItemRect(item)
            if not rect.isValid() or rect.bottom() < 0 or rect.top() > tree.viewport().height():
                return
                
            for i in range(3):
                if self.ch_paths[i] == path:
                    if self.config.backend_mode == "OPENOCD" and not self.openocd_link_alive: item.setText(2, "断开")
                    else: item.setText(2, f"{self.ch_buffers[i][-1]:.4f}" if self.ch_buffers[i] else "载入中")
                    return
            
            tab_vars = db_vars_by_tab.get(t_idx, {})
            if path not in tab_vars: return
            offset, base_var, _v_type = tab_vars[path]
            if base_var.startswith("0x") or base_var.startswith("0X"): absolute_address = int(base_var, 16) + offset
            else:
                base_addr = self.symbol_cache.get(base_var)
                if base_addr is None: return
                absolute_address = base_addr + offset

            cache_key = (path, absolute_address, v_type.strip())
            if cache_key in read_cache:
                item.setText(2, read_cache[cache_key])
                for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                return

            try:
                type_info = self.type_info_for_path(path)
                if type_info is not None and scalar_size(type_info):
                    value = self.decode_value(self.read_memory_bytes(absolute_address, scalar_size(type_info)), type_info)
                    display_value = f"{value:.4f}" if isinstance(value, float) else str(value)
                    read_cache[cache_key] = display_value
                    item.setText(2, display_value)
                    for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                    return
                norm_type = v_type.strip()
                is_custom_enum = False
                width_bytes = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
                if self.config.backend_mode == "OPENOCD":
                    if not self.openocd_link_alive: raise Exception()
                    cmd = "mdb" if width_bytes == 1 else ("mdh" if width_bytes == 2 else "mdw")
                    res = self.send_tcl_cmd(f"{cmd} {hex(absolute_address)}")
                    if "ERROR" in res or ":" not in res: raise Exception()
                    raw_int = int(res.split(":")[-1].strip(), 16)
                else:
                    if not self.jlink_native_instance: raise Exception()
                    with self.jlink_lock:
                        if width_bytes == 1: raw_int = self.jlink_native_instance.memory_read8(absolute_address, 1)[0]
                        elif width_bytes == 2: raw_int = self.jlink_native_instance.memory_read16(absolute_address, 1)[0]
                        else: raw_int = self.jlink_native_instance.memory_read32(absolute_address, 1)[0]
                
                if "uint8" in norm_type: v = raw_int & 0xFF
                elif "int8" in norm_type or "char" in norm_type: v = struct.unpack('<b', struct.pack('<B', raw_int & 0xFF))[0]
                elif "uint16" in norm_type: v = raw_int & 0xFFFF
                elif "int16" in norm_type: v = struct.unpack('<h', struct.pack('<H', raw_int & 0xFFFF))[0]
                elif "uint32" in norm_type: v = raw_int & 0xFFFFFFFF
                elif "float" in norm_type: v = struct.unpack('<f', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                elif is_custom_enum: v = raw_int & 0xFF
                else: v = struct.unpack('<i', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                display_value = f"{v:.4f}" if "float" in norm_type else str(v)
                read_cache[cache_key] = display_value
                item.setText(2, display_value)
            except:
                read_cache[cache_key] = "断开"
                item.setText(2, "断开")
            for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)

        for tab_index in sorted(active_tabs):
            tree = self.debug_trees.get(tab_index)
            if tree and tree.isVisible():
                for i in range(tree.topLevelItemCount()): refresh_item(tree.topLevelItem(i), tab_index)

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

    def update_hardware_heartbeat(self):
        is_alive = False
        if self.backend_suspended:
            is_alive = False
        elif self.config.backend_mode == "OPENOCD":
            process = getattr(self.config, "openocd_process", None)
            process_alive = process is not None and process.poll() is None
            worker_alive = hasattr(self, 'worker') and self.worker and self.worker.isRunning() and self.worker.is_link_alive
            has_worker = hasattr(self, 'worker') and self.worker and self.worker.isRunning()
            is_alive = process_alive and self.openocd_link_alive and (not has_worker or worker_alive)
            if not process_alive:
                self.openocd_link_alive = False
        else:
            # Do not call into the native DLL from the GUI heartbeat.  Some
            # probe/USB disconnects make target_connected() wait inside the
            # driver and would freeze every widget, including Disconnect.
            worker_alive = bool(self.worker and self.worker.isRunning() and self.worker.is_link_alive)
            is_alive = bool(self.jlink_native_instance) and (not self.worker or worker_alive)

        if is_alive:
            self.heartbeat_state = not self.heartbeat_state
            self.lbl_led.setStyleSheet(f"background-color: {'#00E676' if self.heartbeat_state else '#00b252'}; border-radius: 5px;")
            self.lbl_status_text.setText(f"调试在线 ({self.config.backend_mode})")
            self.lbl_status_text.setStyleSheet(f"color: {self.theme_colors['status_ok']}; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")
        else:
            self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
            self.lbl_status_text.setText("通信后端已断开" if self.backend_suspended else "链路断开")
            self.lbl_status_text.setStyleSheet("color: #ef5350; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")

    def closeEvent(self, event):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "Flash 烧录尚未完成，为避免中断导致芯片无法启动，请等待烧录结束。")
            event.ignore()
            return
        if hasattr(self, 'elf_watch_timer'): self.elf_watch_timer.stop()
        for window in list(getattr(self, "detached_debug_windows", {}).values()):
            window._reattaching = True
            window.close()
        if hasattr(self, "detached_debug_windows"):
            self.detached_debug_windows.clear()
        self.backend_suspended = True
        self.stop_sampling_thread()
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
        event.accept()


def run_application() -> int:
    """Create the Qt application and run the login/main-window flow."""
    app = QApplication(sys.argv)
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon_path = os.path.join(bundle_root, "assets", "mcu_studio_anime.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    pg.setConfigOption("foreground", "#e0e0e0")
    app.setStyleSheet(DARK_STYLE)
    db = LiveWatchDB()
    dialog = LoginConfigDialog(db)
    if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.is_connected:
        return 0

    window = MCUStudioWindow(dialog.config, db)
    window.show()
    return app.exec()


