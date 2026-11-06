# MCU Live Watch Studio

MCU Live Watch Studio 是面向 Cortex-M 单片机的实时变量观测、波形分析和固件烧录工具。它通过 SEGGER J-Link DLL 或 OpenOCD 直接读写 MCU 内存，不要求固件额外实现串口协议，也不依赖 Keil、Ozone 等 IDE 的断点会话，适合电机控制、数字电源、传感器、PID 调参和现场状态观测。

## 界面示例

![MCU Live Watch Studio 新版调试界面](assets/screenshots/main-window.png)

示例使用 OpenOCD 在线读取 ELF 全局变量，将一个 `float` 变量绑定到 CH1，并在 MCU 持续运行时显示实时波形。底部同时显示通道值、烧录状态和后端在线状态。

## 为什么使用它

- **无需修改通信协议**：直接根据 ELF/DWARF 定位变量，不占用 UART、CAN 或应用层带宽。
- **尽量保持目标持续运行**：普通监视不使用断点，适合观察控制环、传感器和实时状态量。
- **一套界面支持多种探针**：J-Link 原生后端追求速度和稳定性；OpenOCD 后端兼容 ST-Link、DAPLink/CMSIS-DAP 及自定义 cfg。
- **从变量到波形一步完成**：搜索 ELF 变量、加入监视、绑定通道、冻结回看和导出 CSV 都在同一工作区完成。
- **界面与硬件任务隔离**：采样、表格读取、烧录和后端重连在后台执行；波形采用峰值保留降采样，长时间运行不让绘图点数无限增长。
- **操作结果清楚可见**：底部状态条显示连接状态和烧录进度，成功显示 `DOWNLOAD OK`，失败信息保留在进度区提示中。

## 功能特性

- 从 ELF 符号表和 DWARF 读取全局变量、文件内 `static` 全局变量、结构体、数组和枚举。
- 固定三个调试窗口；双击标签可弹出独立悬浮窗口，关闭后自动回到原标签。
- 表格低速监视和标量写入；三个波形 CH 均可绑定多个高速变量，支持单图、双图和三图布局。
- 自定义采样率、锁定滚动、自适应画布、峰值保留绘图和 CSV 数据导出。
- J-Link 原生和 OpenOCD 后端均支持后台 ELF 烧录、校验、复位和自动恢复调试连接。
- 断开、重连、手动复位和紧凑烧录进度条集中在工作站顶部与底部状态区。
- OpenOCD 支持 ST-Link、DAPLink/CMSIS-DAP、J-Link adapter，以及自选 interface/target cfg。
- 常用 STM32 F1/F2/F3/F4/H7 target 可直接选择，其他 MCU 可使用厂商提供的 cfg。
- ELF 更新后自动重新提取符号并刷新已保存变量地址。
- 低速表格读取和后端重连使用独立线程，硬件超时不会阻塞整个窗口。
- 深色/浅色主题、静音应用提示框、清晰复选框和高对比度下拉箭头。
- 配置、窗口布局和监视变量保存到程序同级的 `live_watch_history.db`。

## 支持方式

| 后端 | 推荐探针 | 主要用途 | 说明 |
| --- | --- | --- | --- |
| J-Link 原生 | SEGGER J-Link | 变量读写、波形、复位、ELF 烧录 | 使用 SEGGER DLL 和原始设备名，Windows 下优先推荐 |
| OpenOCD | ST-Link | 变量读写、波形、复位、ELF 烧录 | 选择正确的 interface 和 target cfg |
| OpenOCD | DAPLink/CMSIS-DAP | 变量读写、波形、复位、ELF 烧录 | 选择 CMSIS-DAP interface 和芯片 target cfg |
| OpenOCD | J-Link | 变量读写、波形、复位、ELF 烧录 | Windows 下需要 libusb 驱动，与 SEGGER 官方驱动通常不兼容 |

## 快速开始

### 1. 从源码运行

需要 Python 3.10 或更高版本。

```powershell
python -m pip install PyQt6 pyqtgraph numpy pyelftools pylink-square
python .\main.py
```

也可以直接运行 `dist/Mcu_Studio.exe`。首次启动时若同级目录没有数据库，程序会自动创建 `live_watch_history.db`。

### 2. 连接目标板

1. 选择包含调试信息的 `.elf` 文件，建议固件编译时保留 DWARF 信息。
2. 选择硬件后端。
3. J-Link 模式：选择 SEGGER 安装目录，并在芯片选择器中使用准确的原始设备名。
4. OpenOCD 模式：选择 `openocd.exe`、调试器 interface cfg 和与芯片匹配的 target cfg。
5. 可按需勾选“在独立窗口显示 OpenOCD 日志”，然后点击“连接并进入工作站”。OpenOCD 连接或重连可能复位 MCU，具体行为取决于 target cfg、探针和接线。

仓库自带 OpenOCD，默认路径为 `runtime/OpenOCD/bin/openocd.exe`。常用下拉框只显示程序确认存在的配置；列表中没有的探针或芯片，点击右侧浏览按钮选择任意厂商 `.cfg` 即可。程序会把自选 cfg 所在目录加入 OpenOCD 搜索路径，使其可以继续引用同目录脚本。

### 3. 添加和观察变量

