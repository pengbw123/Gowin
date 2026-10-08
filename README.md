# Tang Primer 25K USB-MIDI加法合成器

这是面向Tang Primer 25K的实时数字乐器工程。MIDI键盘通过Dock板USB-A接口连接FPGA，RV32I软核完成USB Host枚举和USB-MIDI协议处理，RTL音频引擎完成8复音、每音16谐波的加法合成，并通过I2S连接MAX98357功放模块输出声音。

当前版本同时支持四基准谐波音色插值、谐波独立衰减、ADSR、力度、延音踏板、总音量、Pitch Bend、Vibrato、Portamento，以及Chorus、Delay和Reverb三种后级效果。

## 仓库结构

```text
fpga_project3/
├─ src/                  FPGA RTL、约束、USB Host软核和固件
│  ├─ generated/        正弦表、音高表、包络表和默认音色参数
│  └─ usb_midi/         USB 1.1 Host RTL与RV32I固件
├─ sim/                  加法合成器RTL测试平台
├─ tools/                谐波编辑器、UART参数工具和表生成脚本
├─ fpga_project.gprj     Gowin IDE工程
├─ build_cli.tcl         Gowin命令行完整构建脚本
└─ README.md             原理、控制映射、接线和验证说明
```

构建目录、位流、IDE用户配置、诊断工程、Python缓存和本地可执行文件不提交到仓库，可由源码重新生成。

## 快速开始

### 1. 构建FPGA工程

使用Gowin EDA `1.9.12.03`打开：

```text
fpga_project3/fpga_project.gprj
```

器件必须选择：

```text
GW5A-25B / GW5A-LV25MG121NC1/I0 / Device Version B
```

也可以在已配置`gw_sh`的终端中执行：

```powershell
cd fpga_project3
gw_sh .\build_cli.tcl
```

构建完成后下载`fpga_project3/impl/pnr/fpga_project.fs`。

### 2. 连接硬件

- MIDI键盘接Dock板USB-A Host口；
- MAX98357的BCLK、LRC、DIN分别连接J3-15、J3-16、J3-17；
- MAX98357使用J3的5V和GND供电；
- 扬声器只接MAX98357的`SPK+`和`SPK-`，不要将其中一端接地；
- USB-C用于下载、115200波特率日志和UART音色参数传输。

### 3. 启动谐波编辑器

```powershell
cd fpga_project3\tools
.\install_python_requirements.bat
.\run_harmonic_editor.bat
```

编辑器可以修改C2/C3/C4/C6四组基准的16次谐波幅度、各谐波衰减时间和ADSR参数，并通过UART实时发送到FPGA。

## 当前验证状态

- USB-MIDI键盘枚举、按键、力度和总音量已完成实板验证；
- 加法合成、四基准插值和UART编辑链路已完成RTL与实板验证；
- Pitch Bend、Vibrato、Portamento和三种数字效果已通过完整综合、布局布线与时序分析，等待进一步实板听感调校；
- 最终资源：Logic 52%、Register 22%、BSRAM 84%、DSP 90%；
- 50 MHz音频域Fmax为50.296 MHz，Setup/Hold违例为0，TNS为0。

详细MIDI映射、DSP原理、引脚表和测试步骤见[工程说明](fpga_project3/README.md)。

## 第三方代码

USB Host相关RTL和软核代码的许可说明见`fpga_project3/src/usb_midi/LICENSE.third-party.txt`。
