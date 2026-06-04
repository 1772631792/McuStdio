# -*- coding: utf-8 -*-
# extract_param.py

import os
import sys
import re
import sqlite3
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QPushButton, QTreeWidget, QTreeWidgetItem, 
                             QSplitter, QLabel, QMessageBox, QHeaderView, QMenu)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

POPUP_MENU_STYLE = """
    QMenu { background-color: #222222; color: #ffffff; border: 1px solid #444444; padding: 6px; font-family: "Microsoft YaHei", "Consolas"; font-size: 13px; font-weight: bold; }
    QMenu::item { padding: 6px 28px 6px 16px; border-radius: 4px; background-color: transparent; color: #ffffff; }
    QMenu::item:selected { background-color: #1e88e5; color: #ffffff; }
    QMenu::separator { height: 1px; background-color: #444444; margin: 4px 0px; }
"""

# 全局关键字拦截
STRICT_CONTROL_KEYWORDS = {'if', 'else', 'while', 'for', 'switch', 'return', 'break', 'continue', 'goto', 'typedef', 'void', 'main', 'struct', 'enum', 'union', 'extern'}

C_MODIFIERS_CLEAN_REGEX = re.compile(r'\b(volatile|const|extern|register|inline|__inline|__inline__)\b', re.IGNORECASE)
STATIC_MATCH_REGEX = re.compile(r'\bstatic\b')


