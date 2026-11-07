# MCU Live Watch Studio V2.0

V2.0 是面向 Cortex-M 实时变量观察、在线调参、波形分析和固件烧录的正式可用版本。

## 核心更新

- 新增多变量整宽滑动调节：每个变量独立保存范围、步进、自动扩展和写入模式。
- 支持拖动、滑轨点击、键盘方向键、减号/加号点击及长按连续调节。
- 新增 SQLite 波形历史：后台 WAL 批量写入，CSV 合并数据库历史和内存最新数据。
- 新增 J-Link 与 OpenOCD 本地环境自动查找，数据库中已保存的有效路径优先。
- OpenOCD 支持 ST-Link、CMSIS-DAP/DAPLink 和 J-Link adapter，烧录带进度、校验和复位流程。
- ELF 浏览器支持源文件筛选、模糊搜索、结构体/数组懒加载以及连续添加变量。
- 三个调试窗口支持拖出悬浮；CH1–CH3 支持多变量、独立颜色、图例、冻结、适配和两点测量。
- 采样、读写、烧录、重连、ELF 解析和历史保存均在后台线程执行，关闭程序时安全回收。

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

## SHA-256

```text
Mcu_Studio.exe  0DB26AB0094F0D15FFBBAF3D998892235FA0FE8A25C7120158C264F63F0F6868
OpenOCD.zip     41B81FDC499950AAF869617662C0A19845212096946C8D6EC97A3ACB6A59A803
```
