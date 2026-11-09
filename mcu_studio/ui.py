# MCU Studio user interface
import sys
import csv
import json
import socket
import re
import time
import struct
import os
import xml.etree.ElementTree as ET
import bisect
import math
import threading
import html
import numpy as np 

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QPushButton, QComboBox as _QComboBox, QLabel, QLineEdit,
                             QHeaderView, QMenu, QMessageBox as _QMessageBox, QDialog, QFileDialog,
                             QFormLayout, QCheckBox as _QCheckBox, QSplitter, QTextEdit,
                             QRadioButton, QButtonGroup, QTabWidget, QFrame, QTabBar,
                             QStyle, QStyleOptionButton, QProgressBar, QSlider, QScrollArea,
                             QSpinBox, QInputDialog)
from PyQt6.QtCore import (QTimer, Qt, QEvent, pyqtSignal, QVariantAnimation, QMutex,
                          QMutexLocker, QPointF, QRectF, QSize, QThread, QEventLoop)
from PyQt6.QtGui import (QFont, QKeyEvent, QIntValidator, QDoubleValidator, QColor,
                         QAction, QPen, QIcon, QPainter, QPalette, QPainterPath,
                         QPixmap, QRegion)

import pyqtgraph as pg

from . import __version__

from .core import (
    ElfDatabase,
    LiveWatchDB,
    ProjectConfig,
    connect_openocd_tcl,
    default_openocd_executable,
    discover_jlink_directories,
    discover_openocd_executables,
    discover_openocd_configs,
    jlink_dll_path,
    launch_openocd,
    receive_tcl_response,
    scalar_size,
    stop_managed_openocd,
    type_label,
    validate_openocd_config,
)
from .workers import (BackendCloseWorker, BackendReconnectWorker, DebuggerDiscoveryWorker,
                      ElfFlashWorker, ElfParseWorker,
                      HighSpeedSamplingWorker, LowSpeedReadWorker, OpenOcdFlashWorker,
                      SampleHistoryWriter, VariableWriteWorker)
from .spectrum import (DEFAULT_FFT_SETTINGS, WINDOW_LABELS, analyze_spectrum,
                       normalize_fft_settings, spectrum_display_values)

pg.setConfigOptions(useOpenGL=False, antialias=True)

try:
    import pylink
except ImportError:
    pylink = None


class SilentMessageBox:
    """Application-styled modal messages that never invoke a system alert sound."""

    StandardButton = _QMessageBox.StandardButton
    _BUTTONS = (
        (_QMessageBox.StandardButton.Yes, "是"),
        (_QMessageBox.StandardButton.No, "否"),
        (_QMessageBox.StandardButton.Ok, "确定"),
        (_QMessageBox.StandardButton.Cancel, "取消"),
    )
    _KINDS = {
        "information": ("i", "#0a84ff"),
        "warning": ("!", "#ff9f0a"),
        "critical": ("×", "#ff453a"),
        "question": ("?", "#0a84ff"),
    }

    @classmethod
    def _show(cls, parent, title, text, kind, buttons=None, default_button=None):
        buttons = buttons or cls.StandardButton.Ok
        default_button = default_button or cls.StandardButton.NoButton
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(430)
        dialog.setMaximumWidth(720)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)

        content = QHBoxLayout()
        content.setSpacing(16)
        symbol, color = cls._KINDS[kind]
        icon = QLabel(symbol)
        icon.setFixedSize(34, 34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: {color}; color: white; border-radius: 17px; "
            "font-size: 20px; font-weight: 800;"
        )
        content.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        message = QLabel(str(text))
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.addWidget(message, 1)
        layout.addLayout(content)

        result = {"button": cls.StandardButton.NoButton}
        if buttons & cls.StandardButton.No:
            result["button"] = cls.StandardButton.No
        elif buttons & cls.StandardButton.Cancel:
            result["button"] = cls.StandardButton.Cancel
        elif buttons & cls.StandardButton.Ok:
            result["button"] = cls.StandardButton.Ok

        actions = QHBoxLayout()
        actions.addStretch()
        for standard_button, label in cls._BUTTONS:
            if not buttons & standard_button:
                continue
            button = QPushButton(label)
            if standard_button in (cls.StandardButton.Yes, cls.StandardButton.Ok):
                button.setObjectName("primaryButton")
            button.clicked.connect(
                lambda _checked=False, value=standard_button: (
                    result.__setitem__("button", value), dialog.accept()
                )
            )
            actions.addWidget(button)
            if standard_button == default_button:
                button.setDefault(True)
                button.setFocus()
        layout.addLayout(actions)
        dialog.exec()
        return result["button"]

    @classmethod
    def information(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "information", buttons, default_button)

    @classmethod
    def warning(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "warning", buttons, default_button)

    @classmethod
    def critical(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "critical", buttons, default_button)

    @classmethod
    def question(cls, parent, title, text, buttons=None, default_button=None):
        return cls._show(parent, title, text, "question", buttons, default_button)


QMessageBox = SilentMessageBox


