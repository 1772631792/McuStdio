# MCU Live Watch Studio V2.2

V2.2 将 MCU Live Watch Studio 扩展为时域、频域和在线调参一体化工具，同时改进调试工具发现、多项目配置、便携打包和关闭安全性。

## 主要更新

- 新增多变量整宽滑动调节：每个变量独立保存范围、步进、自动扩展和写入模式。
- 支持拖动、滑轨点击、键盘方向键、减号/加号点击及长按连续调节。
- 新增 SQLite 波形历史：后台 WAL 批量写入，CSV 合并数据库历史和内存最新数据。
- 新增 J-Link 与 OpenOCD 本地环境自动查找，数据库中已保存的有效路径优先。
- OpenOCD 支持 ST-Link、CMSIS-DAP/DAPLink 和 J-Link adapter，烧录带进度、校验和复位流程。
- ELF 浏览器支持源文件筛选、模糊搜索、结构体/数组懒加载以及连续添加变量。
- 三个调试窗口支持拖出悬浮；CH1–CH3 支持多变量、独立颜色、图例、冻结、适配和两点测量。
- 采样、读写、烧录、重连、ELF 解析和历史保存均在后台线程执行，关闭程序时安全回收。
- 新增独立 FFT 频域模式：每个 CH 可分别选择时域或频域显示。
- 自动检测基波，显示 H2–H30 谐波相对基波百分比和 THD。
- 支持 Hann、Flat Top、Hamming、Blackman、矩形窗以及相干增益幅值校正。
- J-Link/OpenOCD 路径改为可编辑下拉框；只有点击“扫描电脑”才进行后台固定磁盘扫描。
- 新增工作站返回配置按钮，安全停止采样、写入和连接后可重新选择后端与工具链。
- 一个数据库支持多套项目配置，固定主配置不可删除，并提供新建、删除和清空操作。
- 配置之间独立保存 ELF、后端、变量、滑动条、波形、布局和采样历史。
- 新增内置 OpenOCD 的目录版发布包，数据库集中存放在 `data/` 子目录。
- 单文件版优先读取 EXE 同级已有数据库；SQLite 正常运行只持久保留一个主 DB 文件。

## 下载与安装

### J-Link 原生模式

下载 `Mcu_Studio.exe`。电脑安装 SEGGER J-Link Software 后直接运行，不需要 `OpenOCD.zip`。

### OpenOCD 模式

同时下载 `Mcu_Studio.exe` 与 `OpenOCD.zip`，放在同一目录；直接解压 ZIP，形成：

```text
Mcu_Studio.exe
OpenOCD/
└─ bin/openocd.exe
```

程序会自动查找该目录。也可以在启动页选择其他已安装的 OpenOCD。

也可以直接下载 `Mcu_Studio_Portable.zip`。它已经包含程序运行库和完整 OpenOCD，解压后运行目录中的 `Mcu_Studio.exe`，不要只复制其中一个 EXE。

## 使用提示

- 固件应保留 DWARF 调试信息，推荐 Debug 构建并启用 `-g`。
- 在线写变量前确认 ELF、类型、地址和设备安全状态。
- OpenOCD 加载 target cfg、烧录和手动复位可能复位 MCU；电机和功率设备应先置于安全状态。
- J-Link 原生模式使用 SEGGER 官方驱动；不要为了 OpenOCD 随意替换 J-Link USB 驱动。

## 质量验证

- 500 组用户流程和组件组合回归通过。
- PyQt6 单绑定检查通过，没有混入 PyQt5/PySide。
- 最终单文件 EXE 启动自检通过，覆盖 `PyQt6.uic`、主窗口、SQLite、工作线程和 pyqtgraph。
- 回归测试不会连接探针或读写 MCU；正式发布前仍建议在实际目标板完成连接、读写、烧录和重连验证。

## SHA256

```text
Mcu_Studio.exe           8ABE8706B7789B4AA42EE3AD1B63C45E6259413DAA3B66F8B60ADF20B4E2B3A8
OpenOCD.zip              1AC8CEDC1797CE739116FF5DCBE6235D9D1060D43427FD749D808D8BC8C5F870
Mcu_Studio_Portable.zip  D2391434583A3A1C00160DDEB2AA05608E5B98576C07921DBD0497065E0FF550
```
