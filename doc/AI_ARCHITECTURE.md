# MCU Live Watch Studio — AI 协作者代码地图

> 读者：接手本项目的其他 AI / 新同学。
> 目标：用最少的时间建立"改哪里、谁调谁、什么不能碰"的整体认知。
> 本文件描述**重构后的结构**。这次重构只搬运代码、生成 import、拆分文件，**没有改变任何运行时行为**（唯一例外见第 9 节关闭路径，那是重构前就有的独立修复）。
> 使用建议：按符号名检索，不要依赖行号（行号会随修改漂移）。

---

## 1. 入口与构建边界

| 位置 | 作用 | 约束 |
| --- | --- | --- |
| `main.py` | 唯一进程入口；`--self-test` 走离屏自检 | 外部文件，本次重构未改动 |
| `mcu_studio/__init__.py` | 延迟导入 `mcu_studio.ui.run_application`，包导入时不加载 Qt | 保持延迟导入 |
| `mcu_studio/ui.py` | **门面（facade）**：只做 re-export + `__all__` | `build.py` 的 `REQUIRED_SOURCE_FILES` 强制要求该文件存在，**不可删除/改名/变成包** |
| `build.py` | PyInstaller 单文件构建 + 打包自检 | 外部文件，未改动；不读取新增模块清单 |
| `packaging/Mcu_Studio.spec` | PyInstaller `Analysis` 从 `main.py` 静态追踪依赖 | 新增模块必须使用**静态** `from .x import y`，禁止 `importlib` 动态加载，否则打包漏模块 |
| `tools/validate_project.py` | 无硬件回归校验（含离屏构造窗口） | 通过 `ui.<name>` 访问公开名字，门面必须一直 re-export 这些名字 |

调用链：

```
main.py
└─ mcu_studio.run_application()            # __init__.py 延迟导入
   └─ mcu_studio.ui.run_application()      # ui.py 门面
      └─ mcu_studio.ui_parts/window/window.run_application()
```

`run_application()` 的职责：创建 `QApplication` → 应用主题 `DARK_STYLE` → 打开 `LiveWatchDB` → `LoginConfigDialog.exec()` → 连接成功则构造 `MCUStudioWindow` 并 `show()`；窗口通过 `session_closed("config" | "exit")` 决定回到配置页还是退出进程。

---

## 2. 模块分层与依赖方向

依赖只能从下层指向上层（箭头 = import 方向），**不允许反向、不允许循环**。

```
mcu_studio/
├─ __init__.py          # 包入口，延迟导入 run_application
├─ core.py              # ELF 数据库、配置、探针发现、OpenOCD 进程
├─ spectrum.py          # FFT 计算
├─ workers.py           # 全部 QThread 后台任务
├─ ui.py                # 门面（re-export；build.py 要求此文件存在）
└─ ui_parts/
   ├─ __init__.py
   ├─ common.py         # 统一导入层（标准库/Qt/core/workers/spectrum）+ 常量
   ├─ theme.py          # QSS 样式（纯字符串）
   ├─ widgets.py        # 主题化基础控件与模块级别名
   ├─ dialogs.py        # ELF 变量、J-Link 器件、解析器对话框
   ├─ sliders.py        # 滑条辅助函数与滑条控件
   ├─ login.py          # 登录 / 配置窗口
   └─ window/
      ├─ __init__.py
      ├─ window.py      # MCUStudioWindow 组合 + run_application
      ├─ lifecycle.py   # 构造、init_ui、标签页、键盘事件
      ├─ elf.py         # ELF 解析、符号解析、内存访问
      ├─ backend.py     # 连接/重连/烧录/心跳
      ├─ workspace.py   # 布局持久化、变量与 ELF 同步
      ├─ sampling.py    # 采样线程、历史落库、表格刷新
      ├─ variables.py   # 变量树操作、通道绑定、滑条、写值
      ├─ plot.py        # 波形/FFT/测量/绘图菜单
      └─ close.py       # 关闭路径
```

依赖方向（只能向下，禁止反向或循环）：

