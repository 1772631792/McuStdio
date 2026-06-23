# -*- coding: utf-8 -*-
# extract_param.py

import os
import sys
import re
import sqlite3
import struct
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

STRICT_CONTROL_KEYWORDS = {'if', 'else', 'while', 'for', 'switch', 'return', 'break', 'continue', 'goto', 'typedef', 'void', 'main', 'struct', 'enum', 'union', 'extern'}
C_MODIFIERS_CLEAN_REGEX = re.compile(r'\b(volatile|const|extern|register|inline|__inline|__inline__|__packed|packed)\b', re.IGNORECASE)
STATIC_MATCH_REGEX = re.compile(r'\bstatic\b')
QUALIFIER_PATTERN = re.compile(r'\b(const|volatile|static|extern|register|inline|__inline|__inline__|__packed|packed)\b')
ATTRIBUTE_PATTERN = re.compile(r'__attribute__\s*\(\([^\)]*\)\)')

TYPE_SIZES = {
    "char": 1, "uint8_t": 1, "int8_t": 1, "uint8": 1, "int8": 1,
    "uint16_t": 2, "int16_t": 2, "uint16": 2, "int16": 2,
    "uint32_t": 4, "int32_t": 4, "uint32": 4, "int32": 4, "int": 4,
    "float": 4, "double": 8
}

