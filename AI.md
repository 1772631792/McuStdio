# AI 协作入口（McuStudio V2.5）

> 给 AI 助手和新协作者的入口文档。需要完整细节时读 [`doc/AI_ARCHITECTURE.md`](doc/AI_ARCHITECTURE.md)。
> 本文只讲**代码在哪、谁调谁、什么不能改**，不重复用户功能说明（那部分在 [`Readme.md`](Readme.md)）。

## 1. 项目一句话

Cortex-M 实时变量监视/在线调参工具：解析 ELF/AXF 的符号与 DWARF，通过 SEGGER J-Link DLL 或 OpenOCD 直接读写 MCU 内存，提供波形、FFT、滑条写值和烧录。

## 2. 入口与构建边界

```
main.py                         # 唯一进程入口（--self-test 走离屏自检）
└─ mcu_studio.run_application() # 包级延迟导入
   └─ mcu_studio.ui.run_application()          # 门面
      └─ mcu_studio.ui_parts.window.window.run_application()
```

| 文件 | 约束 |
| --- | --- |
| `mcu_studio/ui.py` | **门面**：只做 re-export。`build.py` 强制要求它存在，不能删除/改名/改成包 |
| `build.py` / `build_portable.py` | 外部构建脚本；`packaging/Mcu_Studio.spec` 用静态 `Analysis` 追踪 import |
| `tools/validate_project.py` | 无硬件回归校验；通过 `ui.<name>` 访问公开名字 |

**新增模块必须用静态 `from .x import y`**，否则 PyInstaller 收集不到。

## 3. 目录结构

```
mcu_studio/
├─ __init__.py          # __version__、延迟导入 run_application
├─ core.py              # ELF/DWARF、数据库、配置、探针发现、OpenOCD 进程
├─ spectrum.py          # FFT 纯计算
├─ workers.py           # 全部 QThread 后台任务
├─ ui.py                # 门面（re-export + __all__）
└─ ui_parts/
   ├─ common.py         # 统一导入层（标准库/Qt/core/workers/spectrum）+ 常量
   ├─ theme.py          # QSS 样式
   ├─ widgets.py        # 主题化控件 + QMessageBox/QComboBox/QCheckBox 别名
   ├─ dialogs.py        # ELF 变量、J-Link 器件、解析器对话框
   ├─ sliders.py        # 滑条辅助函数与控件
   ├─ login.py          # 登录/配置窗口
   └─ window/
      ├─ window.py      # MCUStudioWindow（组合 Mixin + 信号 + 类常量）
      ├─ lifecycle.py   # __init__ / init_ui / 标签页 / 键盘
      ├─ elf.py         # ELF 解析、符号解析、内存/TCL 访问
      ├─ backend.py     # 连接/重连/烧录/心跳
      ├─ workspace.py   # 布局持久化、变量与 ELF 同步
      ├─ sampling.py    # 采样线程、历史落库、表格刷新
      ├─ variables.py   # 变量树、通道绑定、滑条、写值
      ├─ plot.py        # 波形/FFT/测量/绘图菜单
      └─ close.py       # 关闭路径
```

依赖只能向下：`ui.py → window/window.py → window/*.py → {login,dialogs,sliders} → widgets → {theme,common} → core/workers/spectrum`。

## 4. 主窗口的组合规则（最容易踩坑）

```python
class MCUStudioWindow(
    _WindowLifecycleMixin, ..., _WindowCloseMixin,   # Mixin 在前
    QMainWindow,                                     # 必须最后
):
    variable_write_result = pyqtSignal(...)   # 信号只能定义在最终类
    session_closed = pyqtSignal(str)
```

1. `QMainWindow` 必须在 MRO 最后，否则 `closeEvent`/`eventFilter`/`keyPressEvent` 重写失效。
2. `pyqtSignal` 只能放在 QObject 子类（最终类），Mixin 里不行。
3. Mixin 之间只用 `self.xxx` 调用，**禁止跨文件 import `MCUStudioWindow`**（循环导入）。
4. Mixin 不定义 `__init__`；`super().__init__()` 会沿 MRO 走到 `QMainWindow.__init__`。

## 5. 五条关键调用链

