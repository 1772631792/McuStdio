# MCU Live Watch Studio V2.5

V2.5 主要做代码结构整理和退出可靠性完善。变量监视、波形、FFT、滑动调节和烧录流程与 V2.4 保持一致，配置和数据库结构不变。

## 主要更新

- `mcu_studio/ui.py` 从单文件（约 6300 行）拆分为门面 + `mcu_studio/ui_parts/` 子包，共 15 个职责模块。
- UI 实现按层组织：`common`（统一导入）→ `theme` → `widgets` → `dialogs` / `sliders` → `login` → `window/*`（生命周期、ELF、后端、布局、采样、变量、绘图、关闭）。
- 主窗口 `MCUStudioWindow` 由 8 个 Mixin 组合，信号和类常量仍保留在最终类，Qt 事件重写和运行行为不变。
- 完善标题栏 X 的退出路径：任何状态下都不再被重连或烧录线程阻塞；烧录中退出会先确认。
- “返回配置”的优雅关闭增加重试上限（约 2 秒）和强制退出兜底；关闭时在界面线程的等待从 2.7 秒降到 0.8 秒以内。
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

- `Mcu_Studio.exe`: `9B77FE29743D821BD495B7DB36D04D57F6BBB087218FB1BB5D5F69BD707EEED8`
- `Mcu_Studio_Portable_v2.5.zip`: `F016B6744CA9768664F0FC80EB266E0F5939142F2A0D516755755862EC35CA46`
- `OpenOCD.zip`: `097D535E1047B81B104B685D6A70A2AC9335C874C4013F7A7BFC3F33D5A3B6B0`