```
ui.py
└─ ui_parts/window/window.py          # 组合 Mixin + 信号 + 类常量
   ├─ ui_parts/window/*.py            # 8 个 Mixin，互不 import，只通过 self 调用
   ├─ ui_parts/login.py
   ├─ ui_parts/dialogs.py
   ├─ ui_parts/sliders.py
   ├─ ui_parts/widgets.py
   ├─ ui_parts/theme.py
   └─ ui_parts/common.py → core.py / workers.py / spectrum.py / PyQt6 / pylink
```

| 层 | 文件 | 约行数 | 职责 | 允许依赖 |
| --- | --- | --- | --- | --- |
| 0 | `ui_parts/common.py` | 67 | 标准库、PyQt6、`core`、`workers`、`spectrum` 的统一导入；`FIRMWARE_FILE_FILTER`；`pg.setConfigOptions`；`pylink` 可选导入 | 标准库、PyQt6、pyqtgraph、core、workers、spectrum |
| 0 | `ui_parts/theme.py` | 162 | `DARK_STYLE` / `LIGHT_STYLE` / `POPUP_MENU_STYLE` / `DARK_POPUP_MENU_STYLE`；`application_theme_stylesheet()` | 无（纯字符串） |
| 1 | `ui_parts/widgets.py` | 424 | 主题化基础控件：`SilentMessageBox`、`HighContrastComboBox`、`PathComboBox`、`HighContrastCheckBox`、`RoundedMenu`、`DetachableTabBar`、`DebugVariableTree`、`FloatingDebugWindow`、`InPlaceEditLineEdit`；模块级别名 `QMessageBox` / `QComboBox` / `QCheckBox` | common、theme |
| 2 | `ui_parts/dialogs.py` | 541 | `ElfVariableDialog`（ELF 变量选择）、`JLinkDeviceDialog`（器件选择）、`AdvanceCParserDialog` | common、theme、widgets |
| 2 | `ui_parts/sliders.py` | 663 | `automatic_slider_profile`、`variable_write_available`、`rotating_batch`、`InlineVariableSlider`、`VariableSliderRow`、`VariableSliderDialog` | common、theme、widgets |
| 3 | `ui_parts/login.py` | 725 | `LoginConfigDialog`：配置档、J-Link / OpenOCD 路径、探针扫描、主题 | common、theme、widgets、dialogs |
| 4 | `ui_parts/window/lifecycle.py` | 757 | `_WindowLifecycleMixin`：构造、`init_ui`、标签页增删/拖出、键盘与事件过滤 | 下层全部 |
| 4 | `ui_parts/window/elf.py` | 299 | `_WindowElfMixin`：ELF/AXF 解析、符号解析、内存读写、TCL 命令 | 下层全部 |
| 4 | `ui_parts/window/backend.py` | 489 | `_WindowBackendMixin`：连接/重连/断开、烧录、复位、心跳 | 下层全部 |
| 4 | `ui_parts/window/workspace.py` | 425 | `_WindowWorkspaceMixin`：布局、工作区持久化、已保存变量与 ELF 同步、CSV 导出 | 下层全部 |
| 4 | `ui_parts/window/sampling.py` | 335 | `_WindowSamplingMixin`：采样线程、历史落库、低速表格刷新 | 下层全部 |
| 4 | `ui_parts/window/variables.py` | 572 | `_WindowVariablesMixin`：变量树右键菜单、通道绑定、内联滑条、RAM 写入队列 | 下层全部 |
| 4 | `ui_parts/window/plot.py` | 991 | `_WindowPlotMixin`：波形/FFT 绘制、测量、十字光标、绘图右键菜单 | 下层全部 |
| 4 | `ui_parts/window/close.py` | 173 | `_WindowCloseMixin`：强制退出、优雅关闭、有界重试 | 下层全部 |
| 5 | `ui_parts/window/window.py` | 96 | 组合 `MCUStudioWindow`（信号 + 类常量 + MRO）与 `run_application()` | 全部 Mixin、common、theme、login |
| 6 | `ui.py` | 298 | 门面：显式 re-export 上述公开名字 + `__all__` | 上述全部 |

> 行数只是量级参考，用于判断"这段代码大概在哪一层"。

---

## 3. 主窗口的组合方式（重要）

