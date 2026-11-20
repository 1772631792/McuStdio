# MCU Live Watch Studio V2.5.5

V2.5.5 主要做代码结构整理和退出可靠性完善。变量监视、波形、FFT、滑动调节和烧录流程与 V2.4 保持一致，配置和数据库结构不变。

## 主要更新

- `mcu_studio/ui.py` 从单文件（约 6300 行）拆分为门面 + `mcu_studio/ui_parts/` 子包，共 15 个职责模块。
- UI 实现按层组织：`common`（统一导入）→ `theme` → `widgets` → `dialogs` / `sliders` → `login` → `window/*`（生命周期、ELF、后端、布局、采样、变量、绘图、关闭）。
- 主窗口 `MCUStudioWindow` 由 8 个 Mixin 组合，信号和类常量仍保留在最终类，Qt 事件重写和运行行为不变。
- 标题栏 X 不再依赖 `QCloseEvent.spontaneous()` 判断：除“返回配置”外的任何关闭都直接退出，避免误走优雅关闭而卡在数据库写锁上。
- 标题栏 X 路径不再弹模态确认框，避免弹窗被窗口遮挡后表现为“按 X 无响应”。
- 强制退出路径不写数据库、不等待任何后台线程：先停止采样标志、关闭 TCL socket、终止本软件启动的 OpenOCD，再由后台线程在 350 ms 内尽力关闭 J-Link，最后退出进程。
- 新增 `%TEMP%\mcu_shutdown_trace.log` 关闭诊断日志，记录版本、关闭来源、烧录状态和每一步时间戳。
- “返回配置”的优雅关闭保留，并带有约 2 秒重试上限和强制退出兜底。
- 新增 `AI.md` 和 `doc/AI_ARCHITECTURE.md`，记录模块关系、线程模型、采样/写值/烧录/关闭调用链和改动约束。
- `build.py` 增加 `ui_parts` 子包存在性校验，避免打包后才发现模块缺失。

## 兼容性

- 配置档、变量绑定、滑动条参数、波形历史和 CSV 导出格式不变。
- 现有 `live_watch_history.db` 可直接使用，无需迁移。
- 单文件版、内置 OpenOCD 便携版和独立 `OpenOCD.zip` 的目录结构与 V2.4 相同。

## 构建状态

- 单文件版、内置 OpenOCD 的便携版已在本地构建，PyInstaller Qt 审计和打包启动自检均通过。
- 便携版包含 `Create_Desktop_Shortcut.cmd` 和 `portable-mode.flag`。

## SHA256

- `Mcu_Studio.exe`: `4A6FE2B4067C838D8B0AB910783350D2783E2AFA850D1EEBEB44D318705810C2`
- `Mcu_Studio_Portable_v2.5.5.zip`: `1AA522B521EEFB7179F6D64607FF8997EAF087541293564E25657B10D5705A30`
- `OpenOCD.zip`: `097D535E1047B81B104B685D6A70A2AC9335C874C4013F7A7BFC3F33D5A3B6B0`
