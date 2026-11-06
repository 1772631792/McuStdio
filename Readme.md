# MCU Live Watch Studio

一个轻量的 Cortex-M 实时变量监视、波形分析与固件烧录工具。

它直接通过 ELF/DWARF 找到变量，再使用 SEGGER J-Link DLL 或 OpenOCD 读写 MCU 内存。固件不需要增加串口协议，也不需要为了观察变量而暂停正在运行的控制系统。适合电机控制、数字电源、传感器、PID 调参和现场状态分析。

![MCU Live Watch Studio 工作区](assets/screenshots/workspace-overview.png)

## 设计目标

- **保持 MCU 运行**：普通监视和波形采样不依赖断点，避免停机影响实时系统。
- **变量优先**：搜索、结构展开、实时值和波形绑定都围绕变量组织，减少无关界面。
- **配置轻量**：工作状态集中保存在一个 SQLite 文件中，复制 DB 即可切换项目配置。
- **后端解耦**：J-Link 原生和 OpenOCD 共用相同的 ELF、变量、波形与烧录流程。
- **适合嵌入现有流程**：可以继续使用 VS Code、Make/CMake 或厂商工程编译，只把本软件作为实时观察窗口。

## 主要能力

### ELF 变量浏览器

![ELF 变量浏览器](assets/screenshots/elf-variable-browser.png)

- 从 ELF 符号表和 DWARF 读取全局变量、文件内 `static` 变量、结构体、数组和枚举。
- 左侧按源文件筛选，文件搜索和顶部变量搜索均支持模糊匹配；留空时不筛选。
- “全部文件”视图优先排列最近使用的变量。
- 结构体和数组可直接懒加载展开，大型 ELF 单次最多渲染 2000 个搜索结果。
- 双击或点击添加后浏览器保持打开，可以连续加入多个变量。
- 右键可以明确添加到主调试窗口、调试窗口 2 或调试窗口 3。

调试窗口保存的是变量路径。ELF 被重新编译或烧录后，软件会重新解析地址、类型和结构成员偏移；只要变量名没有改变，就会自动跟随新的链接地址。已经改名或删除的变量不会继续读取旧地址。

### 实时波形

- CH1、CH2、CH3 是三个显示分组，每个 CH 都可以绑定多个变量。
- 同一个变量出现在多个窗口或多个 CH 时，底层只采样一次，再分发到不同视图。
- 支持单图、双图、三图布局以及自定义采样频率。
- 曲线自动分配高辨识度颜色，也可以通过右键单独修改并持久化。
- 绘图使用峰值保留降采样，保留突变特征，同时限制长期运行时的绘制点数。

![双图与波形右键菜单](assets/screenshots/waveform-context-menu.png)

波形区域右键可以完成常用操作：添加变量、控制本图通道、修改曲线颜色、自动适配、开启测量以及导出采样 CSV。界面不会显示 pyqtgraph 的内部菜单。

### 两点测量与冻结回看

![两点差值测量](assets/screenshots/waveform-measurement.png)

点击“测量”后波形自动冻结。选择两个采样点即可得到横轴时间差 `Δt` 和纵轴差值 `ΔY`，结果固定显示在底部状态栏，不遮挡曲线。

![冻结后的光标读数](assets/screenshots/waveform-cursor.png)

冻结状态下，十字线平滑跟随鼠标，数据圆点和标签吸附最近的真实采样点。空格键全局只负责暂停或继续波形，不会触发当前获得焦点的其他按钮。

### 三个调试窗口

- 固定提供三个逻辑调试窗口，每个窗口独立保存变量。
- 表格以约 5 Hz 刷新可见标量，结构体和数组支持展开。
- 双击标量值可以写回 MCU；写入前应确认类型和设备安全状态。
- 双击标签或拖动标签可把完整调试窗口悬浮出来，关闭悬浮窗口后自动恢复原位置。

### 工作区自动恢复

程序同级的 `live_watch_history.db` 会保存：

- ELF、调试后端、探针和 cfg 配置；
- 三个调试窗口中的变量；
- 每个 CH 绑定的变量与曲线颜色；
- 单图/双图/三图模式、左右和上下分割比例；
- 采样频率、当前调试页和窗口尺寸；
- ELF 浏览器的最近使用权重。