### 5.1 启动

```
main.py → run_application()
  → QApplication + DARK_STYLE + LiveWatchDB
  → LoginConfigDialog.exec()
  → MCUStudioWindow.__init__（lifecycle.py）
     → init_ui → 定时器 → singleShot 初始 ELF 解析 → singleShot 初始后端连接
```

### 5.2 采样到波形

```
restart_sampling_thread（sampling.py）
└─ HighSpeedSamplingWorker（workers.py）  [持 jlink_lock 读内存]
   └─ data_received → handle_high_speed_data（sampling.py）
      ├─ buffer_mutex 内写 latest_values / ch_buffers
      └─ history_writer.submit（SQLite 后台）
timer_plot(30ms) → update_plot_canvas（plot.py）→ _display_plot_data
timer_table(200ms) → low_speed_tree_refresh（sampling.py）→ LowSpeedReadWorker
```

### 5.3 变量写值

```
滑条/双击 → queue_variable_write（variables.py）
└─ VariableWriteWorker.submit（workers.py，按 key 合并）
   └─ memory_write8/16/32（持 jlink_lock）→ _variable_write_finished
```

### 5.4 烧录（J-Link）

```
flash_elf_by_user（backend.py）
├─ 停采样 + _close_native_link_async（释放句柄）
├─ _start_pending_flash → ElfFlashWorker（JLink.exe）
└─ handle_flash_finished → reload_elf_symbols（elf.py）→ restart_sampling_thread（sampling.py）
```

### 5.5 关闭

```
closeEvent（close.py）
├─ spontaneous（X/Alt+F4）→ _force_close_from_titlebar → os._exit(0)
└─ 返回配置 → 停线程（300/500ms）→ 异步关句柄
              → 仍有线程则每 250ms 重试，最多 8 次 → 强制退出
```

## 6. 硬约束（改代码前必读）

- 拆分/重构只搬代码，不改行为；行为变更单独提交。
- `mcu_studio/ui.py` 必须保留并继续 re-export 外部用到的名字（`MCUStudioWindow`、`LoginConfigDialog`、`QMessageBox`、`FIRMWARE_FILE_FILTER`、`SilentMessageBox`、`DebugVariableTree`、`ElfVariableDialog`、`InlineVariableSlider`、`automatic_slider_profile`、`rotating_batch`）。
- 不在 GUI 线程新增 `pylink`/socket/串口调用；所有硬件访问进 `QThread` 并遵守 `jlink_lock`。
- 不要删除 `retired_workers` / `backend_close_workers` 的引用管理。
- `ui_parts/theme.py` 的 QSS 字符串不要被格式化工具重排。
- `workers.py` 的信号名（`data_received`、`link_lost`、`finished_signal`、`error_signal`）被按名字连接，改名会静默断链。

## 7. 验证

```bash
python -m compileall -q mcu_studio
python tools/validate_project.py      # 约 7 秒，含 500 组组合场景
python main.py --self-test report.json
```

不要用"显示窗口 + processEvents"的临时脚本做验证：离屏模式下 pyqtgraph 布局会让进程长时间无响应。

## 8. 常见改动定位

| 想改 | 文件 |
| --- | --- |
| 样式/颜色 | `ui_parts/theme.py` |
| 基础控件、消息框 | `ui_parts/widgets.py` |
| 登录/探针扫描 | `ui_parts/login.py`、`workers.DebuggerDiscoveryWorker` |
| 主界面布局 | `ui_parts/window/lifecycle.py::init_ui` |
| 变量树/滑条/写值 | `ui_parts/window/variables.py`、`ui_parts/sliders.py` |
| 采样/历史 | `ui_parts/window/sampling.py`、`workers.HighSpeedSamplingWorker` |
| 波形/FFT | `ui_parts/window/plot.py`、`spectrum.py` |
| 烧录/重连/心跳 | `ui_parts/window/backend.py`、`workers.py` |
| ELF 解析 | `ui_parts/window/elf.py`、`core.ElfDatabase` |
| 布局持久化 | `ui_parts/window/workspace.py`、`core.LiveWatchDB` |
| 关闭/退出 | `ui_parts/window/close.py` |