class InnerStructParser:
    """内部集成式 C 结构体与数组对齐推演引擎"""
    def __init__(self):
        self.structs = {}
        self.variables = []
        self.enums = set()

    def normalize_type(self, ctype):
        ctype = QUALIFIER_PATTERN.sub('', ctype)
        ctype = ATTRIBUTE_PATTERN.sub('', ctype)
        ctype = re.sub(r'\s+', ' ', ctype).strip()
        return ctype

    def remove_comments(self, code):
        code = re.sub(r'//.*', '', code)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
        return code

    def parse_structs(self, code):
        code = self.remove_comments(code)
        enum_matches = re.findall(r'typedef[\s\xa0]+enum[\s\xa0]*\{.*?\}(?:\s*__\w+__)?[\s\xa0]*(\w+)[\s\xa0]*;', code, flags=re.S)
        for enum_name in enum_matches:
            self.enums.add(enum_name.strip())

        typedef_matches = re.findall(r'typedef[\s\xa0]+struct(?:[\s\xa0]+\w+)?[\s\xa0]*\{(.*?)\}[\s\xa0]*(\w+)[\s\xa0]*;', code, flags=re.S)
        for body, struct_name in typedef_matches:
            members = []
            for stmt in body.split(';'):
                stmt = stmt.strip()
                if not stmt or '(' in stmt or ')' in stmt: continue
                m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
                if not m: continue
                ctype = self.normalize_type(m.group(1).strip())
                for var in m.group(2).strip().split(','):
                    var = var.strip()
                    count = 1
                    arr = re.search(r'\[(\d+)\]', var)
                    if arr: count = int(arr.group(1))
                    name = re.sub(r'\[.*?\]', '', var).replace('*', '').strip()
                    members.append({"type": ctype, "name": name, "count": count})
            self.structs[struct_name] = members

        struct_matches = re.findall(r'struct[\s\xa0]+(\w+)[\s\xa0]*\{(.*?)\}[\s\xa0]*;', code, flags=re.S)
        for struct_name, body in struct_matches:
            if struct_name in self.structs: continue
            members = []
            for stmt in body.split(';'):
                stmt = stmt.strip()
                if not stmt or '(' in stmt or ')' in stmt: continue
                m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
                if not m: continue
                ctype = self.normalize_type(m.group(1).strip())
                for var in m.group(2).strip().split(','):
                    var = var.strip()
                    count = 1
                    arr = re.search(r'\[(\d+)\]', var)
                    if arr: count = int(arr.group(1))
                    name = re.sub(r'\[.*?\]', '', var).replace('*', '').strip()
                    members.append({"type": ctype, "name": name, "count": count})
            self.structs[struct_name] = members

    def get_alignment(self, ctype):
        if ctype in self.enums or ctype.replace("_t", "") in self.enums: return 4
        if ctype in TYPE_SIZES: return TYPE_SIZES[ctype]
        if ctype in self.structs:
            max_align = 1
            for member in self.structs[ctype]:
                align = self.get_alignment(member['type'])
                if align > max_align: max_align = align
            return max_align
        return 4

    def sizeof(self, ctype):
        if ctype in self.enums or ctype.replace("_t", "") in self.enums: return 4
        if ctype in TYPE_SIZES: return TYPE_SIZES[ctype]
        if ctype in self.structs:
            total = 0
            max_align = self.get_alignment(ctype)
            for member in self.structs[ctype]:
                m_type = member['type']
                count = member['count']
                m_size = self.sizeof(m_type)
                m_align = self.get_alignment(m_type)
                if total % m_align != 0:
                    total += (m_align - (total % m_align))
                total += m_size * count
            if total % max_align != 0:
                total += (max_align - (total % max_align))
            return total
        return 4

    def flatten_struct(self, ctype, prefix="", base_offset=0):
        result = []
        if ctype not in self.structs: return result
        offset = base_offset
        for member in self.structs[ctype]:
            m_type, m_name, count = member['type'], member['name'], member['count']
            m_size = self.sizeof(m_type)
            m_align = self.get_alignment(m_type)
            
            if offset % m_align != 0:
                offset += (m_align - (offset % m_align))
                
            if m_type in self.structs:
                if count == 1:
                    path = f"{prefix}{m_name}"
                    result.append({"path": path, "type": m_type, "offset": offset})
                    result.extend(self.flatten_struct(m_type, prefix=path + ".", base_offset=offset))
                    offset += m_size
                else:
                    result.append({"path": f"{prefix}{m_name}", "type": f"{m_type}[{count}]", "offset": offset})
                    for i in range(count):
                        arr_offset = offset + i * m_size
                        path = f"{prefix}{m_name}[{i}]"
                        result.append({"path": path, "type": m_type, "offset": arr_offset})
                        result.extend(self.flatten_struct(m_type, prefix=path + ".", base_offset=arr_offset))
                    offset += m_size * count
            else:
                if count == 1:
                    path = f"{prefix}{m_name}"
                    result.append({"path": path, "type": m_type, "offset": offset})
                    offset += m_size
                else:
                    a_path = f"{prefix}{m_name}"
                    result.append({"path": a_path, "type": f"{m_type}[{count}]", "offset": offset})
                    for i in range(count):
                        result.append({"path": f"{a_path}[{i}]", "type": m_type, "offset": offset + i * m_size})
                    offset += m_size * count
        return result

    def flatten_variable(self, var):
        """⚡【算法核心重写】：将顶级原生数组（如 float Test[3]）与嵌套结构体等同打散，强制产出级联子节点实体"""
        result = []
        v_type, v_name, count = var['type'], var['name'], var['count']
        t_size = self.sizeof(v_type)
        
        if v_type in self.structs:
            if count == 1:
                result.append({"path": v_name, "type": v_type, "offset": 0})
                result.extend(self.flatten_struct(v_type, prefix=v_name + ".", base_offset=0))
            else:
                result.append({"path": v_name, "type": f"{v_type}[{count}]", "offset": 0})
                for i in range(count):
                    base = i * t_size
                    path = f"{v_name}[{i}]"
                    result.append({"path": path, "type": v_type, "offset": base})
                    result.extend(self.flatten_struct(v_type, prefix=path + ".", base_offset=base))
        else:
            # 💡 针对基础数据类型数组（如 float Test[3]），无条件生成带中括号的基础可展开子树记录
            if count == 1:
                result.append({"path": v_name, "type": v_type, "offset": 0})
            else:
                result.append({"path": v_name, "type": f"{v_type}[{count}]", "offset": 0})
                for i in range(count):
                    result.append({"path": f"{v_name}[{i}]", "type": v_type, "offset": i * t_size})
        return result


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
            results.append({"name": var_name, "type": c_type, "count": count})
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
                self.parser.parse_structs(content) 
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
                    
                    raw_vars = self.extract_variables_by_stream(content)
                    flattened_vars = []
                    
                    for v in raw_vars:
                        flat_items = self.parser.flatten_variable(v)
                        for fi in flat_items:
                            flattened_vars.append({
                                "path": fi["path"], "type": fi["type"], "offset": fi["offset"]
                            })
                    
                    pdb.clear_file_data(full_path)
                    for item in flattened_vars: 
                        pdb.save_variable(full_path, item['path'], item['type'], item['offset'])
                    pdb.update_file_fingerprint(full_path, current_mtime)
                else:
                    flattened_vars = pdb.get_file_variables(full_path)
                
                if flattened_vars:
                    file_records.append({"rel_path": rel_path, "file_name": file_name, "flattened": flattened_vars})
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
        self.db = db
        self.parser = parser_instance
        self.symbol_cache = symbol_cache if isinstance(symbol_cache, (dict, list, set)) else {}
        self.project_root = ""
        self.all_file_records = [] 
        
        self.setWindowTitle("🛒 项目文件夹提取变量工作台")
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
            self.status_lbl.setText("🟢 离线快照加载完毕。")
        else:
            self.project_root = ""
            self.status_lbl.setText("⚠️ 提示：请绑定有效的工程根目录！")

    def select_project_directory(self):
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "选择嵌入式工程源码的绝对根路径", self.project_root)
        if path:
            self.project_root = os.path.normpath(path)
            pdb = ParamDB()
            pdb.save_config("last_source_project_root", self.project_root)
            pdb.close()
            self.execute_async_parse_engine(force_refresh=False)

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
            if '.' in path or '[' in path: continue 
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
                if kw in item['path'].lower() or kw in item['type'].lower(): 
                    filtered_flat_pool.append(item)
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