再次打开软件时会先使用当前 ELF 校验变量，再恢复波形和布局，不需要重复添加变量。数据库不保存实时采样历史；需要长期保存的数据请从波形右键菜单导出 CSV。

## 调试后端

| 后端 | 可用探针 | 说明 |
| --- | --- | --- |
| J-Link 原生 | SEGGER J-Link | 使用 SEGGER 官方 DLL，Windows 下速度和兼容性优先 |
| OpenOCD | ST-Link | 使用对应 interface 与 target cfg |
| OpenOCD | DAPLink / CMSIS-DAP | 使用 CMSIS-DAP interface cfg |
| OpenOCD | J-Link adapter | 依赖 libusb，通常不应与 SEGGER 官方驱动混用 |

两种后端都支持变量读写、实时波形、后台 ELF 烧录、校验、复位和连接恢复。OpenOCD 模式还支持自选厂商 interface/target cfg；程序会自动加入 cfg 所在目录作为脚本搜索路径。

> J-Link 原生模式与 OpenOCD 的 J-Link adapter 是两套驱动链路。Windows 出现 `LIBUSB_ERROR_NOT_SUPPORTED` 时，优先使用 J-Link 原生模式，不要随意替换 SEGGER 官方 USB 驱动。

## 快速开始

### 从源码运行

需要 Python 3.10 或更高版本：

```powershell
python -m pip install PyQt6 pyqtgraph numpy pyelftools pylink-square
python .\main.py
```

也可以直接运行打包后的 `Mcu_Studio.exe`。首次启动时会在程序同级创建 `live_watch_history.db`。

### 连接和使用

1. 选择包含调试信息的 `.elf` 文件，固件编译时建议保留 `-g`。
2. 选择 J-Link 原生或 OpenOCD 后端并填写对应配置。
3. 进入工作站，通过顶部输入框或 ELF 变量浏览器添加变量。
4. 在调试变量上右键，将标量绑定到 CH1、CH2 或 CH3。
5. 使用暂停、适配和测量完成动态观察；需要保存数据时在波形或变量上右键导出 CSV。

仓库自带 OpenOCD，默认位置为：

```text
runtime/OpenOCD/bin/openocd.exe
```

OpenOCD 连接、重连和烧录可能复位目标，具体行为取决于 target cfg。电机、功率设备和其他不能随意复位的系统，应先断开执行机构并在安全环境验证。

## 性能与稳定性

- J-Link 会合并连续地址读取，且不会跨越地址空洞。
- OpenOCD 使用统一 TCL 请求完成一轮高速采样，连续安全区间使用块读取。
- 低速表格、高速采样、烧录和后端重连都在后台线程执行。
- 每个高速变量的历史缓存有上限；绘图最多使用约 4000 个峰值保留点。
- 关闭窗口时会等待后台通信线程安全结束，避免 `QThread: Destroyed while thread is still running`。
- ELF 文件变化会等待写入稳定后再自动重新提取，解析失败时保留原有有效数据。

## 校验与打包

不连接探针、不读写 MCU 的全项目校验：

```powershell
python .\tools\validate_project.py
```

构建 Windows EXE：

```powershell
python -m pip install pyinstaller
python .\build.py
```

成品输出到 `dist/Mcu_Studio.exe`。`build.py` 强制使用 PyQt6，并检查产物中是否混入第二套 Qt。

## 项目结构

```text
McuStdio/
├─ main.py                  # 应用入口
├─ build.py                 # PyInstaller 构建入口
├─ mcu_studio/             # UI、ELF/数据库核心和后台任务
├─ assets/                 # 应用图标与 README 截图
├─ runtime/OpenOCD/         # 内置 OpenOCD 与 cfg
├─ tools/                  # 无硬件校验工具
├─ doc/                    # 完整使用与维护文档
└─ live_watch_history.db   # 本机工作状态（首次运行后生成）
```

更详细的连接、变量、烧录、数据库迁移、构建和故障排查说明见 [文档目录](doc/README.md)。第三方组件保留各自许可证。
