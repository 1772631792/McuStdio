# MCU Live Watch Studio

MCU Live Watch Studio 是一个面向 Cortex-M 单片机的实时变量观测工具。它通过 J-Link DLL 或 OpenOCD 直接读写 MCU 内存，不依赖 Keil 的断点调试会话，适合电机控制、电源、PID 调参等需要保持固件连续运行的场景。

> 当前 J-Link 后端已实机使用；CMSIS-DAP/ST-Link + OpenOCD 后端仍属待完善功能。在用于高风险功率设备前，请先在低风险平台上验证连接和复位行为。

## 主要功能

- 从 ELF/DWARF 读取全局变量、类型、结构体和枚举信息。
- 树形展开结构体、数组和多级成员。
- 低速表格实时刷新，双击可修改 MCU 内存中的标量值。
- 三通道波形观测，支持单图、双图和三图布局。
- 空格键暂停时间轴，十字光标回看时间戳和数值。
- J-Link 模式支持 ELF 烧录、手动复位、断开和重连。
- OpenOCD 模式支持 CMSIS-DAP 与 ST-Link，可选内置或自定义 interface/target cfg。

## 快速开始

### 从源码运行

需要 Python 3.10 或更高版本。

```powershell
python -m pip install PyQt6 pyqtgraph numpy pyelftools pylink-square
python .\live_set.py
```

启动后：

1. 选择固件 `.elf` 文件。
2. 选择 J-Link 或 OpenOCD 后端。
3. J-Link 模式下选择 SEGGER 安装目录和准确的 Device。
4. OpenOCD 模式下选择 `openocd.exe`、调试器 cfg 和与芯片完全匹配的 target cfg。
5. 进入工作站后添加变量，再绑定到 CH1–CH3。

### OpenOCD 注意事项

- 仓库已包含 OpenOCD，路径为 `runtime/OpenOCD/bin/openocd.exe`。
- 内置脚本已包含常见 STM32 target，但不代表支持所有国产兼容芯片。
- 当前界面中的 `py32f0xx.cfg` 需要用户自行提供；所选 OpenOCD 发行包若没有该脚本，则无法连接。
- OpenOCD 启动时会执行 target 初始化。是否会影响复位线，取决于 target cfg、接线和调试器。
- 请确保 TCP 3333、4444 和 6666 端口没有被其他 OpenOCD 实例占用。

## 项目结构

```text
McuStdio/
├─ live_set.py              # 唯一应用入口
├─ build.py                 # 打包入口，可设置 EXE 名称
├─ README.md
├─ assets/                 # 图标等静态资源
├─ live_watch_history.db   # 运行数据，与入口/可执行文件同级
├─ runtime/                # 跟随 Git 发布的 OpenOCD、ARM GCC、cfg 和 SVD
├─ packaging/              # PyInstaller spec
├─ tools/                  # 图标生成、仓库打包等工具
├─ doc/                    # 目录说明、已知问题和历史归档
├─ dist/                   # 打包后的 EXE（Git 忽略）
└─ artifacts/              # build 缓存和其他可再生成产物
```

程序会在入口脚本或可执行文件的同级目录创建 `live_watch_history.db`，用于保存布局、变量和后端配置。因此发布 EXE 时，数据库应与 EXE 放在一起。

## 打包

先打开 `build.py`，修改顶部的 `APP_NAME`（不要带 `.exe`），然后执行：

```powershell
python -m pip install pyinstaller
python .\build.py
```

`build.py` 只使用 `packaging/Mcu_Studio.spec`，强制选择 PyQt6，并排除 PySide6、PyQt5 及其绑定库。打包后还会自动检查 EXE 内容；如果发现第二套 Qt，脚本会报错退出。历史 spec 仅存放在 `doc/archive/packaging/`，不要用于发布。

打包成品输出到项目根目录的 `dist/`，例如 `dist/Mcu_Studio.exe`。PyInstaller 中间文件仍保存在 `artifacts/build/`。

## 内置工具链

`runtime/` 不在 `.gitignore` 中，应跟随开源仓库提交。当前包含：

- Arm GNU Toolchain 15.2.Rel1：`runtime/arm-gcc/`
- OpenOCD 0.12.0：`runtime/OpenOCD/`
- 常用 target cfg：`runtime/cfg/`
- SVD 资源：`runtime/svd/`

该目录约 1.14 GiB，会显著增加克隆和下载时间。第三方工具版本与许可说明见 `doc/03-第三方工具链.md`。

## 界面预览

![MCU Live Watch Studio](https://file1.elecfans.com/web3/M00/61/66/wKgZPGoUBmOAP7HxAAL1ZVXrXKM604.png)
