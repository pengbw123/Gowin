# Tang Primer 25K USB-MIDI加法合成器与数字效果器

本工程是当前发布版本。USB-MIDI枚举由RV32I软核负责，所有逐采样音频运算均由RTL完成。音源采用“8复音 × 每音16个正弦谐波”的加法合成结构，并支持C2/C3/C4/C6四组音色基准、音高插值和每谐波独立衰减。

## 音频链路

```text
25键USB-MIDI键盘
  -> Dock USB-A Host
  -> USB1.1 PHY/SIE + RV32I枚举/MIDI协议
  -> 48/50 MHz事件邮箱
  -> 8声部音符分配、力度和延音踏板
  -> 4基准参数插值
  -> 共享正弦ROM，分时计算8 x 16个谐波
  -> 每谐波指数衰减 + 每声部ADSR
  -> Pitch Bend + Vibrato + Portamento
  -> 0 dB总音量混音与16位饱和
  -> Chorus -> Delay -> Reverb
  -> I2S -> MAX98357 -> 扬声器
```

软核只输出MIDI事件，不生成音频采样。每个声部独立保存音高、相位、力度、按键状态、ADSR状态和16组谐波衰减状态。

## 四基准谐波插值

默认基准音为MIDI 36/48/60/84，即C2/C3/C4/C6。每个基准包含：

- 16个Q1.15谐波初始幅度；
- 16个Q0.24指数衰减系数。

中间音符在左右基准之间进行Q0.5近似线性插值。C2～C3和C3～C4各为12个半音；C4～C6为24个半音，C5正好使用C4与C6参数各50%。5位比例让这个两八度区间内的每个半音都有独立音色位置，并用查表避免硬件除法。C2以下使用C2参数，C6以上使用C6参数。低音覆盖比原布局多一个八度；因为原C5参数本就接近C4/C6的中点，移除独立C5基准对当前预设的影响很小。

每个分量按下式衰减：

```text
level[n+1] = level[n] - level[n] * alpha
```

上位机显示的是更直观的衰减时间常数（毫秒），发送时转换为Q0.24 `alpha`。数值越大，衰减越慢；0表示不进行额外谐波衰减，声音仍受全局ADSR控制。

高音区只计算满足 `harmonic_frequency < 0.45 * sample_rate` 的分量，防止超过奈奎斯特频率后产生混叠。

## 分时计算预算

- 系统时钟：50 MHz；
- 音频采样率：48.828125 kHz；
- 每个音频采样周期：1024个系统时钟；
- 最坏8声部 × 16谐波运算：约560个系统时钟。

一个正弦ROM和乘法流水线由全部声部共享。分时复用不会降低逻辑复音数，因为8个声部的相位、包络和谐波状态相互独立。

## MIDI功能

| 消息/控件 | 用途 |
| --- | --- |
| `90 nn vv` | Note On，音高和力度 |
| `80 nn vv` | Note Off |
| `90 nn 00` | 按Note Off处理 |
| `B0 14 vv` / MODE旋钮 | 总音量0～127 |
| `E0 ll hh` / PITCH滑条 | 14位手动弯音，范围±2半音 |
| `B0 01 vv` / MOD滑条 | 颤音深度，最大±0.5半音 |
| `B0 15 vv` / OCT旋钮 | Chorus干湿比例，0为旁路 |
| `B0 16 vv` / LATCH旋钮 | Portamento时间，0为关闭 |
| `B0 17 vv` / GATE旋钮 | Delay干湿比例，0为旁路 |
| `B0 18 vv` / SWING旋钮 | Reverb干湿比例，0为旁路 |
| `B0 19 vv` / TEMPO旋钮 | Delay时间约42～208 ms |
| `B0 1A vv` / RATE旋钮 | 颤音速度约3～9 Hz |
| CC64 | 延音踏板 |
| CC123 | All Notes Off |

最大复音数为8。没有空闲声部时，优先复用包络电平最低的声部。Release尚未结束的音符仍占用声部。

所有效果器上电默认旁路，先保持原始音色。不同厂商键盘若使用标准CC91/CC93，也可分别控制Reverb/Chorus。

## 演奏控制的实现原理

Pitch Bend和Vibrato最终都不是改波表内容，而是修改DDS每个采样点的相位步进。工程使用一张512项Q1.16比例ROM覆盖-2～+2半音，因此MIDI弯音的音程关系是指数关系，不是粗略的线性频率偏移。MOD产生三角形低频振荡，深度最大为半个半音；RATE控制约3～9 Hz。