```python
class MCUStudioWindow(
    _WindowLifecycleMixin,      # __init__ / init_ui / 标签页 / 键盘
    _WindowElfMixin,
    _WindowBackendMixin,
    _WindowWorkspaceMixin,
    _WindowSamplingMixin,
    _WindowVariablesMixin,
    _WindowPlotMixin,
    _WindowCloseMixin,
    QMainWindow,
):
    WORKSPACE_STATE_KEY = ...
    INLINE_SLIDER_STATE_KEY = ...
    FFT_STATE_KEY = ...
    SLIDER_CONTROL_ROLE = ...
    variable_write_result = pyqtSignal(str, int, bool, str, object)
    session_closed = pyqtSignal(str)
    CLOSE_RETRY_LIMIT = 8
```

必须遵守的结构规则：

1. **`QMainWindow` 永远放在最后一个基类**。Mixin 必须排在它前面，否则 `closeEvent` / `eventFilter` / `keyPressEvent` 等 Qt 虚函数会被 `QMainWindow` 的默认实现抢先，重写失效。
2. **`pyqtSignal` 只能定义在最终类**（QObject 子类）。Mixin 是普通类，在 Mixin 里定义信号会直接报错。
3. **`__init__` 放在 `_WindowLifecycleMixin`**，其中的 `super().__init__()` 会沿 MRO 走到 `QMainWindow.__init__`；其他 Mixin 不要定义 `__init__`。
4. **Mixin 之间通过 `self.xxx` 互相调用**，禁止 `from .ui_parts/window/window import MCUStudioWindow`（循环导入 + 拆分类名耦合）。
5. 唯一需要类级访问的地方已改造：`_format_low_speed_value` 是 `@classmethod`，使用 `cls.decode_value(...)`。

Mixin 职责速查：

| Mixin | 典型方法（检索关键字） |
| --- | --- |
| `_WindowLifecycleMixin` | `__init__`、`init_ui`、`create_new_debug_tab`、`detach_debug_tab`、`attach_debug_tab`、`keyPressEvent`、`eventFilter` |
| `_WindowElfMixin` | `reload_elf_symbols`、`_parse_elf_in_background`、`check_elf_file_changed`、`decode_value`、`read_memory_bytes`、`send_tcl_cmd` |
| `_WindowBackendMixin` | `force_reconnect_engine`、`disconnect_backend`、`flash_elf_by_user`、`handle_flash_finished`、`update_hardware_heartbeat` |
| `_WindowWorkspaceMixin` | `save_workspace_state`、`restore_workspace_state`、`handle_layout_changed`、`move_debug_variable`、`export_variable_csv` |
| `_WindowSamplingMixin` | `restart_sampling_thread`、`stop_sampling_thread`、`handle_high_speed_data`、`low_speed_tree_refresh` |
| `_WindowVariablesMixin` | `show_context_menu`、`bind_tree_item_to_channel`、`enable_inline_slider`、`queue_variable_write`、`remove_variable_root` |
| `_WindowPlotMixin` | `update_plot_canvas`、`configure_fft`、`_update_fft_plot`、`handle_plot_mouse_move`、`show_plot_context_menu` |
| `_WindowCloseMixin` | `closeEvent`、`_force_close_from_titlebar`、`_retry_close_after_workers` |

---

## 4. 启动时序

```
MCUStudioWindow.__init__            (_WindowLifecycleMixin)
├─ 状态字段初始化（backend_suspended=True，尚未占用探针）
├─ init_ui()                        # 构建控件、绘图、定时器、信号连接
├─ VariableWriteWorker(...)         # 常驻写线程
├─ restore_layout_and_type_configs()
├─ timer_table(200ms) / timer_plot(30ms) / timer_heartbeat(300ms)
├─ elf_watch_timer(1000ms)
├─ singleShot(0, _start_initial_elf_load)        # 先显示界面再解析
└─ singleShot(0, _start_initial_backend_connection)
   └─ force_reconnect_engine(initial=True)       # 后台线程连接，失败不影响界面

run_application()
├─ LoginConfigDialog.exec()
└─ 连接成功 → MCUStudioWindow(config, db).show()
```

要点：**窗口先显示，ELF 解析和后端连接都在后台线程**。`backend_suspended` 初始为 `True`，只有连接成功后才变 `False` 并启动采样。

---

## 5. 线程模型

所有耗时/可能阻塞的硬件操作都在 `QThread` 子类里（`workers.py`）：

