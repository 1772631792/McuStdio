# struct_parser.py
import re
import struct

TYPE_SIZES = {
    "char": 1, "uint8_t": 1, "int8_t": 1, "uint8": 1, "int8": 1,
    "uint16_t": 2, "int16_t": 2, "uint16": 2, "int16": 2,
    "uint32_t": 4, "int32_t": 4, "uint32": 4, "int32": 4, "int": 4,
    "float": 4, "double": 8
}

QUALIFIER_PATTERN = re.compile(r'\b(?:const|volatile|static|extern|register|inline|__inline|__inline__|__packed|packed)\b')
ATTRIBUTE_PATTERN = re.compile(r'__attribute__\s*\(\([^\)]*\)\)')

class StructParser:
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
        self.structs.clear()
        self.enums.clear()
        code = self.remove_comments(code)
        
        # 提取枚举
        enum_matches = re.findall(r'typedef[\s\xa0]+enum[\s\xa0]*\{.*?\}(?:\s*__\w+__)?[\s\xa0]*(\w+)[\s\xa0]*;', code, flags=re.S)
        for enum_name in enum_matches:
            self.enums.add(enum_name.strip())

        # 提取 typedef struct ... 名称;
        typedef_matches = re.findall(r'typedef[\s\xa0]+struct(?:[\s\xa0]+\w+)?[\s\xa0]*\{(.*?)\}[\s\xa0]*(\w+)[\s\xa0]*;', code, flags=re.S)
        for body, struct_name in typedef_matches:
            members = []
            statements = body.split(';')
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt: continue
                if '(' in stmt or ')' in stmt: continue
                m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
                if not m: continue
                ctype = self.normalize_type(m.group(1).strip())
                vars_list = m.group(2).strip().split(',')
                for var in vars_list:
                    var = var.strip()
                    count = 1
                    arr = re.search(r'\[(\d+)\]', var)
                    if arr: count = int(arr.group(1))
                    name = re.sub(r'\[.*?\]', '', var).replace('*', '').strip()
                    members.append({"type": ctype, "name": name, "count": count})
            self.structs[struct_name] = members

        # 提取普通 struct 定义
        struct_matches = re.findall(r'struct[\s\xa0]+(\w+)[\s\xa0]*\{(.*?)\}[\s\xa0]*;', code, flags=re.S)
        for struct_name, body in struct_matches:
            if struct_name in self.structs: continue
            members = []
            statements = body.split(';')
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt: continue
                if '(' in stmt or ')' in stmt: continue
                m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
                if not m: continue
                ctype = self.normalize_type(m.group(1).strip())
                vars_list = m.group(2).strip().split(',')
                for var in vars_list:
                    var = var.strip()
                    count = 1
                    arr = re.search(r'\[(\d+)\]', var)
                    if arr: count = int(arr.group(1))
                    name = re.sub(r'\[.*?\]', '', var).replace('*', '').strip()
                    members.append({"type": ctype, "name": name, "count": count})
            self.structs[struct_name] = members

    def remove_struct_definitions(self, code):
        code = re.sub(r'typedef[\s\xa0]+struct[\s\xa0]*\{.*?\}[\s\xa0]*\w+[\s\xa0]*;', '', code, flags=re.S)
        code = re.sub(r'typedef[\s\xa0]+enum[\s\xa0]*\{.*?\}[\s\xa0]*\w+[\s\xa0]*;', '', code, flags=re.S)
        return code

    def parse_variables(self, code):
        def strip_initializer_blocks(text):
            result = []
            brace_level = 0
            for ch in text:
                if ch == '{':
                    brace_level += 1
                    continue
                if ch == '}':
                    if brace_level > 0:
                        brace_level -= 1
                    continue
                if brace_level == 0:
                    result.append(ch)
            return ''.join(result)

        self.variables.clear()
        code = self.remove_comments(code)
        code = re.sub(r'^\s*#.*$', '', code, flags=re.M)
        code = self.remove_struct_definitions(code)
        statements = code.split(';')
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt: continue
            if '(' in stmt or ')' in stmt: continue
            stmt = strip_initializer_blocks(stmt)
            stmt = QUALIFIER_PATTERN.sub('', stmt)
            stmt = ATTRIBUTE_PATTERN.sub('', stmt)
            stmt = re.sub(r'\s+', ' ', stmt).strip()
            if not stmt: continue
            m = re.match(r'(.+?)[\s\xa0]+(.+)', stmt)
            if not m: continue
            ctype = self.normalize_type(m.group(1).strip())
            vars_list = m.group(2).strip().split(',')
            for var in vars_list:
                var = var.strip()
                if not var or var.startswith('.'):
                    continue
                var = var.split('=')[0].strip()
                if not var: continue
                count = 1
                arr = re.search(r'\[(\d+)\]', var)
                if arr: count = int(arr.group(1))
                name = re.sub(r'\[.*?\]', '', var).replace('*', '').strip()
                if not name: continue
                self.variables.append({"type": ctype, "name": name, "count": count})

    def get_alignment(self, ctype):
        # 枚举在标准32位MCU下默认为 4 字节对齐
        if ctype in self.enums or ctype.replace("_t", "") in self.enums:
            return 4
        if ctype in TYPE_SIZES:
            return TYPE_SIZES[ctype]
        if ctype in self.structs:
            max_align = 1
            for member in self.structs[ctype]:
                align = self.get_alignment(member['type'])
                if align > max_align:
                    max_align = align
            return max_align
        return 4

    def sizeof(self, ctype):
        if ctype in self.enums or ctype.replace("_t", "") in self.enums: 
            return 4
        if ctype in TYPE_SIZES:
            return TYPE_SIZES[ctype]
        if ctype in self.structs:
            total = 0
            max_align = self.get_alignment(ctype)
            for member in self.structs[ctype]:
                m_type = member['type']
                count = member['count']
                m_size = self.sizeof(m_type)
                m_align = self.get_alignment(m_type)
                
                # 严格按照当前数组成员对齐要求前移
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
            
            # ⚡ 关键修复：计算扁平化绝对偏移量时，强制进行边界对齐前移补齐
            if offset % m_align != 0:
                offset += (m_align - (offset % m_align))
                
            if m_type in self.structs:
                if count == 1:
                    path = f"{prefix}{m_name}"
                    result.append({"path": path, "type": m_type, "offset": offset, "level": path.count('.') + path.count('[')})
                    result.extend(self.flatten_struct(m_type, prefix=path + ".", base_offset=offset))
                    offset += m_size
                else:
                    result.append({"path": f"{prefix}{m_name}", "type": f"{m_type}[{count}]", "offset": offset, "level": prefix.count('.') + prefix.count('[')})
                    for i in range(count):
                        arr_offset = offset + i * m_size
                        path = f"{prefix}{m_name}[{i}]"
                        result.append({"path": path, "type": m_type, "offset": arr_offset, "level": path.count('.') + path.count('[')})
                        result.extend(self.flatten_struct(m_type, prefix=path + ".", base_offset=arr_offset))
                    offset += m_size * count
            else:
                if count == 1:
                    path = f"{prefix}{m_name}"
                    result.append({"path": path, "type": m_type, "offset": offset, "level": path.count('.') + path.count('[')})
                    offset += m_size
                else:
                    a_path = f"{prefix}{m_name}"
                    result.append({"path": a_path, "type": f"{m_type}[{count}]", "offset": offset, "level": a_path.count('.') + a_path.count('[')})
                    for i in range(count):
                        path = f"{a_path}[{i}]"
                        result.append({"path": path, "type": m_type, "offset": offset + i * m_size, "level": path.count('.') + path.count('[')})
                    offset += m_size * count
        return result

    def flatten_variable(self, var):
        result = []
        v_type, v_name, count = var['type'], var['name'], var['count']
        t_size = self.sizeof(v_type)
        if v_type in self.structs:
            if count == 1:
                result.append({"path": v_name, "type": v_type, "offset": 0, "level": 0})
                result.extend(self.flatten_struct(v_type, prefix=v_name + ".", base_offset=0))
            else:
                result.append({"path": v_name, "type": f"{v_type}[{count}]", "offset": 0, "level": 0})
                for i in range(count):
                    base = i * t_size
                    path = f"{v_name}[{i}]"
                    result.append({"path": path, "type": v_type, "offset": base, "level": 1})
                    result.extend(self.flatten_struct(v_type, prefix=path + ".", base_offset=base))
        else:
            if count == 1:
                result.append({"path": v_name, "type": v_type, "offset": 0, "level": 0})
            else:
                result.append({"path": v_name, "type": f"{v_type}[{count}]", "offset": 0, "level": 0})
                for i in range(count):
                    result.append({"path": f"{v_name}[{i}]", "type": v_type, "offset": i * t_size, "level": 1})
        return result

    def flatten_all(self):
        all_items = []
        for var in self.variables:
            all_items.extend(self.flatten_variable(var))
        return all_items