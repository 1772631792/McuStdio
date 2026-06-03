# live_set.py
import sys
import socket
import subprocess
import re
import time
import struct
import sqlite3
import os
import psutil
import bisect
import platform  
import numpy as np 

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
                             QPushButton, QComboBox, QLabel, QLineEdit, 
                             QHeaderView, QMenu, QMessageBox, QDialog, QFileDialog, 
                             QFormLayout, QCheckBox, QSplitter, QTextEdit, QRadioButton, QButtonGroup,
                             QTabWidget, QTabBar)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QVariantAnimation, QMutex, QMutexLocker
from PyQt6.QtGui import QFont, QKeyEvent, QIntValidator, QDoubleValidator, QColor, QAction, QPen

import pyqtgraph as pg

# 💡 彻底关闭不稳定的 OpenGL 硬件级加速，全局开启抗锯齿
# 经过实测，基于 NumPy 矢量切片的原生绘图在现代 CPU 上足以轻松吃满 8K 采样，且彻底免疫上下文死锁。
pg.setConfigOptions(useOpenGL=False, antialias=True)

from struct_parser import StructParser

try:
    import pylink
except ImportError:
    pylink = None

DARK_STYLE = """
    QWidget { background-color: #1a1a1a; color: #e0e0e0; font-family: "Microsoft YaHei", "Consolas"; font-size: 12px; }
    QDialog, QMainWindow { background-color: #121212; }
    QLineEdit, QComboBox, QCheckBox, QTextEdit { background-color: #242424; border: 1px solid #333333; padding: 5px; color: #ffffff; border-radius: 6px; }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border: 1px solid #1e88e5; background-color: #2a2a2a; }
    QPushButton { background-color: #2d2d2d; border: 1px solid #424242; padding: 5px 12px; color: #ffffff; border-radius: 6px; font-weight: 500; }
    QPushButton:hover { background-color: #3d3d3d; border: 1px solid #525252; }
    QPushButton:disabled { background-color: #1f1f1f; color: #444444; border: 1px solid #222222; }
    QLineEdit:disabled, QComboBox:disabled { background-color: #1a1a1a; color: #555555; border: 1px solid #222222; }
    QTreeWidget { background-color: #161616; border: 1px solid #2d2d2d; border-radius: 8px; alternate-background-color: #1c1c1c; }
    QTreeWidget::item { height: 26px; border-bottom: 1px solid #222222; }
    QTreeWidget::item:hover { background-color: #262626; }
    QTreeWidget::item:selected { background-color: #0d47a1; color: #ffffff; }
    QHeaderView::section { background-color: #222222; color: #90a4ae; padding: 6px; border: 1px solid #121212; font-weight: bold; }
    QSplitter::handle { background-color: #252525; }
    QTreeWidget QLineEdit { background-color: #2c2c2c !important; border: 2px solid #1e88e5 !important; color: #ffffff !important; font-family: "Consolas" !important; font-size: 12px !important; font-weight: bold !important; padding: 1px 3px; }
    
    QTabWidget::pane { border: 1px solid #2d2d2d; background-color: #161616; border-radius: 8px; top: -1px; }
    QTabBar::tab { background-color: #222222; color: #888888; border: 1px solid #2d2d2d; padding: 6px 15px; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: bold; margin-right: 2px; }
    QTabBar::tab:selected { background-color: #161616; color: #1e88e5; border-bottom-color: #161616; border-top: 2px solid #1e88e5; }
"""

POPUP_MENU_STYLE = """
    QMenu { background-color: #222222; color: #ffffff; border: 1px solid #444444; padding: 6px; font-family: "Microsoft YaHei", "Consolas"; font-size: 13px; font-weight: bold; }
    QMenu::item { padding: 6px 28px 6px 16px; border-radius: 4px; background-color: transparent; color: #ffffff; }
    QMenu::item:selected { background-color: #1e88e5; color: #ffffff; }
    QMenu::separator { height: 1px; background-color: #444444; margin: 4px 0px; }
"""

class ProjectConfig:
    def __init__(self):
        self.elf_path = ""
        self.nm_path = ""
        self.backend_mode = "JLINK" 
        self.openocd_exe = ""
        self.interface = ""  
        self.target = ""     
        self.jlink_dll_dir = ""        
        self.jlink_device = "Cortex-M0+" 
        self.svd_path = ""             
        self.openocd_host = "127.0.0.1"
        self.tcl_port = 6666
        self.show_log_window = False  

    def get_openocd_args(self):
        exe_dir = os.path.dirname(self.openocd_exe) if self.openocd_exe else ""
        scripts_path = os.path.abspath(os.path.join(exe_dir, "../share/openocd/scripts"))
        if not os.path.exists(scripts_path): scripts_path = os.path.abspath(os.path.join(exe_dir, "scripts"))
        
        args = ["-s", scripts_path, "-c", f"bindto {self.openocd_host}", "-c", f"tcl_port {self.tcl_port}", "-c", "gdb_port 3333"]
        if ":" in self.interface or "/" in self.interface or "\\" in self.interface:
            args += ["-f", self.interface]
        else:
            args += ["-f", f"interface/{self.interface}"]

        if ":" in self.target or "/" in self.target or "\\" in self.target:
            args += ["-f", self.target]
        else:
            args += ["-f", f"target/{self.target}"]
        return args