class ParamDB:
    def __init__(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(base_dir, "param.db")
        self.conn = None
        self.cursor = None
        try:
            self.conn = sqlite3.connect(self.db_path, timeout=1.0)
            self.cursor = self.conn.cursor()
            self.init_tables()
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()

    def init_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_fingerprints (
                file_path TEXT PRIMARY KEY, last_modified REAL
            )''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS extracted_variables (
                file_path TEXT, path TEXT, type TEXT, offset INTEGER,
                PRIMARY KEY(file_path, path)
            )''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ui_config (
                key TEXT PRIMARY KEY, value TEXT
            )''')
        self.conn.commit()

    def safe_destruction_and_rebuild(self):
        try:
            if self.conn: self.conn.close()
        except: pass
        if os.path.exists(self.db_path):
            try: os.remove(self.db_path)
            except: pass
        self.conn = sqlite3.connect(self.db_path, timeout=1.0)
        self.cursor = self.conn.cursor()
        self.init_tables()

    def get_config(self, key, default=""):
        try:
            self.cursor.execute("SELECT value FROM ui_config WHERE key=?", (key,))
            row = self.cursor.fetchone()
            return row[0] if row else default
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()
            return default

    def save_config(self, key, value):
        try:
            self.cursor.execute("INSERT OR REPLACE INTO ui_config (key, value) VALUES (?, ?)", (key, str(value)))
            self.conn.commit()
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()

    def get_last_modified(self, file_path):
        try:
            self.cursor.execute("SELECT last_modified FROM file_fingerprints WHERE file_path=?", (file_path,))
            row = self.cursor.fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()
            return None

    def begin_transaction(self):
        try: self.cursor.execute("BEGIN TRANSACTION")
        except: pass

    def end_transaction(self):
        try: self.conn.commit()
        except: pass

    def update_file_fingerprint(self, file_path, mtime):
        try: self.cursor.execute("INSERT OR REPLACE INTO file_fingerprints (file_path, last_modified) VALUES (?, ?)", (file_path, mtime))
        except: pass

    def clear_file_data(self, file_path):
        try: self.cursor.execute("DELETE FROM extracted_variables WHERE file_path=?", (file_path,))
        except: pass

    def save_variable(self, file_path, path, v_type, offset):
        try: self.cursor.execute("INSERT OR REPLACE INTO extracted_variables (file_path, path, type, offset) VALUES (?, ?, ?, ?)", (file_path, path, v_type, offset))
        except: pass

    def get_file_variables(self, file_path):
        try:
            self.cursor.execute("SELECT path, type, offset FROM extracted_variables WHERE file_path=?", (file_path,))
            return [{"path": r[0], "type": r[1], "offset": r[2]} for r in self.cursor.fetchall()]
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()
            return []

    def load_all_cached_records(self, root_dir):
        try:
            self.cursor.execute("SELECT file_path, path, type, offset FROM extracted_variables")
            rows = self.cursor.fetchall()
            file_map = {}
            for f_path, p, t, off in rows:
                if not f_path.startswith(root_dir): continue
                rel_path = os.path.relpath(f_path, root_dir)
                if rel_path not in file_map:
                    file_map[rel_path] = {"rel_path": rel_path, "file_name": os.path.basename(f_path), "flattened": []}
                file_map[rel_path]["flattened"].append({"path": p, "type": t, "offset": off})
            return list(file_map.values())
        except sqlite3.Error:
            self.safe_destruction_and_rebuild()
            return []

    def close(self):
        if self.conn:
            try: self.conn.close()
            except: pass


class LocalCacheLoaderWorker(QThread):
    cache_loaded_signal = pyqtSignal(list, str)

    def __init__(self, project_root):
        super().__init__()
        self.project_root = project_root

    def run(self):
        if not self.project_root or not os.path.exists(self.project_root):
            self.cache_loaded_signal.emit([], "INVALID_PATH")
            return
        pdb = ParamDB()
        records = pdb.load_all_cached_records(self.project_root)
        search_kw = pdb.get_config("last_source_search_keyword", "")
        pdb.close()
        self.cache_loaded_signal.emit(records, search_kw)


class SourceCodeParserWorker(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(list)

    def __init__(self, root_dir, parser_instance, force_refresh=False):
        super().__init__()
        self.root_dir = os.path.normpath(root_dir)
        self.parser = parser_instance
        self.force_refresh = force_refresh

    def extract_variables_by_stream(self, content):
        content = re.sub(r'//.*', '', content)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.S)
        content = re.sub(r'#\s*\w+.*', '', content)
        
        content = content.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
        
        output_chars = []
        brace_level = 0
        for char in content:
            if char == '{':
                if brace_level == 0: output_chars.append(';') 
                brace_level += 1
                continue
            elif char == '}':
                brace_level -= 1
                if brace_level < 0: brace_level = 0
                idx = 0
                continue
            if brace_level == 0: output_chars.append(char)
                
        stream_pure = "".join(output_chars)
        statements = stream_pure.split(';')
        results = []
        
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt: continue
            if re.match(r'^\s*(if|else|while|for|switch|return|break|continue)\b', stmt): continue
            if '=' in stmt: stmt = stmt.split('=')[0].strip()
                
            stmt = C_MODIFIERS_CLEAN_REGEX.sub('', stmt).strip()
            stmt = STATIC_MATCH_REGEX.sub('', stmt).strip()
            stmt = re.sub(r'\s+', ' ', stmt).strip()
            if not stmt: continue
            if '(' in stmt or ')' in stmt: continue
            if 'typedef ' in stmt: continue
            
            tokens = stmt.split(' ')
            if len(tokens) < 2: continue
            
            target_var_token = tokens[-1].strip()
            c_type = " ".join(tokens[:-1]).strip()
            
            if c_type in STRICT_CONTROL_KEYWORDS or target_var_token in STRICT_CONTROL_KEYWORDS: continue
            if ' ' in target_var_token or ',' in target_var_token: continue 
            
            count = 1
            arr_match = re.search(r'\[(\d+)\]', target_var_token)
            if arr_match: count = int(arr_match.group(1))
            var_name = re.sub(r'\[.*?\]', '', target_var_token).replace('*', '').strip()
            
            if not var_name or var_name in STRICT_CONTROL_KEYWORDS or var_name == c_type: continue
            
            results.append({
                "path": var_name,
                "type": f"{c_type}[{count}]" if count > 1 else c_type,
                "offset": 0
            })
        return results

    def run(self):
        file_records = []
        valid_extensions = ('.c', '.h', '.C', '.H')
        pdb = ParamDB()
        target_files = []
        for root, dirs, files in os.walk(self.root_dir):
            for file in files:
                if file.endswith(valid_extensions): target_files.append(os.path.join(root, file))

        for full_path in target_files:
            try:
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                clean_code = self.parser.remove_comments(content)
                
                enum_matches = re.findall(r'typedef[\s\xa0]+enum[\s\xa0]*\{.*?\}(?:\s*__\w+__)?[\s\xa0]*(\\w+)[\s\xa0]*;', clean_code, flags=re.S)
                for enum_name in enum_matches: self.parser.enums.add(enum_name.strip())

                struct_matches = re.findall(r'typedef[\s\xa0]+struct[\s\xa0]*\{(.*?)\}[\s\xa0]*(\w+)[\s\xa0]*;', clean_code, flags=re.S)
                for body, struct_name in struct_matches:
                    members = []
                    for stmt in body.split(';'):
                        stmt = stmt.strip()
                        if not stmt or not re.match(r'(.+?)[\s\xa0]+(.+)', stmt): continue
                        m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
                        ctype = m.group(1).strip()
                        for var in m.group(2).strip().split(','):
                            var = var.strip()
                            count = 1
                            arr = re.search(r'\[(\d+)\]', var)
                            if arr: count = int(arr.group(1))
                            name = re.sub(r'\[.*?\]', '', var).strip()
                            members.append({"type": ctype, "name": name, "count": count})
                    self.parser.structs[struct_name] = members
            except: pass

        pdb.begin_transaction()
        try:
            for full_path in target_files:
                rel_path = os.path.relpath(full_path, self.root_dir)
                file_name = os.path.basename(full_path)
                current_mtime = os.path.getmtime(full_path)
                cached_mtime = pdb.get_last_modified(full_path)
                
                if self.force_refresh or cached_mtime is None or current_mtime > cached_mtime:
                    self.progress_signal.emit(f"深度提取中: {file_name}")
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                    cleaned_vars = self.extract_variables_by_stream(content)
                    
                    pdb.clear_file_data(full_path)
                    for item in cleaned_vars: pdb.save_variable(full_path, item['path'], item['type'], item['offset'])
                    pdb.update_file_fingerprint(full_path, current_mtime)
                else:
                    cleaned_vars = pdb.get_file_variables(full_path)
                
                if cleaned_vars:
                    file_records.append({"rel_path": rel_path, "file_name": file_name, "flattened": cleaned_vars})
        except Exception as e:
            print(f"解析异常: {e}")
        finally:
            pdb.end_transaction()
            pdb.close()
        self.finished_signal.emit(file_records)


class AdvancedWorkspaceDialog(QWidget):
    symbol_selected_signal = pyqtSignal(str, str, int)

    def __init__(self, db, parser_instance, symbol_cache=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.setWindowModality(Qt.WindowModality.NonModal)
        
        self.db = db
        self.parser = parser_instance
        self.symbol_cache = symbol_cache if isinstance(symbol_cache, (dict, list, set)) else {}
        self.project_root = ""
        self.all_file_records = [] 
        
        self.setWindowTitle("🛒 嵌入式工程级多源 C/H 静态符号智能提炼工坊")
        self.resize(1150, 750)
        self.init_ui()
        
        pdb = ParamDB()
        self.project_root = pdb.get_config("last_source_project_root", "")
        left_w = pdb.get_config("splitter_left_width", "")
        right_w = pdb.get_config("splitter_right_width", "")
        if left_w and right_w: self.splitter.setSizes([int(left_w), int(right_w)])
        else: self.splitter.setSizes([380, 770])
        pdb.close()
        
        self.status_lbl.setText("⏳ 正在进行本地快照无感离线反序列化加载...")
        
        self.cache_worker = LocalCacheLoaderWorker(self.project_root)
        self.cache_worker.cache_loaded_signal.connect(self.handle_cache_async_ready)
        self.cache_worker.start()

    def init_ui(self):
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #e0e0e0; font-family: "Microsoft YaHei", "Consolas"; }
            QLabel { font-size: 13px; color: #b0bec5; }
            QLineEdit { background-color: #1e1e1e; border: 1px solid #37474f; padding: 6px 10px; color: #ffffff; border-radius: 4px; font-size: 14px; font-family: "Consolas"; }
            QLineEdit:focus { border: 1px solid #00e5ff; background-color: #262626; }
            QPushButton { background-color: #263238; border: 1px solid #455a64; padding: 6px 14px; color: #ffffff; border-radius: 4px; font-size: 13px; font-weight: bold; }
            QPushButton:hover { background-color: #37474f; border: 1px solid #00e5ff; }
            QTreeWidget { background-color: #161616; border: 1px solid #263238; border-radius: 6px; alternate-background-color: #1d1d1d; }
            QTreeWidget::item { height: 32px; border-bottom: 1px solid #212121; font-size: 14px; color: #e0e0e0; }
            QTreeWidget::item:hover { background-color: #262626; color: #00e5ff; }
            QTreeWidget::item:selected { background-color: #004d40; color: #00e676; font-weight: bold; }
            QHeaderView::section { background-color: #212121; color: #00e5ff; padding: 6px; border: 1px solid #121212; font-weight: bold; font-size: 13px; }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        
        top_bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 输入最外层全局/静态变量实例名，右键无缝直接秒加主监视表...")
        self.search_edit.textChanged.connect(self.execute_live_filter)
        
        self.btn_sync = QPushButton("🔍 智能增量同步")
        self.btn_sync.setStyleSheet("background-color: #004d40; color: #00e676; border: 1px solid #00796b;")
        self.btn_sync.clicked.connect(lambda: self.execute_async_parse_engine(force_refresh=False))
        
        self.btn_force = QPushButton("🔄 全量重载")
        self.btn_force.clicked.connect(self.handle_force_reset_and_delete_db)
        
        btn_select_dir = QPushButton("📂 定位工程根目录")
        btn_select_dir.clicked.connect(self.select_project_directory)
        
        top_bar.addWidget(QLabel("<b>全域动态检索:</b>"))
        top_bar.addWidget(self.search_edit, 10)
        top_bar.addWidget(self.btn_sync, 3)
        top_bar.addWidget(self.btn_force, 2)
        top_bar.addWidget(btn_select_dir, 3)
        layout.addLayout(top_bar)
        
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setStyleSheet("QSplitter::handle { width: 4px; background-color: #263238; }")
        
        self.file_tree = QTreeWidget()
        self.file_tree.setColumnCount(1)
        self.file_tree.setHeaderLabels(["📁 工程源码树 (自动忽略非 C/H 文件)"])
        self.file_tree.itemClicked.connect(self.handle_file_tree_node_clicked)
        self.splitter.addWidget(self.file_tree)
        
        self.var_tree = QTreeWidget()
        self.var_tree.setColumnCount(3)
        self.var_tree.setHeaderLabels(["变量映射绝对路径", "C 原始数据类型", "扁平内存相对偏移"])
        self.var_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.var_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.var_tree.customContextMenuRequested.connect(self.show_variable_right_click_menu)
        self.splitter.addWidget(self.var_tree)
        
        layout.addWidget(self.splitter)
        
        self.status_lbl = QLabel("状态：就绪。")
        self.status_lbl.setFixedHeight(20)
        self.status_lbl.setStyleSheet("color: #00e5ff; font-family: 'Consolas'; font-size: 12px;")
        layout.addWidget(self.status_lbl)

    def handle_force_reset_and_delete_db(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        
        pdb = ParamDB()
        pdb.safe_destruction_and_rebuild()
        pdb.close()
        
        self.file_tree.clear()
        self.var_tree.clear()
        self.execute_async_parse_engine(force_refresh=True)

    def handle_cache_async_ready(self, records, keyword):
        self.all_file_records = records
        self.search_edit.setText(keyword)
        if self.project_root and os.path.exists(self.project_root):
            self.render_file_tree_ui()
            self.status_lbl.setText("🟢 离线快照加载完毕。主子窗口现已全面恢复并行流畅操作。")
        else:
            self.project_root = ""
            self.status_lbl.setText("⚠️ 提示：当前的工程路径已失效或不存在！请点击右上方【📂 定位工程根目录】重新绑定。")

    def select_project_directory(self):
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "选择嵌入式工程源码的绝对根路径", self.project_root)
        if path:
            self.project_root = os.path.normpath(path)
            pdb = ParamDB()
            pdb.save_config("last_source_project_root", self.project_root)
            pdb.close()
            self.execute_async_parse_engine(force_refresh=False)

    def update_live_symbol_cache(self, updated_cache):
        if isinstance(updated_cache, (dict, list, set)):
            self.symbol_cache = updated_cache

    def execute_async_parse_engine(self, force_refresh=False):
        if not self.project_root or not os.path.exists(self.project_root): return
        self.status_lbl.setText("🚀 正在对工程源码执行极速并行差异性解构分析...")
        self.file_tree.clear()
        self.var_tree.clear()
        
        self.worker = SourceCodeParserWorker(self.project_root, self.parser, force_refresh=force_refresh)
        self.worker.progress_signal.connect(lambda msg: self.status_lbl.setText(msg))
        self.worker.finished_signal.connect(self.handle_parse_engine_callback)
        self.worker.start()

    def handle_parse_engine_callback(self, file_records):
        self.all_file_records = file_records
        self.status_lbl.setText(f"🟢 同步完毕！当前共解构出 {len(file_records)} 个有效文件符号节点。")
        self.render_file_tree_ui()

    def render_file_tree_ui(self):
        self.file_tree.clear()
        self.file_tree.setUpdatesEnabled(False)
        for rec in self.all_file_records:
            parts = rec["rel_path"].split(os.sep)
            current_parent = None
            for i, part in enumerate(parts):
                found = False
                if current_parent is None:
                    for idx in range(self.file_tree.topLevelItemCount()):
                        if self.file_tree.topLevelItem(idx).text(0) == part:
                            current_parent = self.file_tree.topLevelItem(idx)
                            found = True; break
                else:
                    for idx in range(current_parent.childCount()):
                        if current_parent.child(idx).text(0) == part:
                            current_parent = current_parent.child(idx)
                            found = True; break
                            
                if not found:
                    new_node = QTreeWidgetItem([part])
                    if i == len(parts) - 1:
                        new_node.setData(0, Qt.ItemDataRole.UserRole, rec)
                        new_node.setForeground(0, Qt.GlobalColor.white)
                    else:
                        new_node.setForeground(0, QColor("#78909c"))
                    if current_parent is None: self.file_tree.addTopLevelItem(new_node)
                    else: current_parent.addChild(new_node)
                    current_parent = new_node
        self.file_tree.expandAll()
        self.file_tree.setUpdatesEnabled(True)
        self.execute_live_filter()

    def handle_file_tree_node_clicked(self, item, col):
        rec = item.data(0, Qt.ItemDataRole.UserRole)
        if rec: self.render_target_variables_to_right_table(rec["flattened"])
        else: self.var_tree.clear()

    def render_target_variables_to_right_table(self, flattened_list):
        self.var_tree.clear()
        self.var_tree.setUpdatesEnabled(False)
        for item in flattened_list:
            path = item['path']
            v_type = item['type']
            offset = item['offset']
            new_node = QTreeWidgetItem([path, v_type, f"+ {offset} (0x{offset:02X})"])
            new_node.setData(0, Qt.ItemDataRole.UserRole, (path, v_type, offset))
            self.var_tree.addTopLevelItem(new_node)
        self.var_tree.setUpdatesEnabled(True)

    def execute_live_filter(self):
        kw = self.search_edit.text().strip().lower()
        pdb = ParamDB()
        pdb.save_config("last_source_search_keyword", kw)
        pdb.close()
        if not kw:
            selected_items = self.file_tree.selectedItems()
            if selected_items: self.handle_file_tree_node_clicked(selected_items[0], 0)
            return
        filtered_flat_pool = []
        for rec in self.all_file_records:
            for item in rec["flattened"]:
                if kw in item['path'].lower() or kw in item['type'].lower(): filtered_flat_pool.append(item)
        if filtered_flat_pool: self.render_target_variables_to_right_table(filtered_flat_pool)

    def show_variable_right_click_menu(self, pos):
        item = self.var_tree.itemAt(pos)
        if not item: return
        data_tuple = item.data(0, Qt.ItemDataRole.UserRole)
        if not data_tuple: return
        path, v_type, offset = data_tuple
            
        menu = QMenu()
        menu.setStyleSheet(POPUP_MENU_STYLE)
        m_inject = menu.addAction("➕ 将此静态符号无缝添加到主监视栏")
        act = menu.exec(self.var_tree.viewport().mapToGlobal(pos))
        if act == m_inject:
            base_name = path.split('.')[0].split('[')[0]
            
            # ⚡ 终极全自动清洗自愈：检测并强制擦除主数据库（self.db）里之前积累的陈年残留影子点号噪声！
            try:
                # 动态获取主视窗对应的底层 sqlite3 连接句柄或执行环境
                if hasattr(self.db, 'cursor') and self.db.cursor:
                    # 强力清除所有带有点号或中括号的影子碎片，例如 g_thermostat.current_temp
                    self.db.cursor.execute("DELETE FROM watched_variables WHERE path LIKE ?", (f"{base_name}.%",))
                    self.db.cursor.execute("DELETE FROM watched_variables WHERE path LIKE ?", (f"{base_name}[%",))
                    if hasattr(self.db, 'conn') and self.db.conn:
                        self.db.conn.commit()
            except Exception as db_err:
                print(f"💡 自动清洗历史影子残留提示: {db_err}")

            if self.symbol_cache and isinstance(self.symbol_cache, (dict, list, set)):
                if base_name not in self.symbol_cache:
                    QMessageBox.warning(self, "符号优化提示", f"变量 '{base_name}' 虽在源码中声明，但未在固件物理 ELF 符号表中激活（可能被强优化或未被业务引用过）。")
                    return
            self.symbol_selected_signal.emit(path, v_type, offset)

    def closeEvent(self, event):
        try:
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and sizes[0] > 0:
                pdb = ParamDB()
                pdb.save_config("splitter_left_width", sizes[0])
                pdb.save_config("splitter_right_width", sizes[1])
                pdb.close()
        except: pass
        if hasattr(self, 'worker') and self.worker.isRunning(): self.worker.terminate()
        event.accept()