- 已知变量名或物理地址时，可在顶部输入后点击“添加监视”。物理地址示例：`0x20000100`。
- 不确定名称时，打开“ELF 变量浏览器”搜索。双击变量会添加到主界面当前选中的调试窗口；右键可明确添加到主调试窗口、调试窗口 2 或调试窗口 3。大型 ELF 单次最多渲染 2000 行，可继续输入关键字缩小范围。
- 文件内 `static` 全局变量只要保留在 ELF/DWARF 中也能读取；同名变量会使用可区分的内部名称。
- 在变量表中右键标量，可加入 CH1、CH2 或 CH3；每个 CH 可容纳多个变量，同一变量跨 CH 显示时底层只采样一次。
- 双击可编辑的标量值可写回 MCU。写入会立即改变目标内存，请先确认变量类型和设备安全状态。
- 双击“主调试窗口 / 调试窗口 2 / 调试窗口 3”标签后，该标签会从主窗口移除，完整变量页进入独立悬浮窗口；关闭悬浮窗口会按原顺序恢复标签。

### 4. 波形与运行控制

- 采样速率支持预设值和自定义值；显示的 kHz 是目标采样率，实际速率取决于探针、SWD 时钟、变量数量和地址连续性。
- “暂停/继续”用于冻结或恢复波形显示，“适配”立即匹配当前数据范围。
- 空格键为全局波形开关，只用于暂停或继续波形显示；十字光标用于回看时间戳和数值。
- “断开后端”释放硬件连接，“通信重连”重新建立当前后端。
- 点击“烧录”会立即暂停采样并在后台写入当前 ELF，不再弹出二次确认；完成后底部显示 `DOWNLOAD OK`，随后恢复调试连接。烧录会擦写 Flash 并复位 MCU。
- 点击“导出 CSV”，选择已绑定的通道变量，即可导出当前缓存中的相对时间和变量值；导出使用快照，不会暂停实时采样。
- 正常关闭或断开后端不会主动烧录；OpenOCD 的连接/重连是否复位取决于 target cfg。烧录期间不要拔插探针或强制结束程序。

## OpenOCD 与 J-Link 注意事项

- J-Link 原生后端和 OpenOCD J-Link adapter 不是同一套驱动链路。前者使用 SEGGER 官方 DLL，后者使用 libusb。
- Windows 下出现 `LIBUSB_ERROR_NOT_SUPPORTED` / `No J-Link device found` 时，应切换到“J-Link 原生内核驱动”，或改用 ST-Link/DAPLink。不要为此随意用 Zadig 替换 J-Link 官方驱动。
- cfg 存在不等于芯片配置正确。PY32/N32 等型号应使用厂商提供且与芯片完全匹配的 target cfg，不要用 STM32 cfg 猜测替代。
- OpenOCD 加载 target cfg 时可能初始化或复位目标。电机和功率设备请先断开执行机构，在低风险板卡上验证。
- 应用只在本机回环地址启用 TCL 6666，并在连接后验证 OpenOCD 身份；端口被占用时会拒绝连接旧实例。

## 大工程与长期运行

- 变量浏览器搜索有防抖和显示上限，避免大型 ELF 阻塞界面。
- 低速监视只轮询当前页中已展开且实际可见的标量，并在独立线程读取硬件。
- OpenOCD 连续字节读取会合并，高速数据按时间批量发送到界面。
- 每个高速变量的历史缓存有上限，避免运行时间增长导致内存无限增加。
- 波形显示最多保留约 4000 个峰值特征点参与绘制，完整缓存仍用于 CSV 导出。
- OpenOCD/J-Link 重连在后台执行，USB 超时或 OpenOCD 启动等待不会冻结主界面。

## 全项目校验

以下命令不连接探针，也不会读写 MCU：

```powershell
python .\tools\validate_project.py
```

它检查 Python 编译与导入、ELF 表达式、TCL 分包和大小限制、OpenOCD cfg 发现/参数/读取解码、J-Link DLL 位数与原始设备名、Qt 单绑定以及 Git 空白错误。

## 打包

打开 `build.py`，修改顶部 `APP_NAME`（不要带 `.exe`），然后运行：

```powershell
python -m pip install pyinstaller
python .\build.py
```

成品固定输出到项目根目录的 `dist/`，例如 `dist/Mcu_Studio.exe`；中间文件位于 `artifacts/build/`。打包脚本强制使用 PyQt6，并排除 PySide6、PyQt5 等第二套 Qt，构建完成后还会检查 EXE，发现多套 Qt 时直接失败。不要使用 `doc/archive/packaging/` 中的历史 spec。

只面向已安装 SEGGER 软件的 J-Link 用户时可只分发 EXE；需要自带 OpenOCD 时，还应同时发布 `runtime/OpenOCD`。程序会在 EXE 同级创建数据库。若需要携带已有配置，则把 `live_watch_history.db` 与 EXE 放在同一目录。

## 项目结构

```text
McuStdio/
├─ main.py                  # 唯一应用入口
├─ build.py                 # 打包入口，可修改 APP_NAME
├─ live_watch_history.db   # 本机运行配置，与入口/EXE 同级
├─ mcu_studio/             # 核心、后台硬件任务和界面
├─ assets/                 # 图标和文档截图
├─ runtime/                # 随仓库发布的 OpenOCD、附加 cfg、SVD
├─ packaging/              # 当前 PyInstaller 配置
├─ tools/                  # 校验与辅助工具
├─ doc/                    # 文档、已知问题和历史归档
├─ dist/                   # 打包成品（Git 忽略）
└─ artifacts/              # 可重新生成的构建缓存
```

更多资料见 [文档目录](doc/README.md)。其中包含后端连接、变量与波形、烧录安全、数据库迁移、构建发布、故障排查和项目维护等完整用法。

## 内置工具链与许可

`runtime/` 跟随开源仓库提交，当前包含 OpenOCD 0.12.0、附加 cfg 和 SVD 资源。ELF/DWARF 由 `pyelftools` 直接解析，项目不需要 ARM GCC、GDB、NM、objdump 或 readelf，因此不再携带体积庞大的交叉编译工具链。第三方组件保留各自许可证；发布前请阅读 `doc/03-第三方工具链.md`。