| Worker | 触发方 | 结果信号 | 说明 |
| --- | --- | --- | --- |
| `DebuggerDiscoveryWorker` | 登录窗口"自动检测" | `progress_signal` / `finished_signal` | 全盘扫描 J-Link / OpenOCD |
| `ElfParseWorker` | 初始加载 / 重新提取 | `result_ready` | 后台解析 ELF/DWARF |
| `HighSpeedSamplingWorker` | `restart_sampling_thread` | `data_received(list)` / `link_lost(str)` | 采样主循环，批次 512 点或 25ms 刷一次 |
| `LowSpeedReadWorker` | `low_speed_tree_refresh` | `finished_signal(dict, str)` | 表格可见行的低速读取 |
| `VariableWriteWorker` | `queue_variable_write` | `finished_signal` | 常驻，按变量 key 合并待写值 |
| `ElfFlashWorker` | `_start_pending_flash` | `finished_signal(bool, str, link)` | 调用 `JLink.exe` |
| `OpenOcdFlashWorker` | `_flash_elf_with_openocd` | `finished_signal(bool, str, bool)` | `program ... verify reset exit` |
| `BackendCloseWorker` | `_close_native_link_async` | `finished_signal(str)` | 关闭旧 J-Link 句柄 |
| `BackendReconnectWorker` | `_start_backend_reconnect` | `finished_signal(bool, str, backend)` | 新建后端 |
| `SampleHistoryWriter` | `handle_high_speed_data` | `error_signal` | 批量写 SQLite，环形裁剪 100 万行 |

共享状态与保护：

- `jlink_lock`（`threading.RLock`）：保护**所有** J-Link 原生调用。采样、低速读、写值、关闭、重连都走这把锁。
- `buffer_mutex`（`QMutex`）：保护 `ch_buffers` / `ch_time_stamps`。
- `retired_workers`：停止超时后仍存活的 worker，**保留引用直到其 `finished`**，避免析构运行中的 QThread 导致崩溃。
- `backend_close_workers`：后台关闭句柄的 worker 列表。

> 已知风险（**本次不修改**）：`jlink_lock` 没有超时；厂商 DLL 调用可能长时间不返回，导致关闭/重连排队。GUI 线程中的 `trigger_manual_mcu_reset()` 仍直接调用 `reset()`。这些都是行为层改造，需单独评审。

---

## 6. 采样与波形数据流

```
HighSpeedSamplingWorker.run()                # 后台线程
├─ 按通道地址分组（build_contiguous_read_blocks）
├─ J-Link: memory_read8/16/32（持 jlink_lock）
├─ OpenOCD: mdw/mdh/mdb 合并命令（TCL socket）
└─ data_received(batch)  ── Qt 队列连接 ──►  GUI 线程
   └─ MCUStudioWindow.handle_high_speed_data(batch)
      ├─ buffer_mutex 内：
      │  ├─ latest_values[path] = value
      │  ├─ 每 5ms 采样一次写入 history_rows
      │  └─ ch_buffers[i].append(value)（>40000 点裁剪到 25000）
      └─ history_writer.submit(history_rows)   # SQLite 后台落库

QTimer timer_plot (30ms) ─► update_plot_canvas()
                           ├─ _display_plot_data()（最多 4000 点/通道）
                           └─ set_plot_mode / _update_fft_plot（FFT 模式）

QTimer timer_table (200ms) ─► low_speed_tree_refresh()
                             └─ LowSpeedReadWorker ──► _finish_low_speed_read()
                                 └─ 更新可见单元格 / 内联滑条目标值

QTimer timer_heartbeat (300ms) ─► update_hardware_heartbeat()
                                  └─ 判定在线状态、LED、状态栏文案
```

FFT 相关逻辑在 `ui_parts/window/plot.py`（`configure_fft`、`_update_fft_plot`）+ `spectrum.py`（纯计算）。

---

## 7. 变量树、滑条与 RAM 写入