class HighContrastComboBox(_QComboBox):
    """Draw a crisp theme-aware chevron over the platform combo-box arrow."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup_open = False

    def showPopup(self):
        self._popup_open = True
        self.update()
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        self._popup_open = False
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        role = QPalette.ColorRole.Mid if not self.isEnabled() else QPalette.ColorRole.Text
        color = self.palette().color(role)
        if self.hasFocus() and self.isEnabled():
            color = self.palette().color(QPalette.ColorRole.Highlight)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_x = self.width() - 12
        center_y = self.height() // 2
        direction = -1 if self._popup_open else 1
        painter.drawLine(center_x - 4, center_y - 2 * direction,
                         center_x, center_y + 2 * direction)
        painter.drawLine(center_x, center_y + 2 * direction,
                         center_x + 4, center_y - 2 * direction)


QComboBox = HighContrastComboBox


class PathComboBox(HighContrastComboBox):
    """Editable path selector that keeps the old line-edit call surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(_QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(_QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(24)

    def text(self):
        return self.currentText()

    def setText(self, value):
        value = str(value or "")
        index = self.findText(value, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setEditText(value)


class HighContrastCheckBox(_QCheckBox):
    """Keep the checked state visible with every platform and application theme."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        checked = self.checkState() != Qt.CheckState.Unchecked
        fill = self.palette().color(
            QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Base
        )
        border = self.palette().color(
            QPalette.ColorRole.Highlight if checked else QPalette.ColorRole.Mid
        )
        if not self.isEnabled():
            fill = self.palette().color(QPalette.ColorRole.Window)
            border = self.palette().color(QPalette.ColorRole.Mid)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawRoundedRect(indicator.adjusted(1, 1, -1, -1), 3, 3)
        if checked:
            path = QPainterPath()
            path.moveTo(indicator.left() + 3.5, indicator.center().y())
            path.lineTo(indicator.left() + 6.5, indicator.bottom() - 4.0)
            path.lineTo(indicator.right() - 3.0, indicator.top() + 4.0)
            check_pen = QPen(QColor("#ffffff"), 2.0)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)


QCheckBox = HighContrastCheckBox


DARK_STYLE = """
    QWidget { background-color: #1c1c1e; color: #f2f2f7; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
    QDialog, QMainWindow { background-color: #1c1c1e; }
    QLabel { background-color: transparent; }
    QLabel#appTitle { color: #f3f3f3; font-size: 22px; font-weight: 700; letter-spacing: 1px; }
    QLabel#appSubtitle { color: #8b949e; font-size: 12px; }
    QLabel#sectionTitle { color: #e5e5ea; font-size: 12px; font-weight: 700; padding: 8px 0 4px 8px; border-left: 3px solid #0a84ff; }
    QLabel#fieldCaption { color: #9da5b4; font-size: 12px; font-weight: 700; }
    QLabel#fieldHint { color: #ffb340; font-size: 12px; padding-top: 2px; }
    QLabel#toolbarSeparator { color: #3c3c3c; padding: 0 2px; }
    QFrame#topBar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 9px; }
    QLabel#plotTitle { color: #d1d1d6; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #1f1f1f; border: 1px solid #2d2d30; border-radius: 6px; }
    QFrame#statusPanel { background-color: #242426; border: 1px solid #3a3a3c; border-radius: 10px; }
    QFrame#sliderControlPanel { background-color: #202225; border: 1px solid #3a3a3c; border-radius: 10px; }
    QLabel#sliderControlTitle { color: #f2f2f7; font-size: 13px; font-weight: 700; }
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
    QPushButton#profileAddButton { min-height: 30px; padding: 0; color: #64b5ff; font-size: 20px; font-weight: 500; }
    QPushButton#profileDeleteButton { min-height: 30px; padding: 0; color: #ff6961; font-size: 19px; font-weight: 500; }
    QPushButton#backConfigButton { background-color: #252a32; border: 1px solid #465264; color: #d8e8f8; border-radius: 9px; padding: 0px; }
    QPushButton#backConfigButton:hover { background-color: #2e4058; border-color: #5b8fc4; color: #ffffff; }
    QPushButton#backConfigButton:pressed { background-color: #20344c; }
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
    QLabel#fieldHint { color: #b45309; font-size: 12px; padding-top: 2px; }
    QLabel#toolbarSeparator { color: #cbd5e1; padding: 0 2px; }
    QFrame#topBar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 11px; }
    QWidget#plotToolbar { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 9px; }
    QLabel#plotTitle { color: #3a3a3c; font-size: 13px; font-weight: 700; }
    QFrame#heroPanel { background-color: #f8fafc; border: 1px solid #dbe3ec; border-radius: 6px; }
    QFrame#statusPanel { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; }
    QFrame#sliderControlPanel { background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 10px; }
    QLabel#sliderControlTitle { color: #172033; font-size: 13px; font-weight: 700; }
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
    QPushButton#profileAddButton { min-height: 30px; padding: 0; color: #007aff; font-size: 20px; font-weight: 500; }
    QPushButton#profileDeleteButton { min-height: 30px; padding: 0; color: #d92d20; font-size: 19px; font-weight: 500; }
    QPushButton#backConfigButton { background-color: #f7faff; border: 1px solid #b9cbe0; color: #24557f; border-radius: 9px; padding: 0px; }
    QPushButton#backConfigButton:hover { background-color: #e8f2ff; border-color: #7aaee0; color: #123d63; }
    QPushButton#backConfigButton:pressed { background-color: #dcecff; }
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
    QTreeWidget { background-color: #ffffff; border: 1px solid #d1d1d6; border-radius: 10px; alternate-background-color: #f8f8fa; outline: 0; }
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
    QMenu { background-color: #ffffff; color: #1f2937; border: 1px solid #c7d1dc; border-radius: 11px; padding: 7px; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
    QMenu::item { padding: 8px 30px 8px 13px; border-radius: 7px; margin: 1px 0px; background-color: transparent; color: #1f2937; }
    QMenu::item:selected { background-color: #dff2fa; color: #066cae; }
    QMenu::item:disabled { color: #9aa5b1; }
    QMenu::separator { height: 1px; background-color: #d8e0e8; margin: 6px 9px; }
"""

DARK_POPUP_MENU_STYLE = """
    QMenu { background-color: #20242b; color: #e6e9ee; border: 1px solid #3d4858; border-radius: 11px; padding: 7px; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
    QMenu::item { padding: 8px 30px 8px 13px; border-radius: 7px; margin: 1px 0px; background-color: transparent; }
    QMenu::item:selected { background-color: #293b54; color: #ffffff; }
    QMenu::item:disabled { color: #6f7b89; }
    QMenu::separator { height: 1px; background-color: #3d4858; margin: 6px 9px; }
"""


def application_theme_stylesheet(theme_mode):
    return LIGHT_STYLE if str(theme_mode).upper() == "LIGHT" else DARK_STYLE


class RoundedMenu(QMenu):
    """Rounded application menu with matching rounded submenus."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 11, 11)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def addMenu(self, menu_or_title):
        if isinstance(menu_or_title, str):
            submenu = RoundedMenu(menu_or_title, self)
            submenu.setStyleSheet(self.styleSheet())
            super().addMenu(submenu)
            return submenu
        return super().addMenu(menu_or_title)


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
        super().keyPressEvent(event)



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
        reload_button = QPushButton("重新提取 ELF")
        reload_button.setToolTip("重新读取当前 ELF 的符号表和 DWARF，并刷新已有监视变量的地址与结构")
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

class LoginConfigDialog(QDialog):
    def __init__(self, db, enable_global_discovery=True):
        super().__init__()
        self.db = db
        self.config = ProjectConfig()
        self.is_connected = False
        self.discovery_worker = None
        self.enable_global_discovery = bool(enable_global_discovery)
        self.setWindowTitle(f"MCU Live Watch Studio V{__version__}")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumSize(760, 600)
        self.resize(900, 680)
        self.init_ui()
        self.load_profiles()
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

        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("切换项目配置；主配置固定保留")
        self.profile_combo.currentIndexChanged.connect(self.profile_changed)
        self.btn_profile_add = QPushButton("+")
        self.btn_profile_add.setObjectName("profileAddButton")
        self.btn_profile_add.setFixedWidth(36)
        self.btn_profile_add.setToolTip("新建配置")
        self.btn_profile_add.clicked.connect(self.create_profile)
        self.btn_profile_delete = QPushButton("×")
        self.btn_profile_delete.setObjectName("profileDeleteButton")
        self.btn_profile_delete.setFixedWidth(36)
        self.btn_profile_delete.setToolTip("删除当前配置；主配置不能删除")
        self.btn_profile_delete.clicked.connect(self.delete_current_profile)
        self.btn_profile_clear = QPushButton("清空配置")
        self.btn_profile_clear.setProperty("commandButton", True)
        self.btn_profile_clear.setFixedWidth(82)
        self.btn_profile_clear.setToolTip("保留配置名称，清除该配置内的工程和工作区数据")
        self.btn_profile_clear.clicked.connect(self.clear_current_profile)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_row.addWidget(self.profile_combo, 1)
        profile_row.addWidget(self.btn_profile_add)
        profile_row.addWidget(self.btn_profile_delete)
        profile_row.addWidget(self.btn_profile_clear)
        form.addRow("项目配置", profile_row)

        appearance_section = QLabel("外观与固件")
        appearance_section.setObjectName("sectionTitle")
        form.addRow(appearance_section)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["深色模式（默认）", "浅色模式"])
        self.theme_combo.currentIndexChanged.connect(self.apply_selected_theme)
        form.addRow("界面主题", self.theme_combo)
        
        self.elf_edit = QLineEdit()
        btn_elf = QPushButton("浏览…")
        btn_elf.setFixedWidth(76)
        btn_elf.clicked.connect(lambda: self.select_file(self.elf_edit, "选择 ELF 固件", "ELF Files (*.elf)"))
        h1 = QHBoxLayout(); h1.setSpacing(6); h1.addWidget(self.elf_edit, 1); h1.addWidget(btn_elf)
        form.addRow("ELF 固件", h1)

        backend_section = QLabel("调试后端")
        backend_section.setObjectName("sectionTitle")
        form.addRow(backend_section)

        self.driver_mode_combo = QComboBox()
        self.driver_mode_combo.addItems(["J-Link 原生驱动", "ST-Link / CMSIS-DAP（OpenOCD）"])
        self.driver_mode_combo.currentIndexChanged.connect(self.toggle_engine_fields)
        form.addRow("后端类型", self.driver_mode_combo)

        self.lbl_jl_dir = QLabel("J-Link 安装目录")
        self.jlink_dir_edit = PathComboBox()
        self.jlink_dir_edit.activated.connect(lambda _index: self.static_env_check())
        self.jlink_dir_edit.lineEdit().editingFinished.connect(self.static_env_check)
        self.btn_jl_dir = QPushButton("浏览…")
        self.btn_jl_dir.setFixedWidth(76)
        self.btn_jl_dir.clicked.connect(self.select_jlink_dir)
        self.btn_detect_jlink = QPushButton("扫描电脑")
        self.btn_detect_jlink.setProperty("commandButton", True)
        self.btn_detect_jlink.setFixedWidth(88)
        self.btn_detect_jlink.setToolTip("立即检测常见位置，并在后台扫描本机所有固定磁盘")
        self.btn_detect_jlink.clicked.connect(self.auto_detect_jlink)
        self.h_jl_dir = QHBoxLayout(); self.h_jl_dir.setSpacing(6); self.h_jl_dir.addWidget(self.jlink_dir_edit, 1); self.h_jl_dir.addWidget(self.btn_jl_dir); self.h_jl_dir.addWidget(self.btn_detect_jlink)

        self.lbl_jl_dev = QLabel("目标器件")
        self.jlink_device_edit = QLineEdit("Cortex-M0+")
        self.jlink_device_edit.textEdited.connect(lambda text: self.jlink_device_edit.setProperty("connect_device", text.strip()))
        self.btn_jl_device = QPushButton("选择芯片…")
        self.btn_jl_device.setFixedWidth(92)
        self.btn_jl_device.clicked.connect(self.open_jlink_device_selector)
        self.h_jl_device = QHBoxLayout(); self.h_jl_device.setSpacing(6); self.h_jl_device.addWidget(self.jlink_device_edit, 1); self.h_jl_device.addWidget(self.btn_jl_device)

        form.addRow(self.lbl_jl_dir, self.h_jl_dir)
        form.addRow(self.lbl_jl_dev, self.h_jl_device)

        self.lbl_ocd_exe = QLabel("OpenOCD 程序")
        self.openocd_edit = PathComboBox()
        self.openocd_edit.activated.connect(lambda _index: self.refresh_openocd_cfgs())
        self.openocd_edit.lineEdit().editingFinished.connect(self.refresh_openocd_cfgs)
        self.btn_ocd = QPushButton("浏览…")
        self.btn_ocd.setFixedWidth(76)
        self.btn_ocd.clicked.connect(self.select_openocd_executable)
        self.btn_detect_openocd = QPushButton("扫描电脑")
        self.btn_detect_openocd.setProperty("commandButton", True)
        self.btn_detect_openocd.setFixedWidth(88)
        self.btn_detect_openocd.setToolTip("立即检测常见位置，并在后台扫描本机所有固定磁盘")
        self.btn_detect_openocd.clicked.connect(self.auto_detect_openocd)
        self.h_ocd_exe = QHBoxLayout(); self.h_ocd_exe.setSpacing(6); self.h_ocd_exe.addWidget(self.openocd_edit, 1); self.h_ocd_exe.addWidget(self.btn_ocd); self.h_ocd_exe.addWidget(self.btn_detect_openocd)

        self.lbl_ocd_intf = QLabel("探针配置")
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.btn_browse_intf = QPushButton("浏览…")
        self.btn_browse_intf.setFixedWidth(76)
        self.btn_browse_intf.clicked.connect(lambda: self.select_cfg_file(self.interface_combo, "interface"))
        self.h_ocd_intf = QHBoxLayout(); self.h_ocd_intf.setSpacing(6); self.h_ocd_intf.addWidget(self.interface_combo, 1); self.h_ocd_intf.addWidget(self.btn_browse_intf)

        self.lbl_ocd_targ = QLabel("目标配置")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.btn_browse_target = QPushButton("浏览…")
        self.btn_browse_target.setFixedWidth(76)
        self.btn_browse_target.clicked.connect(lambda: self.select_cfg_file(self.target_combo, "target"))
        self.h_ocd_targ = QHBoxLayout(); self.h_ocd_targ.setSpacing(6); self.h_ocd_targ.addWidget(self.target_combo, 1); self.h_ocd_targ.addWidget(self.btn_browse_target)

        form.addRow(self.lbl_ocd_exe, self.h_ocd_exe)
        form.addRow(self.lbl_ocd_intf, self.h_ocd_intf)
        form.addRow(self.lbl_ocd_targ, self.h_ocd_targ)

        self.log_checkbox = QCheckBox("在独立窗口显示 OpenOCD 日志")
        self.lbl_openocd_reset_hint = QLabel("连接或重连 OpenOCD 可能复位 MCU")
        self.lbl_openocd_reset_hint.setObjectName("fieldHint")
        log_field = QWidget()
        log_layout = QVBoxLayout(log_field)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)
        log_layout.addWidget(self.log_checkbox)
        log_layout.addWidget(self.lbl_openocd_reset_hint)
        form.addRow("调试日志", log_field)

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

    def load_profiles(self, selected_id=None):
        selected_id = int(selected_id or self.db.active_profile_id)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        selected_index = 0
        for index, (profile_id, name, is_main) in enumerate(self.db.list_profiles()):
            self.profile_combo.addItem(str(name), (int(profile_id), bool(is_main)))
            if int(profile_id) == selected_id:
                selected_index = index
        self.profile_combo.setCurrentIndex(selected_index)
        self.profile_combo.blockSignals(False)
        self.update_profile_actions()

    def current_profile_record(self):
        data = self.profile_combo.currentData()
        return data if isinstance(data, tuple) and len(data) == 2 else (1, True)

    def update_profile_actions(self):
        _profile_id, is_main = self.current_profile_record()
        self.btn_profile_delete.setEnabled(not is_main)

    def profile_changed(self, index):
        if index < 0:
            return
        profile_id, _is_main = self.current_profile_record()
        if int(profile_id) == int(self.db.active_profile_id):
            self.update_profile_actions()
            return
        self.stop_global_discovery()
        self.db.set_active_profile(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()
        self.update_profile_actions()

    def create_profile(self):
        default_name = f"配置 {len(self.db.list_profiles()) + 1}"
        name, accepted = QInputDialog.getText(self, "新建配置", "配置名称", text=default_name)
        if not accepted:
            return
        try:
            profile_id = self.db.create_profile(name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法新建配置", str(exc))
            return
        self.load_profiles(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()

    def delete_current_profile(self):
        profile_id, is_main = self.current_profile_record()
        if is_main:
            QMessageBox.information(self, "主配置", "主配置固定保留，不能删除。")
            return
        name = self.profile_combo.currentText()
        answer = QMessageBox.question(
            self,
            "删除配置",
            f"确定删除“{name}”及其变量、波形和历史数据吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.delete_profile(profile_id)
        self.load_profiles(1)
        self.load_saved_config()
        self.toggle_engine_fields()

    def clear_current_profile(self):
        profile_id, _is_main = self.current_profile_record()
        name = self.profile_combo.currentText()
        answer = QMessageBox.question(
            self,
            "清空配置",
            f"确定清空“{name}”吗？\n\n"
            "配置名称会保留；数据库中的 UI 状态、工程设置、变量、波形绑定和用于 CSV 导出的历史采样将被删除。\n"
            "已经导出到磁盘的 CSV 文件不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_profile(profile_id)
        self.load_saved_config()
        self.toggle_engine_fields()
        self.lbl_hardware_status.setText(f"已清空配置：{name}")

    def toggle_engine_fields(self):
        is_jlink = (self.driver_mode_combo.currentIndex() == 0)
        self.jlink_dir_edit.setEnabled(is_jlink)
        self.btn_jl_dir.setEnabled(is_jlink)
        self.btn_detect_jlink.setEnabled(is_jlink)
        self.jlink_device_edit.setEnabled(is_jlink)
        self.btn_jl_device.setEnabled(is_jlink)
        self.openocd_edit.setEnabled(not is_jlink)
        self.btn_ocd.setEnabled(not is_jlink)
        self.btn_detect_openocd.setEnabled(not is_jlink)
        self.interface_combo.setEnabled(not is_jlink)
        self.btn_browse_intf.setEnabled(not is_jlink)
        self.target_combo.setEnabled(not is_jlink)
        self.btn_browse_target.setEnabled(not is_jlink)
        self.log_checkbox.setEnabled(not is_jlink)
        self.lbl_openocd_reset_hint.setEnabled(not is_jlink)
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

    @staticmethod
    def _cached_path_list(raw_value):
        try:
            values = json.loads(raw_value or "[]")
        except (TypeError, ValueError):
            values = []
        return [str(value) for value in values if isinstance(value, str) and value.strip()]

    @staticmethod
    def _merge_path_choices(combo, paths, selected=None):
        current = str(selected if selected is not None else combo.currentText()).strip()
        candidates = [current]
        candidates.extend(combo.itemText(index) for index in range(combo.count()))
        candidates.extend(paths)
        unique = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = os.path.normpath(os.path.abspath(os.path.expandvars(candidate)))
            key = os.path.normcase(normalized)
            if key not in seen:
                seen.add(key)
                unique.append(normalized)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(unique)
        if current:
            normalized_current = os.path.normpath(os.path.abspath(os.path.expandvars(current)))
            index = combo.findText(normalized_current, Qt.MatchFlag.MatchFixedString)
            combo.setCurrentIndex(index if index >= 0 else 0)
        elif combo.count():
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def start_global_discovery(self):
        if self.discovery_worker and self.discovery_worker.isRunning():
            return
        worker = DebuggerDiscoveryWorker(self)
        self.discovery_worker = worker
        worker.progress_signal.connect(self._global_discovery_progress)
        worker.finished_signal.connect(self._global_discovery_finished)
        worker.finished.connect(lambda source=worker: self._global_discovery_stopped(source))
        worker.start()
        self.lbl_hardware_status.setText("已加载快速检测结果，正在后台扫描本机固定磁盘…")

    def _global_discovery_progress(self, jlink_paths, openocd_paths, visited, drive):
        self._merge_path_choices(self.jlink_dir_edit, jlink_paths)
        self._merge_path_choices(self.openocd_edit, openocd_paths)
        location = f" · {drive}" if drive else ""
        self.lbl_hardware_status.setText(
            f"正在扫描本机调试工具：{visited:,} 个目录{location} · "
            f"J-Link {len(jlink_paths)} · OpenOCD {len(openocd_paths)}"
        )

    def _global_discovery_finished(self, jlink_paths, openocd_paths):
        self._merge_path_choices(self.jlink_dir_edit, jlink_paths)
        self._merge_path_choices(self.openocd_edit, openocd_paths)
        combined_jlink = [self.jlink_dir_edit.itemText(i) for i in range(self.jlink_dir_edit.count())]
        combined_openocd = [self.openocd_edit.itemText(i) for i in range(self.openocd_edit.count())]
        self.db.save_config("discovered_jlink_paths", json.dumps(combined_jlink, ensure_ascii=False))
        self.db.save_config("discovered_openocd_paths", json.dumps(combined_openocd, ensure_ascii=False))
        self.lbl_hardware_status.setText(
            f"全机扫描完成：J-Link {len(combined_jlink)} 个，OpenOCD {len(combined_openocd)} 个；可在下拉框选择"
        )
        self.static_env_check()

    def _global_discovery_stopped(self, worker):
        if self.discovery_worker is worker:
            self.discovery_worker = None
        worker.deleteLater()

    def stop_global_discovery(self):
        worker = self.discovery_worker
        if not worker:
            return True
        worker.requestInterruption()
        stopped = worker.wait(1500)
        if not stopped:
            for signal in (worker.progress_signal, worker.finished_signal, worker.finished):
                try:
                    signal.disconnect()
                except (TypeError, RuntimeError):
                    pass
            app = QApplication.instance()
            if app is not None:
                worker.setParent(app)
            worker.finished.connect(worker.deleteLater)
            self.discovery_worker = None
        return stopped

    def select_openocd_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 openocd.exe", "", "Executable (*.exe)")
        if path:
            normalized = os.path.normpath(path)
            self._merge_path_choices(self.openocd_edit, [normalized], normalized)
            self.refresh_openocd_cfgs()

    def auto_detect_openocd(self):
        saved = self.db.get_config("openocd_exe", "").strip()
        cached = self._cached_path_list(self.db.get_config("discovered_openocd_paths", "[]"))
        matches = discover_openocd_executables(saved, self.openocd_edit.text().strip())
        matches.extend(path for path in cached if os.path.isfile(path) and path not in matches)
        if matches:
            self._merge_path_choices(self.openocd_edit, matches, matches[0])
            self.refresh_openocd_cfgs()
            source = "数据库配置" if saved and os.path.normcase(matches[0]) == os.path.normcase(os.path.abspath(saved)) else "本机搜索"
            self.lbl_hardware_status.setText(f"已从{source}找到 OpenOCD：{matches[0]}")
            self.static_env_check()
        else:
            self.lbl_hardware_status.setText("快速检测未找到 OpenOCD，正在扫描本机固定磁盘…")
        if self.enable_global_discovery:
            self.start_global_discovery()

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
            normalized = os.path.normpath(path)
            self._merge_path_choices(self.jlink_dir_edit, [normalized], normalized)
            self.static_env_check()

    def auto_detect_jlink(self):
        saved = self.db.get_config("jlink_dll_dir", "").strip()
        cached = self._cached_path_list(self.db.get_config("discovered_jlink_paths", "[]"))
        matches = discover_jlink_directories(saved, self.jlink_dir_edit.text().strip())
        for path in cached:
            try:
                jlink_dll_path(path)
            except (OSError, ValueError):
                continue
            if path not in matches:
                matches.append(path)
        if matches:
            self._merge_path_choices(self.jlink_dir_edit, matches, matches[0])
            source = "数据库配置" if saved and os.path.normcase(matches[0]) == os.path.normcase(os.path.abspath(saved)) else "本机搜索"
            self.lbl_hardware_status.setText(f"已从{source}找到 J-Link：{matches[0]}")
            self.static_env_check()
        else:
            self.lbl_hardware_status.setText("快速检测未找到 J-Link，正在扫描本机固定磁盘…")
        if self.enable_global_discovery:
            self.start_global_discovery()

    def open_jlink_device_selector(self):
        dll_dir = self.jlink_dir_edit.text().strip()
        dialog = JLinkDeviceDialog(dll_dir, self.jlink_device_edit.text().strip(), self)
        dialog.device_selected.connect(self.apply_jlink_device_selection)
        dialog.exec()

    def apply_jlink_device_selection(self, display_name, connect_name):
        self.jlink_device_edit.setText(display_name)
        self.jlink_device_edit.setProperty("connect_device", connect_name)
        if connect_name != display_name:
            self.jlink_device_edit.setToolTip(f"XML 扩展器件；实时调试按内核 {connect_name} 连接")
        else:
            self.jlink_device_edit.setToolTip("")

    def select_cfg_file(self, combo, subfolder):
        exe_path = self.openocd_edit.text().strip()
        initial_dir = os.path.join(os.path.dirname(exe_path), "..", "share", "openocd", "scripts", subfolder)
        if not os.path.isdir(initial_dir): initial_dir = ""
        path, _ = QFileDialog.getOpenFileName(self, f"选择自定义 {subfolder} 配置文件", initial_dir, "Config Files (*.cfg)")
        if path: combo.setEditText(os.path.normpath(path))

    def load_saved_config(self):
        self.elf_edit.setText(self.db.get_config("elf_path"))
        self.openocd_edit.clear()
        saved_openocd = self.db.get_config("openocd_exe", "").strip() or default_openocd_executable()
        cached_openocd = self._cached_path_list(self.db.get_config("discovered_openocd_paths", "[]"))
        remembered_openocd = [saved_openocd]
        remembered_openocd.extend(path for path in cached_openocd if os.path.isfile(path))
        bundled_openocd = default_openocd_executable()
        if os.path.isfile(bundled_openocd):
            remembered_openocd.append(bundled_openocd)
        self._merge_path_choices(self.openocd_edit, remembered_openocd, saved_openocd)
        self.refresh_openocd_cfgs()
        saved_jlink = self.db.get_config("jlink_dll_dir", "C:\\Program Files\\SEGGER\\JLink")
        self.jlink_dir_edit.clear()
        cached_jlink = self._cached_path_list(self.db.get_config("discovered_jlink_paths", "[]"))
        remembered_jlink = [saved_jlink]
        for path in cached_jlink:
            try:
                jlink_dll_path(path)
            except (OSError, ValueError):
                continue
            if path not in remembered_jlink:
                remembered_jlink.append(path)
        self._merge_path_choices(self.jlink_dir_edit, remembered_jlink, saved_jlink)
        self.jlink_device_edit.setText(self.db.get_config("jlink_device", "Cortex-M0+"))
        self.jlink_device_edit.setProperty("connect_device", self.db.get_config("jlink_connect_device", self.jlink_device_edit.text()))
        saved_theme = self.db.get_config("theme_mode", "DARK").upper()
        self.theme_combo.setCurrentIndex(1 if saved_theme == "LIGHT" else 0)
        self.apply_selected_theme()
        
        saved_mode = self.db.get_config("backend_mode", "JLINK")
        self.driver_mode_combo.setCurrentIndex(0 if saved_mode == "JLINK" else 1)
        
        saved_intf = self.db.get_config("last_interface_cfg")
        if saved_intf: self._set_combo_config_value(self.interface_combo, saved_intf)
        saved_targ = self.db.get_config("last_target_cfg")
        if saved_targ: self._set_combo_config_value(self.target_combo, saved_targ)
        self.log_checkbox.setChecked(self.db.get_config("show_openocd_log", "0") == "1")

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
                validate_openocd_config(self.config)
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
            self.db.save_config("show_openocd_log", "1" if self.config.show_log_window else "0")
        else:
            self.db.save_config("jlink_dll_dir", self.config.jlink_dll_dir)
            self.db.save_config("jlink_device", self.config.jlink_device)
            self.db.save_config("jlink_connect_device", self.config.jlink_connect_device)

        self.is_connected = True
        self.accept()

    def done(self, result):
        if hasattr(self, "detect_timer"):
            self.detect_timer.stop()
        self.stop_global_discovery()
        super().done(result)


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


class MCUStudioWindow(QMainWindow):
    WORKSPACE_STATE_KEY = "workspace_state_v1"
    INLINE_SLIDER_STATE_KEY = "inline_slider_state_v1"
    FFT_STATE_KEY = "fft_plot_state_v1"
    SLIDER_CONTROL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    variable_write_result = pyqtSignal(str, int, bool, str, object)
    session_closed = pyqtSignal(str)

    def __init__(self, config, db):
        super().__init__()
        self.config = config
        self.db = db
        self._close_destination = "exit"
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
        self.backend_suspended = False
        self.flash_sequence_active = False
        self.elf_reload_in_progress = False
        self.elf_update_pending = False
        self.jlink_native_instance = None 
        self.jlink_lock = threading.RLock()
        self.openocd_link_alive = False  
        self.latest_values = {}
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

        self.init_global_link_backend()
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

    def _start_initial_elf_load(self, elf_path):
        """Show the workspace first, then parse the initial ELF asynchronously."""
        if self.elf_reload_in_progress or self.initial_workspace_loaded:
            return
        self.elf_reload_in_progress = True
        self.lbl_status_text.setText("正在后台解析 ELF…")
        self.btn_elf_browser.setEnabled(False)
        result = {"ok": False, "database": None, "error": ""}
        worker = ElfParseWorker(elf_path, self)
        self.elf_parse_worker = worker

        def receive_result(ok, database, error):
            result.update(ok=bool(ok), database=database, error=str(error or ""))

        def finish_initial_parse():
            self.elf_reload_in_progress = False
            self.btn_elf_browser.setEnabled(True)
            self.elf_parse_worker = None
            worker.deleteLater()
            if result["ok"] and result["database"] is not None:
                self.elf_db = result["database"]
                self.symbol_cache = dict(self.elf_db.symbols)
                print(f"ELF loaded: {len(self.symbol_cache)} symbols, {len(self.elf_db.types)} DWARF variables")
                self.lbl_status_text.setText("ELF 已加载")
                self._finish_initial_workspace_load(synchronize_elf=True)
            else:
                print(f"❌ ELF 符号表加载异常: {result['error']}")
                self.lbl_status_text.setText("ELF 解析失败，已保留数据库配置")
                self._finish_initial_workspace_load(synchronize_elf=False)

        worker.result_ready.connect(receive_result)
        worker.finished.connect(finish_initial_parse)
        worker.start()

    def _finish_initial_workspace_load(self, synchronize_elf):
        if self.initial_workspace_loaded:
            return
        self.load_history_from_db(
            synchronize_elf=synchronize_elf,
            remove_missing=synchronize_elf,
        )
        self.restore_workspace_state()
        self.workspace_restore_in_progress = False
        self.initial_workspace_loaded = True
        self.schedule_workspace_save()

    def _parse_elf_in_background(self, elf_path):
        """Parse ELF on a worker while a local event loop keeps the UI responsive."""
        result = {"ok": False, "database": None, "error": "ELF 解析线程未返回结果"}
        loop = QEventLoop(self)
        worker = ElfParseWorker(elf_path, self)
        self.elf_parse_worker = worker

        def receive_result(ok, database, error):
            result.update(ok=bool(ok), database=database, error=str(error or ""))

        worker.result_ready.connect(receive_result)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()
        worker.wait()
        worker.deleteLater()
        self.elf_parse_worker = None
        return result["ok"], result["database"], result["error"]

    def reload_elf_symbols(self, show_message=True, source="manual"):
        """Atomically reload ELF/DWARF and refresh saved trees and channel addresses."""
        if self.elf_reload_in_progress:
            return False, "ELF 正在后台解析，请稍候"
        elf_path = os.path.abspath(self.config.elf_path) if self.config.elf_path else ""
        if not os.path.isfile(elf_path):
            error = f"找不到 ELF：\n{elf_path or '(未设置)'}"
            if show_message: QMessageBox.warning(self, "重新提取 ELF 失败", error)
            return False, error

        self.elf_reload_in_progress = True
        self.lbl_status_text.setText("正在后台解析 ELF…")
        ok, refreshed, parse_error = self._parse_elf_in_background(elf_path)
        if not ok or refreshed is None:
            self.elf_reload_in_progress = False
            error = f"ELF 解析失败，已保留原来的变量数据：\n{parse_error}"
            self.lbl_status_text.setText("ELF 解析失败，已保留原有变量")
            if show_message: QMessageBox.critical(self, "重新提取 ELF 失败", error)
            return False, error

        self.stop_sampling_thread()

        try:
            self.elf_db = refreshed
            self.symbol_cache = dict(refreshed.symbols)

            for tree in self.debug_trees.values():
                tree.clear()
            self.load_history_from_db(remove_missing=source in ("flash", "manual"))
            unavailable_count = self._rebuild_sampling_channels_from_bindings()

            if hasattr(self, 'adv_parse_dialog') and self.adv_parse_dialog:
                try: self.adv_parse_dialog.replace_elf_database(refreshed)
                except RuntimeError: self.adv_parse_dialog = None
            self.refresh_ui_channel_bar()
            self.reset_canvas_adapt_ranges()
            self.schedule_workspace_save()
            self.elf_last_signature = self._elf_file_signature()
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            self._set_elf_update_pending(False)
        finally:
            self.elf_reload_in_progress = False

        self.restart_sampling_thread()
        summary = f"已重新提取 ELF：{len(refreshed.symbols)} 个符号，{len(refreshed.types)} 个 DWARF 变量。"
        if unavailable_count:
            summary += f" {unavailable_count} 个已监视变量暂时不可用，配置已保留。"
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
        if signature is None:
            self.elf_pending_signature = None
            self.elf_pending_stable_ticks = 0
            return
        if signature == self.elf_last_signature:
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
        # Compilation must not change the active symbol map while the target
        # still runs the previous firmware.  The new ELF becomes authoritative
        # only after a successful flash (or an explicit manual reload).
        self.elf_last_signature = signature
        self.elf_pending_signature = None
        self.elf_pending_stable_ticks = 0
        self._set_elf_update_pending(True)
        self.lbl_status_text.setText("检测到新 ELF；继续使用旧映射，烧录成功后自动同步")

    def _set_elf_update_pending(self, pending):
        self.elf_update_pending = bool(pending)
        if not hasattr(self, "btn_flash") or self.flash_sequence_active:
            return
        self.btn_flash.setText("烧录*" if self.elf_update_pending else "烧录")
        self.btn_flash.setToolTip(
            "检测到新 ELF；当前继续按旧 ELF 调试，烧录成功后自动切换并同步变量"
            if self.elf_update_pending else
            "通过当前调试后端烧录 ELF；只有点击并确认后才会擦写 Flash/复位"
        )

    def type_info_for_path(self, path):
        if path.startswith(("0x", "0X")): return None
        try: return self.elf_db.resolve(path)[1]
        except Exception: return None

    def resolve_saved_variable_address(self, path, offset, base_var):
        """Resolve a saved watch against the current ELF, never a stale cached address."""
        if base_var.startswith(("0x", "0X")):
            return int(base_var, 16) + int(offset)
        try:
            return int(self.elf_db.resolve(path)[0])
        except (KeyError, ValueError, IndexError):
            base_address = self.symbol_cache.get(base_var)
            return None if base_address is None else int(base_address) + int(offset)

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
        self.btn_reconnect.setToolTip("重新启动并连接当前配置的通信后端，不复位 MCU")
        self.btn_reconnect.clicked.connect(self.force_reconnect_engine)
        
        self.btn_mcu_reset = QPushButton("复位")
        self.btn_mcu_reset.setProperty("commandButton", True)
        self.btn_mcu_reset.setProperty("commandRole", "reset")
        self.btn_mcu_reset.clicked.connect(self.trigger_manual_mcu_reset)

        self.btn_flash = QPushButton("烧录")
        self.btn_flash.setProperty("commandButton", True)
        self.btn_flash.setToolTip("通过当前调试后端烧录 ELF；只有点击并确认后才会擦写 Flash/复位")
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
            
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["全局变量", "数据类型", "实时值（5 Hz）"])
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tree.header().setMinimumSectionSize(70)
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

    def _set_flash_progress(self, state, text="", details=""):
        states = {
            "idle": ("FLASH READY", "#8e8e93", 0),
            "running": ("PROGRAMMING…", "#0a84ff", None),
            "success": ("DOWNLOAD OK", "#34c759", 100),
            "warning": ("FLASH OK · LINK LOST", "#ff9f0a", 100),
            "failed": ("DOWNLOAD FAILED", "#ff453a", 100),
        }
        label, color, value = states[state]
        self.lbl_flash_progress.setText(text or label)
        self.lbl_flash_progress.setStyleSheet(
            f"color: {color}; font-family: 'Consolas'; font-size: 12px; font-weight: 700;"
        )
        track = "#303033" if self.is_dark_theme else "#e5e7eb"
        border = "#48484a" if self.is_dark_theme else "#cbd5e1"
        self.flash_progress.setStyleSheet(
            f"QProgressBar {{ background: {track}; border: 1px solid {border}; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        if value is None:
            self.flash_progress.setRange(0, 0)
        else:
            self.flash_progress.setRange(0, 100)
            self.flash_progress.setValue(value)
        self.flash_status_widget.setToolTip(details)

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
        if self.flash_sequence_active:
            return
        elf_path = os.path.abspath(self.config.elf_path)
        if self.config.backend_mode == "OPENOCD":
            self._flash_elf_with_openocd(elf_path)
            return
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
        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("正在烧录…")
        self.lbl_status_text.setText("正在烧录 ELF（界面仍可响应）")
        self._set_flash_progress("running", details=elf_path)
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.variable_write_worker.update_jlink(None)
        self.pending_flash_args = (
            commander, jlink_dir, elf_path, device, self.config.jlink_connect_device
        )
        if old_link:
            self._close_native_link_async(old_link, next_action="flash")
            return
        self._start_pending_flash()

    def _flash_elf_with_openocd(self, elf_path):
        if not os.path.isfile(elf_path):
            QMessageBox.warning(self, "无法烧录", f"找不到 ELF：\n{elf_path}")
            return
        try:
            validate_openocd_config(self.config)
        except Exception as exc:
            QMessageBox.warning(self, "无法烧录", str(exc))
            return

        self.btn_flash.setEnabled(False)
        self.btn_flash.setText("正在烧录…")
        self.lbl_status_text.setText("正在通过 OpenOCD 烧录 ELF（界面仍可响应）")
        self._set_flash_progress("running", details=elf_path)
        self.flash_sequence_active = True
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        if self.tcl_socket:
            try:
                self.tcl_socket.close()
            except OSError:
                pass
        self.tcl_socket = None
        self.openocd_link_alive = False
        self.openocd_flash_worker = OpenOcdFlashWorker(self.config, elf_path, self)
        self.openocd_flash_worker.finished_signal.connect(self.handle_openocd_flash_finished)
        self.openocd_flash_worker.finished.connect(self.openocd_flash_worker.deleteLater)
        self.openocd_flash_worker.start()

    def _start_pending_flash(self):
        args = getattr(self, 'pending_flash_args', None)
        self.pending_flash_args = None
        if not args:
            return
        self.flash_worker = ElfFlashWorker(
            *args, self
        )
        self.flash_worker.finished_signal.connect(self.handle_flash_finished)
        self.flash_worker.finished.connect(self.flash_worker.deleteLater)
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
        self.variable_write_worker.update_jlink(link)
        self.flash_sequence_active = False
        self.backend_suspended = False
        self.btn_flash.setText("烧录")
        self.btn_flash.setEnabled(True)
        self.lbl_status_text.setText("烧录完成" if success else "烧录失败")
        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            self._set_flash_progress("success", details=message)
        else:
            self._set_elf_update_pending(self.elf_update_pending)
            self.restart_sampling_thread()
            self._set_flash_progress("failed", details=message)

    def handle_openocd_flash_finished(self, success, message, backend_ready):
        worker = getattr(self, "openocd_flash_worker", None)
        self.openocd_flash_worker = None
        self.flash_sequence_active = False
        self.backend_suspended = not backend_ready
        self.btn_flash.setText("烧录")
        self.btn_flash.setEnabled(True)
        self.btn_reconnect.setEnabled(True)

        if backend_ready:
            self.init_global_link_backend()
            self.variable_write_worker.update_jlink(self.jlink_native_instance)
            backend_ready = self.openocd_link_alive
            self.backend_suspended = not backend_ready
        self.btn_disconnect.setEnabled(backend_ready)
        if backend_ready:
            self.restart_sampling_thread()

        if success:
            reload_ok, reload_message = self.reload_elf_symbols(show_message=False, source="flash")
            message += f"\n\n{'✓' if reload_ok else '⚠'} {reload_message}"
            if backend_ready:
                self.lbl_status_text.setText("OpenOCD 烧录完成，调试连接已恢复")
                self._set_flash_progress("success", details=message)
            else:
                self.lbl_status_text.setText("烧录完成，OpenOCD 调试连接恢复失败")
                self._set_flash_progress("warning", details=message)
        else:
            self._set_elf_update_pending(self.elf_update_pending)
            self.lbl_status_text.setText("OpenOCD 烧录失败")
            self._set_flash_progress("failed", details=message)

    def force_reconnect_engine(self):
        if self.reconnect_worker and self.reconnect_worker.isRunning():
            return
        self._execute_flash_animation(self.btn_reconnect, "#00b0ff", "")
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
        self.stop_sampling_thread()
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.btn_mcu_reset.setEnabled(False)
        self.lbl_status_text.setText(f"正在后台重连 {self.config.backend_mode}…")
        if self.config.backend_mode == "OPENOCD":
            if self.tcl_socket: 
                try: self.tcl_socket.close()
                except: pass
            self.tcl_socket = None
            self.openocd_link_alive = False
            self._start_backend_reconnect()
            return

        old_link = self.jlink_native_instance
        self.jlink_native_instance = None
        self.variable_write_worker.update_jlink(None)
        if old_link:
            self._close_native_link_async(old_link, next_action="reconnect")
            self.lbl_status_text.setText("正在后台释放旧 J-Link 后端…")
        else:
            self._start_backend_reconnect()

    def _start_backend_reconnect(self):
        self.reconnect_worker = BackendReconnectWorker(self.config, self.jlink_lock, self)
        self.reconnect_worker.finished_signal.connect(self._backend_reconnect_finished)
        self.reconnect_worker.finished.connect(self.reconnect_worker.deleteLater)
        self.reconnect_worker.start()

    def _backend_reconnect_finished(self, success, error, backend):
        worker = self.reconnect_worker
        self.reconnect_worker = None
        if success:
            if self.config.backend_mode == "OPENOCD":
                self.tcl_socket = backend
                self.openocd_link_alive = True
            else:
                self.jlink_native_instance = backend
                self.variable_write_worker.update_jlink(backend)
            self.backend_suspended = False
        else:
            self.backend_suspended = True
        self.btn_flash.setEnabled(True)
        self.btn_mcu_reset.setEnabled(True)
        self._finish_backend_reconnect()
        if not success:
            QMessageBox.critical(self, f"{self.config.backend_mode} 重连失败", error)

    def disconnect_backend(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录过程中不能断开通信后端。")
            return
        self.backend_suspended = True
        self.variable_write_worker.cancel_pending()
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
        self.variable_write_worker.update_jlink(None)
        if old_link:
            self._close_native_link_async(old_link)
        self.btn_disconnect.setEnabled(False)
        self.btn_reconnect.setEnabled(True)
        self.lbl_status_text.setText("通信后端已断开（未复位 MCU）")
        self.lbl_status_text.setStyleSheet("color: #f0a020; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold;")

    def return_to_configuration(self):
        if self.flash_sequence_active:
            QMessageBox.information(self, "正在烧录", "烧录尚未完成，请等待结束后再返回配置。")
            return
        if self.reconnect_worker and self.reconnect_worker.isRunning():
            QMessageBox.information(self, "正在重连", "通信后端正在重连，请等待完成后再返回配置。")
            return
        self._close_destination = "config"
        self.btn_back_config.setEnabled(False)
        self.lbl_status_text.setText("正在安全释放调试连接并返回配置（不会复位 MCU）…")
        self.close()

    def _close_native_link_async(self, link, next_action=""):
        closer = BackendCloseWorker(link, self.jlink_lock, self)
        self.backend_close_workers.append(closer)
        closer.finished_signal.connect(
            lambda error, worker=closer, action=next_action:
            self._native_link_closed(worker, error, action)
        )
        closer.finished.connect(closer.deleteLater)
        closer.start()

    def _native_link_closed(self, worker, error, next_action):
        if worker in self.backend_close_workers:
            self.backend_close_workers.remove(worker)
        if next_action == "reconnect":
            self._start_backend_reconnect()
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
            requests[read_key] = (read_key, absolute_address, size)
            pending[(t_idx, path)] = (item, read_key, type_info, v_type, cell_widget)

        for tab_index in sorted(active_tabs):
            tree = self.debug_trees.get(tab_index)
            if tree and tree.isVisible():
                for item_index in range(tree.topLevelItemCount()):
                    collect_item(tree.topLevelItem(item_index), tab_index)

        if not requests:
            return
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

    @staticmethod
    def _format_low_speed_value(data, type_info, v_type):
        if type_info is not None and scalar_size(type_info):
            value = MCUStudioWindow.decode_value(data, type_info)
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
        self.stop_sampling_thread(wait_ms=1200)
        if hasattr(self, "variable_write_worker"):
            self.variable_write_worker.stop(wait_ms=0)
        if hasattr(self, "history_writer"):
            self.history_writer.stop(wait_ms=1500)
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
        self.close()


def run_application() -> int:
    """Create the Qt application and run the login/main-window flow."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon_path = os.path.join(bundle_root, "assets", "mcu_studio_anime.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    pg.setConfigOption("foreground", "#e0e0e0")
    app.setStyleSheet(DARK_STYLE)
    db = LiveWatchDB()
    active = {"dialog": None, "window": None}

    def workspace_closed(destination):
        closed_window = active.get("window")
        active["window"] = None
        if closed_window is not None:
            QTimer.singleShot(0, closed_window.deleteLater)
        if destination == "config":
            QTimer.singleShot(0, show_configuration)
        else:
            QTimer.singleShot(0, app.quit)

    def show_configuration():
        dialog = LoginConfigDialog(db)
        active["dialog"] = dialog
        result = dialog.exec()
        active["dialog"] = None
        if result != QDialog.DialogCode.Accepted or not dialog.is_connected:
            QTimer.singleShot(0, app.quit)
            return
        window = MCUStudioWindow(dialog.config, db)
        active["window"] = window
        window.session_closed.connect(workspace_closed)
        window.show()

    QTimer.singleShot(0, show_configuration)
    return app.exec()