class LiveWatchDB:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.conn = sqlite3.connect(os.path.join(base_dir, "live_watch_history.db"))
        self.cursor = self.conn.cursor()
        try:
            self.cursor.execute("SELECT offset, base_var FROM variables LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("DROP TABLE IF EXISTS variables")

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS variables (
                name TEXT, type TEXT, offset INTEGER, base_var TEXT, tab_idx INTEGER DEFAULT 0,
                PRIMARY KEY (name, tab_idx)
            )''')
        self.cursor.execute('CREATE TABLE IF NOT EXISTS config_cache (key TEXT PRIMARY KEY, value TEXT)')
        self.cursor.execute('CREATE TABLE IF NOT EXISTS c_code_cache (id INTEGER PRIMARY KEY, code TEXT)')
        self.conn.commit()

    def save_var(self, name, var_type, offset=0, base_var="", tab_idx=0):
        self.cursor.execute("INSERT OR REPLACE INTO variables (name, type, offset, base_var, tab_idx) VALUES (?, ?, ?, ?, ?)", (name, var_type, offset, base_var, tab_idx))
        self.conn.commit()

    def delete_var(self, name, tab_idx=0):
        self.cursor.execute("DELETE FROM variables WHERE name=? AND tab_idx=?", (name, tab_idx))
        self.conn.commit()

    def get_all_vars(self):
        self.cursor.execute("SELECT name, type, offset, base_var, tab_idx FROM variables")
        return self.cursor.fetchall()

    def save_config(self, key, value):
        self.cursor.execute("INSERT OR REPLACE INTO config_cache (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def get_config(self, key, default=""):
        self.cursor.execute("SELECT value FROM config_cache WHERE key=?", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else default

    def save_c_code(self, code):
        self.cursor.execute("INSERT OR REPLACE INTO c_code_cache (id, code) VALUES (1, ?)", (code,))
        self.conn.commit()

    def get_c_code(self):
        self.cursor.execute("SELECT code FROM c_code_cache WHERE id=1")
        row = self.cursor.fetchone()
        return row[0] if row else ""

class LoginConfigDialog(QDialog):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.config = ProjectConfig()
        self.is_connected = False
        self.setWindowTitle("MCU Studio - 启动配置工作站")
        self.resize(680, 540)
        self.init_ui()
        self.load_saved_config()
        self.toggle_engine_fields()

        self.detect_timer = QTimer(self)
        self.detect_timer.timeout.connect(self.static_env_check)
        self.detect_timer.start(1000)

    def init_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()
        
        self.elf_edit = QLineEdit()
        btn_elf = QPushButton("...")
        btn_elf.clicked.connect(lambda: self.select_file(self.elf_edit, "选择 ELF 固件", "ELF Files (*.elf)"))
        h1 = QHBoxLayout(); h1.addWidget(self.elf_edit); h1.addWidget(btn_elf)
        form.addRow("🔄 固件编译文件 (.elf):", h1)

        self.nm_edit = QLineEdit()
        btn_nm = QPushButton("...")
        btn_nm.clicked.connect(lambda: self.select_file(self.nm_edit, "选择 nm.exe", "Executable (*.exe)"))
        h2 = QHBoxLayout(); h2.addWidget(self.nm_edit); h2.addWidget(btn_nm)
        form.addRow("🔨 NM 工具路径 (nm.exe):", h2)

        form.addRow(QLabel("<hr/><b>🛠️ 调试器驱动内核主选择：</b>"))

        self.driver_mode_combo = QComboBox()
        self.driver_mode_combo.addItems(["J-Link 原生内核驱动 (无需OpenOCD/无需cfg)", "CMSIS-DAP / ST-Link (基于 OpenOCD)"])
        self.driver_mode_combo.currentIndexChanged.connect(self.toggle_engine_fields)
        form.addRow("🔌 调试驱动架构派系:", self.driver_mode_combo)

        self.lbl_jl_dir = QLabel("📂 J-Link 驱动安装根路径:")
        self.jlink_dir_edit = QLineEdit()
        self.btn_jl_dir = QPushButton("...")
        self.btn_jl_dir.clicked.connect(self.select_jlink_dir)
        self.h_jl_dir = QHBoxLayout(); self.h_jl_dir.addWidget(self.jlink_dir_edit); self.h_jl_dir.addWidget(self.btn_jl_dir)

        self.lbl_jl_dev = QLabel("🎯 芯片特定型号 (Device):")
        self.jlink_device_edit = QLineEdit("Cortex-M0+")

        self.lbl_jl_svd = QLabel("📋 寄存器映射描述 (.svd):")
        self.svd_edit = QLineEdit()
        self.btn_svd = QPushButton("...")
        self.btn_svd.clicked.connect(lambda: self.select_file(self.svd_edit, "选择外设 SVD 描述文件", "SVD Files (*.svd *.txt)"))
        self.h_jl_svd = QHBoxLayout(); self.h_jl_svd.addWidget(self.svd_edit); self.h_jl_svd.addWidget(self.btn_svd)

        form.addRow(self.lbl_jl_dir, self.h_jl_dir)
        form.addRow(self.lbl_jl_dev, self.jlink_device_edit)
        form.addRow(self.lbl_jl_svd, self.h_jl_svd)

        self.lbl_ocd_exe = QLabel("🚀 OpenOCD 路径 (openocd.exe):")
        self.openocd_edit = QLineEdit()
        self.btn_ocd = QPushButton("...")
        self.btn_ocd.clicked.connect(lambda: self.select_file(self.openocd_edit, "选择 openocd.exe", "Executable (*.exe)"))
        self.h_ocd_exe = QHBoxLayout(); self.h_ocd_exe.addWidget(self.openocd_edit); self.h_ocd_exe.addWidget(self.btn_ocd)

        self.lbl_ocd_intf = QLabel("🔌 硬件调试器种类 (.cfg):")
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        self.interface_combo.addItems(["cmsis-dap.cfg", "stlink.cfg"])
        self.btn_browse_intf = QPushButton("...")
        self.btn_browse_intf.clicked.connect(lambda: self.select_cfg_file(self.interface_combo, "interface"))
        self.h_ocd_intf = QHBoxLayout(); self.h_ocd_intf.addWidget(self.interface_combo); self.h_ocd_intf.addWidget(self.btn_browse_intf)

        self.lbl_ocd_targ = QLabel("🎯 目标芯片内核型号 (.cfg):")
        self.target_combo = QComboBox()
        self.target_combo.setEditable(True)
        self.target_combo.addItems(["stm32f1x.cfg", "stm32f4x.cfg", "py32f0xx.cfg", "stm32f0x.cfg"])
        self.btn_browse_target = QPushButton("...")
        self.btn_browse_target.clicked.connect(lambda: self.select_cfg_file(self.target_combo, "target"))
        self.h_ocd_targ = QHBoxLayout(); self.h_ocd_targ.addWidget(self.target_combo); self.h_ocd_targ.addWidget(self.btn_browse_target)

        form.addRow(self.lbl_ocd_exe, self.h_ocd_exe)
        form.addRow(self.lbl_ocd_intf, self.h_ocd_intf)
        form.addRow(self.lbl_ocd_targ, self.h_ocd_targ)

        self.log_checkbox = QCheckBox("显示硬件调试日志 to 独立黑框 (仅限OpenOCD)")
        form.addRow("📋 调试黑框开关:", self.log_checkbox)

        layout.addLayout(form)
        layout.addSpacing(10)
        
        self.lbl_hardware_status = QLabel("🔍 准备就绪。")
        self.lbl_hardware_status.setStyleSheet("color: #ffb300; font-weight: bold; padding: 5px;")
        layout.addWidget(self.lbl_hardware_status)
        
        btns = QHBoxLayout()
        self.btn_connect = QPushButton("⚡ 初始化硬件并进入工作站")
        self.btn_connect.setStyleSheet("background-color: #1e88e5; font-weight: bold; padding: 10px 24px; color: white; font-size: 13px;")
        self.btn_connect.clicked.connect(self.handle_connect)
        btns.addStretch()
        btns.addWidget(self.btn_connect)
        layout.addLayout(btns)
        self.setLayout(layout)

    def toggle_engine_fields(self):
        is_jlink = (self.driver_mode_combo.currentIndex() == 0)
        
        self.jlink_dir_edit.setEnabled(is_jlink)
        self.btn_jl_dir.setEnabled(is_jlink)
        self.jlink_device_edit.setEnabled(is_jlink)
        self.svd_edit.setEnabled(is_jlink)
        self.btn_svd.setEnabled(is_jlink)

        self.openocd_edit.setEnabled(not is_jlink)
        self.btn_ocd.setEnabled(not is_jlink)
        self.interface_combo.setEnabled(not is_jlink)
        self.btn_browse_intf.setEnabled(not is_jlink)
        self.target_combo.setEnabled(not is_jlink)
        self.btn_browse_target.setEnabled(not is_jlink)
        self.log_checkbox.setEnabled(not is_jlink)
        
        self.static_env_check()

    def static_env_check(self):
        is_jlink_mode = (self.driver_mode_combo.currentIndex() == 0)
        if is_jlink_mode:
            if pylink is None:
                self.lbl_hardware_status.setText("❌ 未检测到 pylink 库，请执行 pip install pylink-square")
                self.lbl_hardware_status.setStyleSheet("color: #e53935; font-weight: bold;")
            else:
                self.lbl_hardware_status.setText("🟢 J-Link 原生运行环境就绪。连接和退出时绝不复位 MCU。")
                self.lbl_hardware_status.setStyleSheet("color: #00E676; font-weight: bold;")
        else:
            exe = self.openocd_edit.text().strip()
            if exe and os.path.exists(exe):
                self.lbl_hardware_status.setText("🟡 OpenOCD 工具路径就绪。通信管道将独立平稳工作。")
                self.lbl_hardware_status.setStyleSheet("color: #00E5FF; font-weight: bold;")
            else:
                self.lbl_hardware_status.setText("⚠️ OpenOCD 路径待完善，可直接进入面板。")
                self.lbl_hardware_status.setStyleSheet("color: #ffb300; font-weight: bold;")

    def select_file(self, edit, title, flt):
        path, _ = QFileDialog.getOpenFileName(self, title, "", flt)
        if path: edit.setText(os.path.normpath(path))

    def select_jlink_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 SEGGER J-Link 安装根目录")
        if path: 
            self.jlink_dir_edit.setText(os.path.normpath(path))
            self.static_env_check()

    def select_cfg_file(self, combo, subfolder):
        exe_path = self.openocd_edit.text().strip()
        initial_dir = ""
        if exe_path and os.path.exists(exe_path):
            scripts = os.path.abspath(os.path.join(os.path.dirname(exe_path), f"../share/openocd/scripts/{subfolder}"))
            if os.path.exists(scripts): initial_dir = scripts
        path, _ = QFileDialog.getOpenFileName(self, f"选择自定义 {subfolder} 配置文件", initial_dir, "Config Files (*.cfg)")
        if path: combo.setEditText(os.path.normpath(path))

    def load_saved_config(self):
        self.elf_edit.setText(self.db.get_config("elf_path"))
        self.nm_edit.setText(self.db.get_config("nm_path"))
        self.openocd_edit.setText(self.db.get_config("openocd_exe"))
        self.jlink_dir_edit.setText(self.db.get_config("jlink_dll_dir", "C:\\Program Files\\SEGGER\\JLink"))
        self.jlink_device_edit.setText(self.db.get_config("jlink_device", "Cortex-M0+"))
        self.svd_edit.setText(self.db.get_config("svd_path"))
        
        saved_mode = self.db.get_config("backend_mode", "JLINK")
        self.driver_mode_combo.setCurrentIndex(0 if saved_mode == "JLINK" else 1)
        
        saved_intf = self.db.get_config("last_interface_cfg")
        if saved_intf: self.interface_combo.setEditText(saved_intf)
        saved_targ = self.db.get_config("last_target_cfg")
        if saved_targ: self.target_combo.setEditText(saved_targ)

    def handle_connect(self):
        if hasattr(self, 'detect_timer'): self.detect_timer.stop()
        self.config.elf_path = self.elf_edit.text().strip()
        self.config.nm_path = self.nm_edit.text().strip()
        self.config.backend_mode = "JLINK" if self.driver_mode_combo.currentIndex() == 0 else "OPENOCD"
        
        self.db.save_config("elf_path", self.config.elf_path)
        self.db.save_config("nm_path", self.config.nm_path)
        self.db.save_config("backend_mode", self.config.backend_mode)

        if self.config.backend_mode == "OPENOCD":
            self.config.openocd_exe = self.openocd_edit.text().strip()
            self.config.interface = self.interface_combo.currentText().strip()
            self.config.target = self.target_combo.currentText().strip()
            self.config.show_log_window = self.log_checkbox.isChecked()

            self.db.save_config("openocd_exe", self.config.openocd_exe)
            self.db.save_config("last_interface_cfg", self.config.interface)
            self.db.save_config("last_target_cfg", self.config.target)

            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and 'openocd' in proc.info['name'].lower():
                    try: proc.kill()
                    except: pass
            time.sleep(0.15)
            
            if self.config.openocd_exe and os.path.exists(self.config.openocd_exe):
                cmd = [self.config.openocd_exe] + self.config.get_openocd_args()
                si = subprocess.STARTUPINFO()
                if not self.config.show_log_window:
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = subprocess.SW_HIDE
                subprocess.Popen(cmd, startupinfo=si, creationflags=subprocess.CREATE_NEW_CONSOLE)
                time.sleep(0.5)
            
        else: 
            self.config.jlink_dll_dir = self.jlink_dir_edit.text().strip()
            self.config.jlink_device = self.jlink_device_edit.text().strip()
            self.config.svd_path = self.svd_edit.text().strip()

            self.db.save_config("jlink_dll_dir", self.config.jlink_dll_dir)
            self.db.save_config("jlink_device", self.config.jlink_device)
            self.db.save_config("svd_path", self.config.svd_path)

        self.is_connected = True
        self.accept()


class InPlaceEditLineEdit(QLineEdit):
    editing_finished_signal = pyqtSignal(str)

    def __init__(self, parent, initial_text, val_type):
        super().__init__(parent)
        self.setText(initial_text)
        if "float" in val_type or "double" in val_type:
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
        btn_run = QPushButton("🔍 智能树化解析并同步至全局主表")
        btn_run.setStyleSheet("background-color: #1e88e5; font-weight: bold; padding: 6px 16px;")
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
        self.setWindowTitle(f"MCU Live Watch Studio - [{'原生J-Link独占' if config.backend_mode=='JLINK' else 'OpenOCD驱动'}]")
        self.resize(1450, 850)
        
        self.parser = StructParser()
        self.symbol_cache = {}
        self.worker = None 
        self.jlink_native_instance = None 
        self.openocd_link_alive = False  
        
        self.ch_paths = [None, None, None]     
        self.ch_types = [None, None, None]     
        self.ch_addrs = [None, None, None]     
        self.ch_buffers = [[], [], []]         
        self.ch_time_stamps = [[], [], []]     
        
        # 数据流级高频物理互斥锁
        self.buffer_mutex = QMutex()
        
        # 布局切换旗标响应
        self.layout_switching = False
        self.current_layout_id = 1
        
        self.plot_active_paths = [[], [], []] 
        self.plot_curves_dict = [{}, {}, {}] 
        
        self.start_time = time.time()          
        self.max_display_duration_ms = 5000    
        self.is_frozen = False
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

    def init_global_link_backend(self):
        if self.config.backend_mode == "OPENOCD":
            try:
                self.tcl_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcl_socket.settimeout(0.3)
                self.tcl_socket.connect((self.config.openocd_host, self.config.tcl_port))
                self.openocd_link_alive = True
                print("⚡ 主控 TCL 管道建立成功。")
            except Exception as e:
                self.openocd_link_alive = False
                self.tcl_socket = None
                print(f"⚠️ 管道初始连接未就绪: {e}")
        else:
            try:
                if pylink is None: return
                is_64bit_python = "64" in platform.architecture()[0]
                dll_name = "JLink_x64.dll" if is_64bit_python else "JLinkARM.dll"
                dll_file = os.path.join(self.config.jlink_dll_dir, dll_name)
                
                if not os.path.exists(dll_file):
                    fallback_name = "JLinkARM.dll" if is_64bit_python else "JLink_x64.dll"
                    dll_file = os.path.join(self.config.jlink_dll_dir, fallback_name)
                
                if os.path.exists(dll_file):
                    os.environ['PATH'] = self.config.jlink_dll_dir + os.pathsep + os.environ.get('PATH', '')
                    self.jlink_native_instance = pylink.JLink(lib=pylink.library.Library(dllpath=dll_file))
                else:
                    self.jlink_native_instance = pylink.JLink()
                    
                self.jlink_native_instance.open()
                self.jlink_native_instance.set_tif(pylink.enums.JLinkInterfaces.SWD)
                self.jlink_native_instance.connect(self.config.jlink_device, speed=4000)
                print("⚡ 原生 J-Link 内核握手成功！")
            except Exception as e:
                self.jlink_native_instance = None
                print(f"❌ 原生 J-Link 内核打开失败: {e}")

    def load_elf_symbols(self):  
        try:
            if not self.config.nm_path or not self.config.elf_path: return
            if not os.path.exists(self.config.nm_path) or not os.path.exists(self.config.elf_path): return
            res = subprocess.run([self.config.nm_path, "-C", self.config.elf_path], stdout=subprocess.PIPE, text=True, check=True)
            matches = re.findall(r"^([0-9a-fA-F]+)\s+[BbDdGgSs]\s+([a-zA-Z_][a-zA-Z0-9_]*)$", res.stdout, re.MULTILINE)
            self.symbol_cache = {name: int(addr, 16) for addr, name in matches}
            print(f"⚡ ELF 符号表加载成功，共包含 {len(self.symbol_cache)} 个有效全局变量。")
        except Exception as e:
            print(f"❌ ELF 符号表加载异常: {e}")

    def send_write_command(self, addr_hex, val_int, width_bytes, is_float=False):
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
                if self.jlink_native_instance and self.jlink_native_instance.target_connected():
                    if width_bytes == 1: self.jlink_native_instance.memory_write8(addr, [val_int & 0xFF])
                    elif width_bytes == 2: self.jlink_native_instance.memory_write16(addr, [val_int & 0xFFFF])
                    else: self.jlink_native_instance.memory_write32(addr, [val_int & 0xFFFFFFFF])
        except Exception:
            pass

    def send_tcl_cmd(self, cmd):
        if not self.tcl_socket or not self.openocd_link_alive: return "ERROR"
        try:
            self.tcl_socket.setblocking(False)
            try:
                while self.tcl_socket.recv(128): pass
            except: pass
            self.tcl_socket.setblocking(True)
            self.tcl_socket.sendall(f"{cmd}\x1a".encode('utf-8'))
            res = self.tcl_socket.recv(1024).decode('utf-8').replace('\x1a', '').strip()
            if "ERROR" in res: raise Exception()
            return res
        except: 
            self.openocd_link_alive = False 
            return "ERROR"

    def get_current_tree(self):
        return self.tab_widget.currentWidget()

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
        
        top = QHBoxLayout()
        self.wave_hz_combo = QComboBox()
        self.wave_hz_combo.addItems([
            "Custom (自定义频率)", "500 Hz 均衡流", "1000 Hz 高速流", 
            "2000 Hz 极限级🚀", "4000 Hz 超极限⚡", "8000 Hz 毁灭级🔥"
        ])
        self.wave_hz_combo.currentIndexChanged.connect(self.restart_sampling_thread)
        
        self.custom_hz_edit = QLineEdit("30")
        self.custom_hz_edit.setFixedWidth(55)
        self.custom_hz_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.custom_hz_edit.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.custom_hz_edit.setValidator(QIntValidator(1, 8000, self))
        self.custom_hz_edit.editingFinished.connect(self.restart_sampling_thread)
        
        self.btn_freeze = QPushButton("❄️ 锁定时间滚动")
        self.btn_freeze.clicked.connect(self.toggle_freeze)
        
        self.btn_reconnect = QPushButton("🔌 重启通信后端")
        self.btn_reconnect.clicked.connect(self.force_reconnect_engine)
        
        self.btn_mcu_reset = QPushButton("♻️ 强制复位")
        self.btn_mcu_reset.setStyleSheet("background-color: #4a1515; color: #ff8a80;")
        self.btn_mcu_reset.clicked.connect(self.trigger_manual_mcu_reset)

        top.addWidget(QLabel("📊 采样速率:"))
        top.addWidget(self.wave_hz_combo)
        top.addWidget(self.custom_hz_edit)
        top.addWidget(self.btn_freeze)
        
        # 💡 修复：将之前挤在一行的多条布局打包语句拆分换行，修复 Python 语法错误
        top.addSpacing(10) 
        top.addWidget(self.btn_reconnect) 
        top.addWidget(self.btn_mcu_reset) 
        top.addStretch()
        
        top.addWidget(QLabel("<b>🖼️ 画布同屏分布:</b>"))
        self.layout_group = QButtonGroup(self)
        self.rad_map1 = QRadioButton("单图 (1行独占)")
        self.rad_map2 = QRadioButton("双图 (2行上下均匀)")
        self.rad_map3 = QRadioButton("三图 (3行上下均匀)")
        self.layout_group.addButton(self.rad_map1, 1)
        self.layout_group.addButton(self.rad_map2, 2)
        self.layout_group.addButton(self.rad_map3, 3)
        self.rad_map1.setChecked(True)
        top.addWidget(self.rad_map1)
        top.addWidget(self.rad_map2)
        top.addWidget(self.rad_map3)
        self.layout_group.idClicked.connect(self.handle_layout_changed)
        
        layout.addLayout(top)

        quick_input_bar = QHBoxLayout()
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("全局变量名或十六进制物理地址(如0x40012440)")
        self.input_name.setFixedWidth(280)
        
        self.input_type = QComboBox()
        self.input_type.addItems(["uint8", "int8", "uint16", "int16", "uint32", "int32", "int", "float"])
        self.input_type.setFixedWidth(90)
        self.input_type.currentIndexChanged.connect(lambda idx: self.db.save_config("last_input_type_idx", str(idx)))
        
        btn_add = QPushButton("➕ 快捷添加监视")
        btn_add.clicked.connect(self.quick_add_variable)
        
        btn_adv_c = QPushButton("🚀 C 源码嵌套提炼结构体面板")
        btn_adv_c.setStyleSheet("background-color: #0d47a1; color: white; font-weight: bold;")
        btn_adv_c.clicked.connect(self.open_advance_parser_window)

        quick_input_bar.addWidget(QLabel("<b>⚡ 快捷输入栏:</b>"))
        quick_input_bar.addWidget(self.input_name)
        quick_input_bar.addWidget(self.input_type)
        quick_input_bar.addWidget(btn_add)
        quick_input_bar.addSpacing(20)
        quick_input_bar.addWidget(btn_adv_c)
        quick_input_bar.addStretch()
        layout.addLayout(quick_input_bar)
        
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.handle_tab_close_requested)
        self.tab_widget.currentChanged.connect(lambda: self.restart_sampling_thread())
        
        self.btn_add_tab = QPushButton("➕")
        self.btn_add_tab.setFixedWidth(30)
        self.btn_add_tab.setToolTip("创建新变量调试窗口 (最多2个)")
        self.btn_add_tab.clicked.connect(self.create_new_debug_tab)
        self.tab_widget.setCornerWidget(self.btn_add_tab, Qt.Corner.TopRightCorner)
        
        self.create_new_debug_tab("主调试窗口")
        
        self.main_splitter.addWidget(self.tab_widget)
        
        self.plot_splitter = QSplitter(Qt.Orientation.Vertical)
        self.plot_splitter.setStyleSheet("QSplitter::handle { height: 4px; background-color: #2a2a2a; }")
        
        self.plot_widgets = []
        self.ch_colors = ['#00E676', '#FFD600', '#00E5FF'] 

        self.v_lines = []
        self.h_lines = []
        self.hover_dots = []
        self.hover_labels = []

        # 初始化固定的 3 个画布静态骨架
        for i in range(3):
            pw = pg.PlotWidget()
            pw.setBackground('#111111')
            pw.showGrid(x=True, y=True, alpha=0.2)
            pw.setLabel('bottom', '时间滚动追踪轴', units='ms')
            pw.getViewBox().setDefaultPadding(0.0)
            
            pw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            pw.customContextMenuRequested.connect(lambda pos, idx=i: self.show_plot_context_menu(pos, idx))

            v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#90a4ae', width=1, style=Qt.PenStyle.DashLine))
            h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#90a4ae', width=1, style=Qt.PenStyle.DashLine))
            v_line.setVisible(False)
            h_line.setVisible(False)
            pw.addItem(v_line)
            pw.addItem(h_line)
            self.v_lines.append(v_line)
            self.h_lines.append(h_line)

            hover_dot = pg.ScatterPlotItem(size=10, pen=pg.mkPen('#ffffff', width=2))
            hover_dot.setVisible(False)
            pw.addItem(hover_dot)
            self.hover_dots.append(hover_dot)

            hover_label = pg.TextItem(anchor=(0, 1), color='#ffffff', fill='#263238')
            hover_label.setVisible(False)
            pw.addItem(hover_label)
            self.hover_labels.append(hover_label)

            pw.scene().sigMouseMoved.connect(lambda pos, idx=i: self.handle_plot_mouse_move(pos, idx))

            self.plot_widgets.append(pw)
            self.plot_splitter.addWidget(pw)
            
        self.main_splitter.addWidget(self.plot_splitter)
        self.main_splitter.setSizes([450, 1000])
        layout.addWidget(self.main_splitter)
        
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(5, 4, 5, 4)
        
        self.lbl_ch1 = QLabel("CH1: 📭 空闲")
        self.lbl_ch2 = QLabel("CH2: 📭 空闲")
        self.lbl_ch3 = QLabel("CH3: 📭 空闲")
        
        ch_style = "font-family: 'Consolas'; font-size: 11px; font-weight: bold; padding: 2px 10px; border-radius: 4px; background-color: #161616; border: 1px solid #252525;"
        self.lbl_ch1.setStyleSheet(f"{ch_style} color: #00E676;")
        self.lbl_ch2.setStyleSheet(f"{ch_style} color: #FFD600;")
        self.lbl_ch3.setStyleSheet(f"{ch_style} color: #00E5FF;")
        
        bottom_bar.addWidget(self.lbl_ch1)
        bottom_bar.addWidget(self.lbl_ch2)
        bottom_bar.addWidget(self.lbl_ch3)
        bottom_bar.addStretch()
        
        self.lbl_led = QLabel()
        self.lbl_led.setFixedSize(10, 10)
        self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
        self.lbl_status_text = QLabel("正在连接...")
        self.lbl_status_text.setStyleSheet("color: #757575; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")
        
        bottom_bar.addWidget(self.lbl_led)
        bottom_bar.addWidget(self.lbl_status_text)
        
        bottom_wrapper = QWidget()
        bottom_wrapper.setFixedHeight(28) 
        bottom_wrapper.setLayout(bottom_bar)
        layout.addWidget(bottom_wrapper)
        
        main_widget.setLayout(layout)
        self.centralWidget = main_widget
        self. CentralWidget = main_widget
        self.setCentralWidget(main_widget)

    def create_new_debug_tab(self, name=None):
        if self.tab_widget.count() >= 2:
            QMessageBox.warning(self, "标签页上限", "为了保障运行渲染流畅度，最多只允许开启 2 个变量调试窗口。")
            return
            
        if not name:
            name = f"窗口 {self.tab_widget.count() + 1}"
            
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["全局变量路径节点 (树状结构)", "底层数据类型", "实时物理数值 (5Hz后台)"])
        tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self.show_context_menu)
        tree.itemDoubleClicked.connect(self.handle_tree_item_double_clicked)
        
        new_idx = self.tab_widget.addTab(tree, name)
        self.tab_widget.setCurrentIndex(new_idx)
        
        if self.tab_widget.count() >= 2:
            self.btn_add_tab.setEnabled(False)

    def handle_tab_close_requested(self, index):
        if self.tab_widget.count() <= 1:
            QMessageBox.warning(self, "禁止关闭", "至少需要保留一个正在执行的变量调试主窗口。")
            return
            
        tree = self.tab_widget.widget(index)
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            root_name = item.text(0)
            for path, _, _, _, t_idx in self.db.get_all_vars():
                if t_idx == index and (path == root_name or path.startswith(root_name + ".") or path.startswith(root_name + "[")):
                    self.db.delete_var(path, tab_idx=t_idx)
                    
        for i in range(3):
            self.unbind_channel(i)
            
        self.tab_widget.removeTab(index)
        self.btn_add_tab.setEnabled(True)
        self.restart_sampling_thread()

    def toggle_freeze(self):
        self.is_frozen = not self.is_frozen
        if self.is_frozen:
            self.btn_freeze.setText("▶️ 恢复滚动追踪")
            self.btn_freeze.setStyleSheet("background-color: #e65100;")
        else:
            self.btn_freeze.setText("❄️ 锁定时间滚动")
            self.btn_freeze.setStyleSheet("background-color: #2d2d2d;")
            for i in range(3):
                if self.v_lines[i]: self.v_lines[i].setVisible(False)
                if self.h_lines[i]: self.h_lines[i].setVisible(False)
                if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                if self.hover_labels[i]: self.hover_labels[i].setVisible(False)

    def trigger_manual_mcu_reset(self):
        # 1. 触发按钮闪烁动画
        self._execute_flash_animation(self.btn_mcu_reset, "#e53935", "color: #ff8a80;")
        
        # 2. 执行底层硬件复位信号下发
        if self.config.backend_mode == "OPENOCD":
            if self.openocd_link_alive and self.tcl_socket:
                try:
                    self.tcl_socket.setblocking(True)
                    self.tcl_socket.sendall(b"reset run\x1a")
                    try: self.tcl_socket.recv(512)
                    except: pass
                    print("♻️ MCU 硬件强制复位信号已下发...")
                except:
                    pass
        else:
            if self.jlink_native_instance and self.jlink_native_instance.target_connected(): 
                try: 
                    self.jlink_native_instance.reset()
                    print("♻️ J-Link 原生内核复位信号已下发...")
                except: 
                    pass

        # 3. 核心改进：复位完紧接着自动调用现有的“重启通信后端”逻辑
        # 这样既能实现一键复位+重连，同时原有的“重启通信”按钮依然独立有效
        print("⚡ 正在自动重开通信管道...")
        self.force_reconnect_engine()

    def force_reconnect_engine(self):
        self._execute_flash_animation(self.btn_reconnect, "#00b0ff", "")
        if self.config.backend_mode == "OPENOCD":
            if self.tcl_socket: 
                try: self.tcl_socket.close()
                except: pass
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and 'openocd' in proc.info['name'].lower(): 
                    try: proc.kill()
                    except: pass
            time.sleep(0.2)
            cmd = [self.config.openocd_exe] + self.config.get_openocd_args()
            subprocess.Popen(cmd, startupinfo=subprocess.STARTINFO(), creationflags=subprocess.CREATE_NEW_CONSOLE)
            time.sleep(0.6)
        
        self.init_global_link_backend()
        self.restart_sampling_thread()

    def _execute_flash_animation(self, button, target_color, base_style):
        anim = QVariantAnimation(button)
        anim.setDuration(220) 
        anim.setStartValue(QColor(target_color))
        anim.setEndValue(QColor("#2d2d2d")) 
        anim.valueChanged.connect(lambda val: button.setStyleSheet(f"{base_style} background-color: {val.name()};"))
        anim.start()

    def handle_layout_changed(self, layout_id):
        self.layout_switching = True
        self.current_layout_id = layout_id
        self.db.save_config("last_layout_id", str(layout_id))
        
        # 💡 核心强化清理防御圈
        with QMutexLocker(self.buffer_mutex):
            for i in range(3):
                should_visible = (i == 0) or (i == 1 and layout_id >= 2) or (i == 2 and layout_id == 3)
                pw = self.plot_widgets[i]
                
                if should_visible:
                    pw.setVisible(True)
                else:
                    pw.setVisible(False)
                    # 💡 修正：只针对单路具体的画布清除，防止解绑不存在的信号引发 TypeError 闪退
                    try:
                        pw.getPlotItem().clear()
                    except:
                        pass
                    self.plot_curves_dict[i].clear()
                    if self.v_lines[i]: self.v_lines[i].setVisible(False)
                    if self.h_lines[i]: self.h_lines[i].setVisible(False)
                    if self.hover_dots[i]: self.hover_dots[i].setVisible(False)
                    if self.hover_labels[i]: self.hover_labels[i].setVisible(False)
        
        container_height = self.plot_splitter.height() or 600
        if layout_id == 1:
            self.plot_splitter.setSizes([container_height, 0, 0])
        elif layout_id == 2:
            h = container_height // 2
            self.plot_splitter.setSizes([h, h, 0])
        elif layout_id == 3:
            h = container_height // 3
            self.plot_splitter.setSizes([h, h, h])

        QTimer.singleShot(100, self._reset_layout_switching_flag)

    def _reset_layout_switching_flag(self):
        self.layout_switching = False

    def restore_layout_and_type_configs(self):
        try:
            layout_id = int(self.db.get_config("last_layout_id", "1"))
            if layout_id == 1: self.rad_map1.setChecked(True)
            elif layout_id == 2: self.rad_map2.setChecked(True)
            elif layout_id == 3: self.rad_map3.setChecked(True)
            self.handle_layout_changed(layout_id)
            
            type_idx = int(self.db.get_config("last_input_type_idx", "7"))
            if type_idx < self.input_type.count():
                self.input_type.setCurrentIndex(type_idx)
        except:
            pass

    def load_history_from_db(self):
        code = self.db.get_c_code()
        if code:
            self.parser.parse_structs(code)
            self.parser.parse_variables(code)
            
        db_vars = self.db.get_all_vars()
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            
            while self.tab_widget.count() <= t_idx:
                self.create_new_debug_tab()
                
            if '.' not in path and '[' not in path:
                self.inject_var_to_tree_ui(path, v_type, t_idx)
                
        for row in db_vars:
            path, v_type, offset, base_var = row[0], row[1], row[2], row[3]
            t_idx = row[4] if len(row) > 4 else 0
            if '.' in path or '[' in path:
                self.inject_var_to_tree_ui(path, v_type, t_idx)

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
        if self.is_frozen or self.layout_switching: 
            return
            
        layout_id = self.current_layout_id
        locker = QMutexLocker(self.buffer_mutex)
        
        for i in range(3):
            # 💡 无论画布当前是否隐藏，都让对应分配变量的时间轴向前推移，防止主时序卡死
            active_paths = self.plot_active_paths[i]
            curves_map = self.plot_curves_dict[i]
            
            ch_idx = -1
            for path in active_paths:
                for c_i in range(3):
                    if self.ch_paths[c_i] == path:
                        ch_idx = c_i
                        break
                if ch_idx != -1: break

            pw = self.plot_widgets[i]
            # 如果对应的画布已经处于不显示布局状态，直接略过实际的线条绘制
            if i >= 1 and layout_id < 2: continue
            if i == 2 and layout_id < 3: continue
            
            if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20:
                continue
                
            try:  
                if not pw.getPlotItem().scene(): 
                    continue

                if ch_idx != -1 and self.ch_time_stamps[ch_idx]:
                    max_x = self.ch_time_stamps[ch_idx][-1]  
                    min_x = max_x - self.max_display_duration_ms
                    pw.setXRange(min_x, max_x, padding=0)
                else:
                    now_rel_time = (time.time() - self.start_time) * 1000.0
                    min_x = now_rel_time - self.max_display_duration_ms
                    pw.setXRange(min_x, now_rel_time, padding=0)
                
                curr_keys = list(curves_map.keys())
                for pk in curr_keys:
                    if pk not in active_paths:
                        if pk in curves_map:
                            pw.removeItem(curves_map[pk])
                            del curves_map[pk]
                
                for path in active_paths:
                    target_ch = -1
                    for c_i in range(3):
                        if self.ch_paths[c_i] == path:
                            target_ch = c_i
                            break
                            
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
            except:
                pass 

    def handle_plot_mouse_move(self, pos, plot_idx):
        if not self.is_frozen or self.layout_switching: return

        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible() or pw.width() <= 20 or pw.height() <= 20:
            return

        active_paths = self.plot_active_paths[plot_idx]
        
        target_ch = -1
        for path in active_paths:
            for c_i in range(3):
                if self.ch_paths[c_i] == path:
                    target_ch = c_i
                    break
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
        except:
            pass

    def show_plot_context_menu(self, pos, plot_idx):
        if self.layout_switching: return
        available_paths = [p for p in self.ch_paths if p is not None]
        pw = self.plot_widgets[plot_idx]
        if pw.isHidden() or not pw.isVisible(): return
        
        try:
            menu = pw.getPlotItem().getContextMenus(None)
            menu.setStyleSheet(POPUP_MENU_STYLE)
            
            y_val_menu = None
            for act in menu.actions():
                if act.menu() and "Y_Val" in act.menu().title():
                    y_val_menu = act.menu()
                    y_val_menu.clear()
                    break
                    
            if not y_val_menu:
                y_val_menu = QMenu("📈 Y_Val 数据源定义", menu)
                menu.insertMenu(menu.actions()[0] if menu.actions() else None, y_val_menu)
                
            y_val_menu.setStyleSheet(POPUP_MENU_STYLE)
            
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
        except:
            pass

    def commit_vofa_layer_selection(self, path, plot_idx, current_state):
        if not current_state:
            if path not in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].append(path)
        else:
            if path in self.plot_active_paths[plot_idx]: self.plot_active_paths[plot_idx].remove(path)
        self.restart_sampling_thread()

    def show_context_menu(self, pos):
        tree = self.get_current_tree()
        if not tree: return
        item = tree.itemAt(pos)
        if not item: return
        if "{结构体/数组}" in item.text(2): return 
            
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path: path = item.text(0)
        
        menu = QMenu()
        menu.setStyleSheet(POPUP_MENU_STYLE)
        
        bound_ch_idx = -1
        for i in range(3):
            if self.ch_paths[i] == path: bound_ch_idx = i; break

        if bound_ch_idx != -1:
            m_unbind = menu.addAction(f"❌ 取消该变量在 通道 {bound_ch_idx+1} 的波形监视")
        else:
            m1 = menu.addAction("🟢 绑定至 示波器通道 1")
            m2 = menu.addAction("🟡 绑定至 示波器通道 2")
            m3 = menu.addAction("🔵 绑定至 示波器通道 3")
            
        menu.addSeparator()
        m_del = menu.addAction("🗑️ 从监视列表中删除该整组变量 (Delete)")
        act = menu.exec(tree.viewport().mapToGlobal(pos))
        
        if 'm_unbind' in locals() and act == m_unbind: self.unbind_channel(bound_ch_idx)
        elif 'm1' in locals() and act == m1: self.bind_tree_item_to_channel(0, item)
        elif 'm2' in locals() and act == m2: self.bind_tree_item_to_channel(1, item)
        elif 'm3' in locals() and act == m3: self.bind_tree_item_to_channel(2, item)
        elif act == m_del:
            root_item = item
            while root_item.parent(): root_item = root_item.parent()
            self.remove_variable_root(root_item)

    def bind_tree_item_to_channel(self, ch_idx, item):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path: path = item.text(0)
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
        
        # 加锁改写通道
        locker = QMutexLocker(self.buffer_mutex)
        self.ch_paths[ch_idx] = path
        self.ch_types[ch_idx] = v_type
        self.ch_addrs[ch_idx] = hex(absolute_address)
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        
        if path not in self.plot_active_paths[ch_idx]:
            self.plot_active_paths[ch_idx].append(path)
        
        self.refresh_ui_channel_bar()
        self.restart_sampling_thread()

    def unbind_channel(self, ch_idx):
        locker = QMutexLocker(self.buffer_mutex)
        path = self.ch_paths[ch_idx]
        if path:
            for p_idx, p_active in enumerate(self.plot_active_paths):
                if path in p_active: 
                    p_active.remove(path)
                if path in self.plot_curves_dict[p_idx]:
                    try:
                        self.plot_widgets[p_idx].removeItem(self.plot_curves_dict[p_idx][path])
                        del self.plot_curves_dict[p_idx][path]
                    except:
                        pass
                        
        self.ch_paths[ch_idx] = None
        self.ch_types[ch_idx] = None
        self.ch_addrs[ch_idx] = None
        self.ch_buffers[ch_idx] = []
        self.ch_time_stamps[ch_idx] = []
        self.refresh_ui_channel_bar()
        self.restart_sampling_thread()

    def refresh_ui_channel_bar(self):
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        for i in range(3):
            labels[i].setText(f"CH{i+1}: {self.ch_paths[i]} 🔄" if self.ch_paths[i] else f"CH{i+1}: 📭 空闲")

    def quick_add_variable(self):
        name = self.input_name.text().strip()
        v_type = self.input_type.currentText()
        if not name: return
        
        current_tab_idx = self.tab_widget.currentIndex()
        if current_tab_idx < 0: return

        if name.startswith("0x") or name.startswith("0X"):
            self.db.save_var(name, v_type, 0, name, current_tab_idx)
            self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
            self.input_name.clear()
            return

        if name not in self.symbol_cache:
            QMessageBox.warning(self, "提示", f"符号名 '{name}' 暂未在 ELF 固件映射文件中检索到。")
            return
        
        self.db.save_var(name, v_type, 0, name, current_tab_idx)
        self.inject_var_to_tree_ui(name, v_type, current_tab_idx)
        self.input_name.clear()

    def open_advance_parser_window(self):
        current_tab_idx = self.tab_widget.currentIndex()
        if current_tab_idx < 0: return
        
        dlg = AdvanceCParserDialog(self.db, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            code = self.db.get_c_code()
            self.parser.parse_structs(code)
            self.parser.parse_variables(code)
            
            flattened_items = self.parser.flatten_all()
            if not flattened_items:
                QMessageBox.warning(self, "解析提示", "未能在源码中提炼出有效的全局变量定义，请检查结尾是否声明了实例。")
                return

            for item in flattened_items:
                path = item['path']
                v_type = item['type']
                offset = item['offset']
                base_var = path.split('.')[0].split('[')[0]
                self.db.save_var(path, v_type, offset, base_var, current_tab_idx)
                
            flattened_items.sort(key=lambda x: (x['path'].count('.') + x['path'].count('[')))
            for item in flattened_items:
                self.inject_var_to_tree_ui(item['path'], item['type'], current_tab_idx)

    def handle_tree_item_double_clicked(self, item, column):
        if column != 2: return
        
        val_text = item.text(2)
        v_type = item.text(1)
        path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
        
        if "{结构体/数组}" in val_text or "断开" in val_text or "异常" in val_text:
            return
            
        if v_type in self.parser.structs or v_type == "struct/array":
            return

        all_db_paths = [x[0] for x in self.db.get_all_vars()]
        has_children = any(p.startswith(path + ".") or p.startswith(path + "[") for p in all_db_paths if p != path)
        if has_children:
            return
        
        tree = self.get_current_tree()
        editor = InPlaceEditLineEdit(tree, val_text, v_type)
        tree.setItemWidget(item, column, editor)
        editor.setFocus()
        editor.editing_finished_signal.connect(lambda text: self.finish_tree_item_editing(item, column, text))

    def finish_tree_item_editing(self, item, column, new_text):
        tree = self.get_current_tree()
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
            
        norm_type = v_type.replace("_t", "").strip()
        width = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
        
        try:
            if "float" in norm_type:
                raw_int = struct.unpack('<I', struct.pack('<f', float(new_text)))[0]
                self.send_write_command(addr_hex, raw_int, 4, is_float=True)
            else:
                self.send_write_command(addr_hex, int(float(new_text)), width)
            item.setText(2, new_text)
        except: pass

    def remove_variable_root(self, root_item):
        current_tab_idx = self.tab_widget.currentIndex()
        root_name = root_item.text(0)
        for path, _, _, _, t_idx in self.db.get_all_vars():
            if t_idx == current_tab_idx and (path == root_name or path.startswith(root_name + ".") or path.startswith(root_name + "[")):
                self.db.delete_var(path, current_tab_idx)
                for i in range(3):
                    if self.ch_paths[i] == path: self.unbind_channel(i)
                        
        tree = self.get_current_tree()
        if tree:
            index = tree.indexOfTopLevelItem(root_item)
            if index >= 0: tree.takeTopLevelItem(index)
        self.restart_sampling_thread()

    def restart_sampling_thread(self):
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
            self.worker = None
            
        hz_idx = self.wave_hz_combo.currentIndex()
        if hz_idx == 0:
            try: hz = int(self.custom_hz_edit.text().strip())
            except: hz = 300
        else:
            hz = [300, 500, 1000, 2000, 4000, 8000][hz_idx]
            
        self.custom_hz_edit.setEnabled(hz_idx == 0)
        delay = 1.0 / hz
        
        active_channels = {}
        for i in range(3):
            if self.ch_addrs[i]: active_channels[i] = (self.ch_addrs[i], self.ch_types[i], self.ch_paths[i])
                
        if active_channels:
            self.worker = HighSpeedSamplingWorker(self.config, active_channels, delay, self.jlink_native_instance)
            self.worker.data_received.connect(self.handle_high_speed_data)
            self.worker.start()

    def low_speed_tree_refresh(self):
        if self.layout_switching: return
        all_db_vars = {x[0]: (x[2], x[3], x[4] if len(x)>4 else 0) for x in self.db.get_all_vars()}
        labels = [self.lbl_ch1, self.lbl_ch2, self.lbl_ch3]
        
        locker = QMutexLocker(self.buffer_mutex)
        
        for i in range(3):
            if self.ch_paths[i]:
                labels[i].setText(f"CH{i+1}: {self.ch_buffers[i][-1]:.2f}" if (self.ch_buffers[i] and (self.config.backend_mode == "JLINK" or self.openocd_link_alive)) else f"CH{i+1}: 断开")
            else:
                labels[i].setText(f"CH{i+1}: 📭 空闲")

        def refresh_item(item, t_idx):
            tree = self.tab_widget.widget(t_idx)
            if not tree or tree.itemWidget(item, 2) is not None: return

            path = item.data(0, Qt.ItemDataRole.UserRole) or item.text(0)
            v_type = item.text(1)
            if "{结构体/数组}" in item.text(2):
                for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)
                return
                
            for i in range(3):
                if self.ch_paths[i] == path:
                    if self.config.backend_mode == "OPENOCD" and not self.openocd_link_alive:
                        item.setText(2, "断开")
                    else:
                        item.setText(2, f"{self.ch_buffers[i][-1]:.4f}" if self.ch_buffers[i] else "载入中")
                    return
            
            if path not in all_db_vars: return
            offset, base_var, _ = all_db_vars[path]
            
            if base_var.startswith("0x") or base_var.startswith("0X"):
                absolute_address = int(base_var, 16) + offset
            else:
                base_addr = self.symbol_cache.get(base_var)
                if base_addr is None: return
                absolute_address = base_addr + offset
            
            norm_type = v_type.strip()
            is_custom_enum = (norm_type in self.parser.enums or norm_type.replace("_t", "") in self.parser.enums)
            
            try:
                if "int8" in norm_type or "char" in norm_type: width_bytes = 1
                elif "int16" in norm_type: width_bytes = 2
                else: width_bytes = 4

                if self.config.backend_mode == "OPENOCD":
                    if not self.openocd_link_alive: raise Exception()
                    cmd = "mdb" if width_bytes == 1 else ("mdh" if width_bytes == 2 else "mdw")
                    res = self.send_tcl_cmd(f"{cmd} {hex(absolute_address)}")
                    if "ERROR" in res or ":" not in res: raise Exception()
                    raw_int = int(res.split(":")[-1].strip(), 16)
                else:
                    if not self.jlink_native_instance or not self.jlink_native_instance.target_connected(): raise Exception()
                    if width_bytes == 1: raw_int = self.jlink_native_instance.memory_read8(absolute_address, 1)[0]
                    elif width_bytes == 2: raw_int = self.jlink_native_instance.memory_read16(absolute_address, 1)[0]
                    else: raw_int = self.jlink_native_instance.memory_read32(absolute_address, 1)[0]
                
                if "uint8" in norm_type: 
                    v = raw_int & 0xFF
                elif "int8" in norm_type or "char" in norm_type: 
                    v = struct.unpack('<b', struct.pack('<B', raw_int & 0xFF))[0]
                elif "uint16" in norm_type: 
                    v = raw_int & 0xFFFF
                elif "int16" in norm_type: 
                    v = struct.unpack('<h', struct.pack('<H', raw_int & 0xFFFF))[0]
                elif "uint32" in norm_type: 
                    v = raw_int & 0xFFFFFFFF
                elif "float" in norm_type: 
                    v = struct.unpack('<f', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                elif is_custom_enum: 
                    v = raw_int & 0xFF
                else: 
                    v = struct.unpack('<i', struct.pack('<I', raw_int & 0xFFFFFFFF))[0]
                
                item.setText(2, f"{v:.4f}" if "float" in norm_type else str(v))
            except:
                item.setText(2, "断开")
                
            for idx in range(item.childCount()): refresh_item(item.child(idx), t_idx)

        for tab_i in range(self.tab_widget.count()):
            tree = self.tab_widget.widget(tab_i)
            if tree:
                for i in range(tree.topLevelItemCount()): 
                    refresh_item(tree.topLevelItem(i), tab_i)

    def inject_var_to_tree_ui(self, path, v_type, tab_idx):
        tree = self.tab_widget.widget(tab_idx)
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
                child_type = "{未知}"
                for h_path, h_type, _, _, h_t_idx in self.db.get_all_vars():
                    if h_t_idx == tab_idx and h_path == current_path_accumulator:
                        child_type = h_type; break
                
                if child_type == "{未知}" and i < len(parts) - 1:
                    child_type = "struct/array"

                is_container = (child_type in self.parser.structs or 
                                ("[" in child_type and not re.search(r'\[\d+\]$', current_path_accumulator)) or 
                                i < len(parts) - 1 or 
                                child_type == "struct/array")
                
                val_display = "{结构体/数组}" if is_container else "读取中..."
                
                new_item = QTreeWidgetItem([display_name, child_type, val_display])
                new_item.setData(0, Qt.ItemDataRole.UserRole, current_path_accumulator)
                
                if current_parent is None:
                    tree.addTopLevelItem(new_item)
                else:
                    current_parent.addChild(new_item)
                found_child = new_item
            
            current_parent = found_child

    def update_hardware_heartbeat(self):
        is_alive = False
        if self.config.backend_mode == "OPENOCD":
            worker_alive = hasattr(self, 'worker') and self.worker and self.worker.isRunning() and self.worker.is_link_alive
            is_alive = worker_alive and self.openocd_link_alive
        else:
            is_alive = True if (self.jlink_native_instance and self.jlink_native_instance.target_connected()) else False

        if is_alive:
            self.heartbeat_state = not self.heartbeat_state
            self.lbl_led.setStyleSheet(f"background-color: {'#00E676' if self.heartbeat_state else '#00b252'}; border-radius: 5px;")
            self.lbl_status_text.setText(f"调试在线 ({self.config.backend_mode})")
            self.lbl_status_text.setStyleSheet("color: #00E676; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_led.setStyleSheet("background-color: #d32f2f; border-radius: 5px;")
            self.lbl_status_text.setText("链路断开")
            self.lbl_status_text.setStyleSheet("color: #d32f2f; font-family: 'Microsoft YaHei'; font-size: 11px; font-weight: bold;")

    def closeEvent(self, event):
        if hasattr(self, 'worker') and self.worker: 
            self.worker.stop()
        if self.tcl_socket:
            try: self.tcl_socket.close()
            except: pass
        if self.jlink_native_instance:
            try: self.jlink_native_instance.close()
            except: pass
        event.accept()


class HighSpeedSamplingWorker(QThread):
    data_received = pyqtSignal(list)

    def __init__(self, config, active_channels, sample_delay, jlink_instance=None):
        super().__init__()
        self.config = config
        self.channels = active_channels 
        self.sample_delay = sample_delay
        self.jlink = jlink_instance
        self.running = True
        self.is_link_alive = False  

    def run(self):
        s = None
        parsed_channels = []
        for ch_idx, (addr_hex, v_type, path) in self.channels.items():
            addr_int = int(addr_hex, 16)
            norm_type = v_type.replace("_t", "").strip().lower()
            width = 1 if "int8" in norm_type or "char" in norm_type else (2 if "int16" in norm_type else 4)
            parsed_channels.append({
                'ch_idx': ch_idx, 'addr': addr_int, 'width': width, 
                'is_float': "float" in norm_type, 'is_signed': "int" in norm_type and "uint" not in norm_type
            })

        if self.config.backend_mode == "OPENOCD":
            if not parsed_channels:
                use_block_read = False
                merged_cmd_bytes = b""
            else:
                addrs = [c['addr'] for c in parsed_channels]
                block_start, block_end = min(addrs) & ~0x3, (max(addrs) + 7) & ~0x3
                word_count = (block_end - block_start) // 4
                use_block_read = (block_end - block_start) <= 128 and len(parsed_channels) > 1
                merged_cmd = f"mdw {hex(block_start)} {word_count}\x1a" if use_block_read else " ; ".join([f"{'mdb' if c['width']==1 else ('mdh' if c['width']==2 else 'mdw')} {hex(c['addr'])}" for c in parsed_channels]) + "\x1a"
                merged_cmd_bytes = merged_cmd.encode('utf-8')

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(0.3)
                s.connect((self.config.openocd_host, self.config.tcl_port))
                self.is_link_alive = True
            except:
                self.is_link_alive = False

        local_batch = []
        last_flush_time = time.time()

        while self.running:
            t_now = time.time()
            packet = {}

            if self.config.backend_mode == "OPENOCD":
                if not merged_cmd_bytes or not self.is_link_alive:
                    time.sleep(0.04)
                    continue
                try:
                    s.sendall(merged_cmd_bytes)
                    res = s.recv(2048).decode('utf-8').replace('\x1a', '').strip()
                    if "ERROR" in res: raise Exception()
                    lines = [l.strip() for l in res.split('\n') if l.strip()]
                    
                    if use_block_read:
                        words = []
                        for line in lines:
                            if ":" in line: words.extend([int(x, 16) for x in line.split(":")[-1].strip().split()])
                        for c in parsed_channels:
                            idx = (c['addr'] - block_start) // 4
                            shift = ((c['addr'] - block_start) % 4) * 8
                            if idx < len(words):
                                raw_val = (words[idx] >> shift) & ((1 << (c['width']*8)) - 1)
                                packet[c['ch_idx']] = struct.unpack('<f', struct.pack('<I', raw_val))[0] if c['is_float'] else float(raw_val - 256 if c['is_signed'] and c['width']==1 and raw_val>=128 else (raw_val - 65536 if c['is_signed'] and c['width']==2 and raw_val>=32768 else raw_val))
                    else:
                        for i, c in enumerate(parsed_channels):
                            if i < len(lines):
                                raw_int = int(lines[i].split(":")[-1].strip(), 16)
                                packet[c['ch_idx']] = struct.unpack('<f', struct.pack('<I', raw_int))[0] if c['is_float'] else float(raw_int)
                except: 
                    self.is_link_alive = False  
                    continue
            else:
                if self.jlink and self.jlink.target_connected():
                    try:
                        for c in parsed_channels:
                            if c['width'] == 1: raw = self.jlink.memory_read8(c['addr'], 1)[0]
                            elif c['width'] == 2: raw = self.jlink.memory_read16(c['addr'], 1)[0]
                            else: raw = self.jlink.memory_read32(c['addr'], 1)[0]
                            packet[c['ch_idx']] = struct.unpack('<f', struct.pack('<I', raw))[0] if c['is_float'] else float(raw)
                    except: 
                        time.sleep(0.04) 
                        continue
                else:
                    time.sleep(0.04)
                    continue

            if packet: local_batch.append((t_now, packet))
            if len(local_batch) >= 20 or (t_now - last_flush_time > 0.025):
                if self.running and local_batch: self.data_received.emit(local_batch)
                local_batch, last_flush_time = [], t_now

            if self.sample_delay > 0: time.sleep(self.sample_delay)

        if s: 
            try: s.close()
            except: pass

    def stop(self):
        self.running = False
        self.wait()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    db = LiveWatchDB()
    dlg = LoginConfigDialog(db)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.is_connected:
        win = MCUStudioWindow(dlg.config, db)
        win.show()
        sys.exit(app.exec())