```
变量树（DebugVariableTree / FloatingDebugWindow 内的树）
├─ 双击/右键 ─► _WindowVariablesMixin
│  ├─ add_elf_variable / quick_add_variable / remove_variable_root
│  ├─ bind_tree_item_to_channel ─► ch_paths / ch_addrs / ch_types
│  └─ enable_inline_slider ─► InlineVariableSlider（写值经 VariableWriteWorker）
├─ 表格刷新 ─► low_speed_tree_refresh（只读可见行，rotating_batch(32) 轮转）
└─ 内联滑条拖动 ─► queue_variable_write(path, value)
                  ├─ variable_write_spec() 计算地址/宽度/符号
                  └─ VariableWriteWorker.submit(key, addr, data)  # 同 key 覆盖旧值
                     └─ memory_write8/16/32（持 jlink_lock）
                        └─ finished_signal ─► _variable_write_finished()
```

数据库角色（`core.LiveWatchDB`）：配置档、已保存变量映射、波形历史、工作区状态。

---

## 8. 后端状态机

关键字段：

| 字段 | 含义 |
| --- | --- |
| `backend_suspended` | `True` 表示不读写目标（未连接/已断开/烧录中） |
| `jlink_native_instance` | `pylink.JLink` 实例（J-Link 模式） |
| `tcl_socket` / `openocd_link_alive` | OpenOCD TCL 通道 |
| `reconnect_worker` | 正在进行的后台重连 |
| `backend_close_workers` | 正在释放旧句柄的 worker |
| `retired_workers` | 停止超时但仍在运行的 worker |
| `flash_sequence_active` | 烧录流程占位（禁止并发烧录/断开） |
| `sampling_restart_pending` | 采样线程未及时退出，待其结束后重启 |

典型流转：

```
连接成功
  backend_suspended=False → restart_sampling_thread() → init_global_link_backend()
断开（用户）
  disconnect_backend() → 停采样 → 关 TCL/停 OpenOCD → _close_native_link_async()
链路丢失（采样线程 link_lost）
  _sampling_link_lost() → _handle_backend_connection_lost() → disconnect_backend()
重连（用户）
  force_reconnect_engine()
  ├─ 停采样、禁用按钮
  ├─ J-Link：_close_native_link_async(old_link, next_action="reconnect")
  │           └─ _native_link_closed() → _start_backend_reconnect()
  └─ OpenOCD：_start_backend_reconnect()
  └─ _backend_reconnect_finished() → _finish_backend_reconnect() → restart_sampling_thread()
烧录（J-Link）
  flash_elf_by_user()
  ├─ 停采样、释放句柄（next_action="flash"）
  ├─ _start_pending_flash() → ElfFlashWorker(JLink.exe)
  └─ handle_flash_finished() → reload_elf_symbols(source="flash")
烧录（OpenOCD）
  _flash_elf_with_openocd() → OpenOcdFlashWorker → handle_openocd_flash_finished()
```

`update_hardware_heartbeat()` 每 300ms 检查线程/进程/句柄状态并刷新状态栏；它**不调用** J-Link 原生函数（避免 GUI 线程被 DLL 卡死）。

---

## 9. 关闭路径（重构前已有的修复，保持现状）

```
closeEvent（_WindowCloseMixin）
├─ event.spontaneous()（标题栏 X / Alt+F4 / 任务栏关闭）
│  ├─ 若正在烧录：确认框，默认"否"
│  ├─ _close_destination="exit"；_returning_to_config=False
│  └─ _force_close_from_titlebar() → 保存工作区 → 停定时器/线程 → os._exit(0)
├─ 非"返回配置"意图 → 同样强制退出
└─ _returning_to_config=True（仅"返回配置"按钮）
   ├─ 烧录中/正在重连 → 提示并忽略
   ├─ 停采样（300ms）、写线程（0ms）、历史线程（500ms）
   ├─ 关闭句柄（异步）
   ├─ 仍有线程在跑 → 每 250ms 重试，最多 CLOSE_RETRY_LIMIT=8 次（约 2s）
   │                    └─ 超限 → _force_close_from_titlebar()
   └─ session_closed.emit("config") → 回配置页
```

设计意图：窗口系统关闭**永远不依赖**后台线程退出；优雅关闭有**时间上限**和**兜底强制退出**。

---

## 10. 改动前必须遵守的不变量