Portamento只在LATCH非零且新音以连奏方式按下时启动。此时复用上一声部并保持相位和包络，以固定步长靠近目标音高，是真正的单音Legato而不是两个音重叠。当前版本提供8档约0.7～671 ms的滑音时间；LATCH为0时恢复正常8复音。它最适合单音Lead演奏，普通钢琴和弦应将LATCH置零。

## 三种后级效果的实现原理

- Chorus不是多开几个合成振荡器。它保存约9～17 ms的历史音频，用0.35 Hz低频振荡器移动读取位置，再把这个时而稍快、时而稍慢的延迟副本与原声叠加。因此副本会产生轻微动态失谐，听起来像多人齐奏。
- Delay把较长时间以前的采样读出来并混回输出，同时把旧回声的一半重新写回延迟线，因此形成一次比一次小的清晰重复声。TEMPO改变约42～208 ms的间隔，GATE改变效果量。
- Reverb使用1499、1777、2137采样三条不同长度的反馈梳状延迟模拟不同墙面反射，再通过521采样全通扩散器把离散反射打散成密集尾音。SWING控制混响量。

为适配25K的DSP余量，三个效果的干湿旋钮内部量化为12.5%、25%、37.5%、50%四档，旋钮0保持真正旁路。处理顺序为`Chorus -> Delay -> Reverb`。

## 串口上位机

板载USB-C的BL616调试串口同时承担两件事：

- FPGA到电脑：USB软核输出枚举与MIDI调试文本；
- 电脑到FPGA：RTL接收115200 baud二进制音色参数。

两个接收端可以同时观察 `uart_rx_pin`，不需要增加新的引脚。使用上位机时必须先关闭占用同一COM口的串口助手。

安装依赖并启动图形编辑器：

```powershell
cd tools
.\install_python_requirements.bat
.\run_harmonic_editor.bat
```

编辑器启动所用的那个Python必须安装`pyserial`。其他串口助手能够看到COM口，并不代表这个Python环境已经装好串口库。编辑器在缺少`pyserial`时会用Windows接口尝试列出COM名并给出提示，但发送仍需先执行安装脚本；串口框也允许直接输入`COM11`一类端口名。

编辑器支持：

- C2/C3/C4/C6四个页面；
- 每页16个谐波幅度；
- 用0～4倍倍率同时缩放当前基准或全部四个基准的16个谐波；
- 将当前基准的谐波幅度和一键归一到1；
- 实时预览当前基准的单周期加法合成波形，并显示幅度和、峰值、RMS及削顶警告；
- 每个谐波独立衰减时间；
- Attack、Decay、Sustain、Release；
- JSON保存/载入；
- 一次发送全部133个配置包。

参数表直接写入活动表，但正在发声的音符已保存自己的谐波状态，因此不会被突然改写。发送完成后重新按下琴键，即可听到新音色。发送约需0.14秒，发送期间不要演奏新音符。

波形预览显示的是16个谐波在按键初始时刻的`sum(Ak*sin(k*x))`，不包含ADSR和各谐波随时间的独立衰减。整体倍率会直接改写界面中的16个幅度，放大后若复合波峰值超过1，曲线变红并提示可能削顶。

## 25键与OCT档位核对

工程采用MIDI标准`A4=69=440 Hz`，并按`C4=60`命名。USB软核把键盘的`data1`音符号原样送给RTL，RTL直接用它读取128项DDS相位步进表，没有再次加减八度。你提供的OCT -3抓包中，最左五键为`0C/0D/0E/0F/10`，即十进制12～16，证明25个琴键是从C开始连续排列，OCT每档移动12个半音。

| 键盘OCT档 | 最左键～最右键MIDI号 | 工程音名范围 |
| ---: | ---: | --- |
| -3 | 12～36 | C0～C2 |
| -2 | 24～48 | C1～C3 |
| -1 | 36～60 | C2～C4 |
| 0 | 48～72 | C3～C5 |
| +1 | 60～84 | C4～C6 |
| +2 | 72～96 | C5～C7 |
| +3 | 84～108 | C6～C8 |

因此键位对应关系正确：例如OCT 0最左键是MIDI48/C3，第13个键是MIDI60/C4，最右键是MIDI72/C5。四基准C2/C3/C4/C6对应MIDI36/48/60/84。OCT -3的最低音只有16.35 Hz，普通小扬声器很难重放；OCT +3最高C8约4186 Hz。极低音听起来很小或近似无声时，应先区分扬声器频响与MIDI映射问题。

