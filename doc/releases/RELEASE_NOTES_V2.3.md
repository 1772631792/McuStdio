# MCU Live Watch Studio V2.3

V2.3 增加 Keil AXF 支持，并完善 J-Link/OpenOCD 被其他下载或调试程序占用时的安全断开与手动恢复流程。

## 主要更新

- 固件选择器同时支持 `.elf` 和 `.axf`；两者均可解析 DWARF 变量并通过 J-Link 或 OpenOCD 烧录。
- 高速采样、低速读取和变量写入检测到后端通信失败时，会立即停止读写并释放当前调试后端。
- OpenOCD 进程异常退出或探针被其他程序占用时，界面明确显示自动断开原因。
- 断线后不再循环自动重连或重新抢占探针，只允许用户手动点击“重连”。
- 手动重连成功后，程序会重新读取一次当前路径上的最新 ELF/AXF，再恢复变量地址、监视列表和波形采样。
- 保持原有安全约束：自动断开不会主动发送复位、暂停或烧录命令。

## 下载和使用

- `Mcu_Studio.exe`：单文件版，适合已安装 SEGGER J-Link Software 的用户。
- `OpenOCD.zip`：独立 OpenOCD 运行包，可与单文件版配合使用。
- `Mcu_Studio_Portable.zip`：内置完整 OpenOCD 的便携目录版，解压后运行目录中的 `Mcu_Studio.exe`。

当其他下载工具占用探针时，先等待 MCU Live Watch Studio 自动断开，再完成外部下载；关闭外部工具后，回到软件手动点击“重连”。重连会读取一次最新 ELF/AXF，因此应确保配置中的固件路径指向刚刚生成的文件。

## 质量验证

- 500 组用户流程与组件组合回归通过。
- 新增调试器占用后只上报一次、自动停止、等待手动重连的回归测试。
- 新增手动重连后读取最新 ELF/AXF 的回归测试。
- PyQt6 单一 Qt 绑定审计以及单文件版、便携版打包自检通过。

## SHA256

```text
Mcu_Studio.exe           E2B293191C62704B39DB6C9BD0DD51627B3C617B9D69F54096983D1F870206F3
OpenOCD.zip              1AC8CEDC1797CE739116FF5DCBE6235D9D1060D43427FD749D808D8BC8C5F870
Mcu_Studio_Portable.zip  1CF633EFED8BA7986CE48447D22FAD31BDF6E35B9D9B92CCAB724B5116B036AB
```