1. **功能语义不变**：拆分类只搬运代码；新增行为需单独提交/评审。
2. **`mcu_studio/ui.py` 必须存在**，并且继续 re-export 外部用到的名字（`MCUStudioWindow`、`LoginConfigDialog`、`QMessageBox`、`FIRMWARE_FILE_FILTER`、`SilentMessageBox`、`DebugVariableTree`、`ElfVariableDialog`、`InlineVariableSlider`、`automatic_slider_profile`、`rotating_batch` 等）。新增公开类后同步更新门面与 `__all__`。
3. **只用静态 import**（`from .ui_x import y`），满足 PyInstaller 静态追踪。
4. **`QMainWindow` 保持 MRO 最后一位**；`pyqtSignal` 只放最终类；Mixin 不定义 `__init__`。
5. **不要在 GUI 线程新增 `pylink` / 串口 / socket 调用**。所有硬件访问必须进 `QThread`，并遵守 `jlink_lock`。
6. **不要删除 `retired_workers` / `backend_close_workers` 的引用管理**，否则会在退出时崩溃。
7. 样式字符串（`ui_parts/theme.py`）不要被格式化工具重排，QSS 对多余空格/换行敏感。
8. `workers.py` 的信号名（`data_received`、`link_lost`、`finished_signal`、`error_signal`）被窗口层按名字连接，改名会静默断链。

---

## 11. 常见任务导航

| 想改什么 | 去哪里 |
| --- | --- |
| 颜色/字体/控件样式 | `ui_parts/theme.py`（`DARK_STYLE`、`LIGHT_STYLE`、`POPUP_MENU_STYLE`） |
| 登录/配置页、探针扫描 | `ui_parts/login.py`、`workers.DebuggerDiscoveryWorker` |
| 变量树右键菜单、拖拽、删除 | `ui_parts/window/variables.py`、`ui_parts/widgets.DebugVariableTree` |
| 内联滑条外观/范围 | `ui_parts/sliders.py`（`InlineVariableSlider`、`automatic_slider_profile`） |
| 采样率、批量、历史落库 | `ui_parts/window/sampling.py`、`workers.HighSpeedSamplingWorker`、`workers.SampleHistoryWriter` |
| 波形绘制、FFT、测量 | `ui_parts/window/plot.py`、`spectrum.py` |
| 烧录/重连/心跳 | `ui_parts/window/backend.py`、`workers.ElfFlashWorker` / `OpenOcdFlashWorker` / `BackendReconnectWorker` |
| ELF 解析、符号偏移 | `ui_parts/window/elf.py`、`core.ElfDatabase` |
| 布局持久化、工作区 | `ui_parts/window/workspace.py`、`core.LiveWatchDB` |
| 主窗口控件布局 | `ui_parts/window/lifecycle.py::init_ui` |
| 关闭/退出行为 | `ui_parts/window/close.py` |

---

## 12. 验证清单（每次改动后）

```bash
# 1) 语法（覆盖 ui_parts 子包）
python -m compileall -q mcu_studio

# 2) 项目回归（无硬件；含 500 组合场景、窗口离屏构造、打包约束）
python tools/validate_project.py

# 3) 打包自检（离屏构造登录窗口）
python main.py --self-test report.json
```

> 不要在 IDE 里反复运行"显示窗口再 `processEvents()`"的临时脚本：离屏模式下 pyqtgraph 布局与定时器会让进程长时间无响应。日常验证用上面三条即可。

`tools/validate_project.py` 覆盖：数据库/配置档、ELF 同步、OpenOCD 参数、J-Link 命令、打包路径、UI 离屏构造、500 组组合场景、Qt 绑定检查、diff 空白检查。任何一条失败都视为回归。

---

## 13. 现状与后续（不属于本次重构）

| 项目 | 状态 |
| --- | --- |
| `ui.py` 单文件过大 | 已拆分：门面 298 行 + 15 个职责模块 |
| 关闭流程可能永远关不掉 | 已修复（第 9 节） |
| `jlink_lock` 无超时、原生调用可能阻塞 | **待处理**（需行为层改造） |
| GUI 线程 `trigger_manual_mcu_reset()` 直接调 DLL | **待处理** |
| 重连失败使用模态 `QMessageBox.critical`，可能挡住标题栏关闭 | **待处理** |
| 采样读失败立即拆链路（探针被占用后不再自恢复） | **待处理**（可改为退避重试） |

后续处理这些问题时，优先在 `workers.py` 引入"可放弃的调用 + 看门狗"，避免把阻塞重新带回 GUI 线程。