命令行示例：

```powershell
python .\tools\uart_synth_control.py COM11 send-preset .\tools\additive_piano_4anchor.json
python .\tools\uart_synth_control.py COM11 harmonic-amp C4 3 0.25 --commit
python .\tools\uart_synth_control.py COM11 harmonic-decay C4 3 1800 --commit
python .\tools\uart_synth_control.py COM11 adsr 30 500 0.18 800
```

协议为固定12字节：

```text
A5 5A CMD DATA0 DATA1 ... DATA7 CHECKSUM
CHECKSUM = XOR(CMD, DATA0..DATA7)
```

命令：

| CMD | 内容 |
| --- | --- |
| `10` | Attack采样数和曲线相位步进 |
| `11` | Decay采样数和曲线相位步进 |
| `12` | Sustain Q0.16 |
| `13` | Release采样数和曲线相位步进 |
| `40` | 基准、谐波序号、幅度Q1.15 |
| `41` | 基准、谐波序号、衰减系数Q0.24 |
| `42` | 本轮参数发送结束标记 |

## 默认音色文件

- `tools/additive_piano_4anchor.json`：上位机默认音色与ADSR；
- `src/generated/additive_sine_2048.mem`：2048点正弦ROM；
- `src/generated/additive_harmonic_amp.mem`：4 × 16默认幅度；
- `src/generated/additive_harmonic_decay.mem`：4 × 16默认衰减系数；
- `tools/generate_additive_tables.py`：从JSON重新生成三份初始化文件。

修改默认JSON后重新生成：

```powershell
python .\tools\generate_additive_tables.py
```

## 接线

| 信号 | FPGA管脚 | 连接 |
| --- | --- | --- |
| `clk` | E2 | Dock 50 MHz时钟 |
| `key_s2` | H10 | S2复位 |
| `usb_host_dp/dn` | L6/K6 | Dock USB-A，连接MIDI键盘 |
| `uart_rx_pin/tx_pin` | B3/C3 | 板载BL616 UART |
| `i2s_bclk` | F1 / J3-15 | MAX98357 BCLK |
| `i2s_lrclk` | F2 / J3-16 | MAX98357 LRC |
| `i2s_din` | A1 / J3-17 | MAX98357 DIN |
| `+5V/GND` | J3-11/J3-12 | MAX98357供电与共地 |

扬声器接MAX98357的SPK+和SPK-，不要把扬声器任一端接地。

## 编译与验证结果

使用Gowin EDA `1.9.12.03`、器件 `GW5A-25B / GW5A-LV25MG121NC1/I0`完成综合、布局布线、时序分析和比特流生成：

| 资源 | 使用量 |
| --- | ---: |
| Logic | 11879 / 23040，52% |
| Register | 5021 / 23280，22% |
| BSRAM | 47 / 56，84% |
| DSP | 25 / 28，90% |

50 MHz音频域实际Fmax为50.296 MHz，47.917 MHz USB域实际Fmax为62.896 MHz，TNS为0，无Setup/Hold违例。生成文件为 `impl/pnr/fpga_project.fs`。25K资源已经较紧，后续大型效果扩展应迁移到60K，而不是继续复制长延迟线或乘法器。

RTL回归测试位于`sim/tb_midi_poly_synth.v`和`sim/tb_midi_poly_audio.v`。测试覆盖C2/C3/C4/C6四个锚点、C4～C6的C5中点和C#4非锚点，检查谐波初始化、DDS相位步进及完整音频输出；当前六个测试音峰值均非零并通过。

首次上板按以下顺序验证：

1. 下载 `impl/pnr/fpga_project.fs`；
2. 打开115200串口，确认USB Host每秒心跳；
3. 插入MIDI键盘，确认枚举到MIDIStreaming接口；
4. 单音检查16谐波音色和Release；
5. 依次测试2、4、8个同时音符；
6. 关闭串口助手，打开谐波编辑器发送默认预设；
7. 重新按键，分别修改C2/C3或C4/C6两个基准后试听中间音的插值效果；
8. 用频谱软件检查基频与有效谐波峰，确认高音区没有折叠混叠。

综合、布局布线和时序分析已经通过。USB-MIDI、基础发声和UART参数下载已完成实板验证；新加入的Pitch Bend、Vibrato、Portamento及三种效果器仍需按上述步骤进行实板听感调校。
