"""Shared standard-library, Qt, core, worker and spectrum imports."""
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
                             QSpinBox, QInputDialog, QAbstractItemView)
from PyQt6.QtCore import (QTimer, Qt, QEvent, pyqtSignal, QVariantAnimation, QMutex,
                          QMutexLocker, QPointF, QRectF, QSize, QThread, QEventLoop,
                          QMimeData)
from PyQt6.QtGui import (QFont, QKeyEvent, QIntValidator, QDoubleValidator, QColor,
                         QAction, QPen, QIcon, QPainter, QPalette, QPainterPath,
                         QPixmap, QRegion, QDrag)

import pyqtgraph as pg

from .. import __version__

from ..core import (
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
from ..workers import (BackendCloseWorker, BackendReconnectWorker, DebuggerDiscoveryWorker,
                      ElfFlashWorker, ElfParseWorker,
                      HighSpeedSamplingWorker, LowSpeedReadWorker, OpenOcdFlashWorker,
                      SampleHistoryWriter, VariableWriteWorker)
from ..spectrum import (DEFAULT_FFT_SETTINGS, WINDOW_LABELS, analyze_spectrum,
                       normalize_fft_settings, spectrum_display_values)

pg.setConfigOptions(useOpenGL=False, antialias=True)

FIRMWARE_FILE_FILTER = "ELF / AXF 固件 (*.elf *.axf);;所有文件 (*)"

try:
    import pylink
except ImportError:
    pylink = None
