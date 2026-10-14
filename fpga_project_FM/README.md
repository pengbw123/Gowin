# Tang Primer 25K USB-MIDI · 6算子FM / 16复音

本目录是独立的FM合成分支。USB-MIDI Host、UART、I2S和数字效果链沿用已验证工程，音源已替换为6算子相位调制、经典32算法路由和16复音。它面向高云赛题中的实时音色合成与多复音展示，也为后续迁移Mega60K预留了可参数化声部数与共享流水线结构。

## 整体链路

```text
25键USB-MIDI键盘 / UART上位机
  -> USB1.1 PHY/SIE + RV32I枚举，或UART定长协议
  -> USB/UART演奏事件仲裁
  -> 16声部管理、力度、CC64延音、Pitch Bend、LFO
  -> 6算子调度器（OP6 -> OP1）
  -> 状态BSRAM -> DX7对数EG -> 增益ROM -> 正弦ROM -> 算法路由/反馈
  -> 16声部混音 -> Chorus -> Reverb
  -> I2S -> MAX98357 -> 扬声器
```

每个声部包含6个正弦算子，每个算子都有Ratio/Fixed频率、输出电平、四段Rate/Level包络、力度灵敏度、幅度LFO与键位缩放；全局还有32种连接算法、反馈、Pitch EG、LFO和移调。

本版定位为“DX7音乐兼容”，并非DX7逐位仿真。公开`.syx`可以直接导入作为音色起点。算子EG采用DX7实测的非线性Level、qrate与Attack加速规律，输出电平/键位缩放/力度在同一对数域合成；极端反馈、LFO与原机量化噪声仍会有差异。

## 流水线与资源设计

- 算子实时状态放入106位×96槽同步BSRAM，而不是16×6套并行寄存器；
- 调度器连续处理16个声部的同一算子，再进入下一算子；
- 四级共享流水线依次完成状态读取/相位、对数Level查增益、正弦乘法、输出路由；
- 相邻声部天然隔开依赖算子的读写延迟，一个物理计算通道即可服务96个逻辑算子；
- 6×128键位缩放在PC端预编译为带符号Level偏移，FPGA在Note On时叠加力度；每个采样只做有限步长EG和共享ROM查表。

`VOICE_COUNT`是模块参数，后续Mega60K可提高声部数；但增加复音前必须重新检查每采样周期的调度预算、DSP、BSRAM和Fmax。

## FM音色编辑器

首次使用执行`tools/launchers/install_python_requirements.bat`。随后双击`tools/run_tools_menu.bat`选择第6项，或运行`tools/launchers/run_fm_editor.bat`。

编辑器支持：

- DX7 155字节单音色SysEx；
- DX7 4096字节、32音色Bank SysEx；
- 无SysEx外壳的128/4096字节原始音色数据；
- 本项目`gowin-fm-patch-v1` JSON保存/载入；
- 算法、反馈、移调、LFO、Pitch EG；
- 六个算子的开关、Ratio/Fixed、Coarse/Fine/Detune、输出电平；
- 六个独立四段Rate/Level EG、Rate Scaling、力度、AMS与左右键位缩放；
- C3/C4/C5上板试听。

“发送完整音色”会先发送All Notes Off，把DX7参数编译成FPGA固定点参数和键位增益表，最后Commit。约800个12字节包在115200波特率下约需1秒。发送期间不要让串口助手或另一上位机占用同一COM口。

默认示例位于`tools/presets/fm_default_epiano.json`。

## MIDI键盘控制

| 控制 | 功能 |
|---|---|
| 力度琴键 | 音符与算子力度灵敏度 |
| MODE旋钮 / CC20 | 总音量0～127 |
| Pitch滑条 | 14位弯音 |
| MOD滑条 / CC1 | 颤音深度 |
| OCT旋钮 / CC21 | Chorus量 |
| LATCH旋钮 / CC22 | Portamento参数（FM核当前仅保留接口） |
| SWING旋钮 / CC24 | Reverb量 |
| RATE旋钮 / CC26 | 颤音速度 |
| CC64 | 延音踏板 |

旧吉他扫弦模块没有接入这个FM分支：它会额外占用约千级LUT，并曾成为50 MHz关键路径。扫弦算法仍完整保留在原加法合成工程，等Mega60K资源和时序更宽裕时再移植更稳妥。

## 接线

MAX98357接Dock J3：

| MAX98357 | J3 | FPGA管脚 |
|---|---:|---|
| VIN | 11 | +5 V |
| GND | 12 | GND |
| BCLK | 15 | F1 |
| LRC/WS | 16 | F2 |
| DIN | 17 | A1 |

其他关键管脚：

| 信号 | 管脚 |
|---|---|
| 50 MHz时钟 | E2 |
| S2复位 | H10 |
| UART RX/TX | B3 / C3 |
| USB Host D+ / D- | L6 / K6 |

USB-A Dock连接MIDI键盘；板载USB-C用于下载、JTAG和BL616串口。MAX98357优先从J3的5 V供电并与FPGA共地，不从普通GPIO供电。

若E2报CPU/SSPI专用管脚错误，在Gowin工程的Dual-Purpose Pin中启用“Use CPU as regular IO”和“Use SSPI as regular IO”。

## 编译与验证

```powershell
& 'D:\Gowin\Gowin_V1.9.12.03_x64\IDE\bin\gw_sh.exe' build_cli.tcl
$env:PYTHONPATH='tools'
python -m unittest discover -s tools/tests -p 'test_*.py' -v
```

比特流输出为`impl/pnr/fpga_project.fs`。

2026-10-14当前完整布局布线结果：50 MHz音频域实际Fmax 51.012 MHz，47.917 MHz USB域实际Fmax 89.829 MHz，Setup/Hold TNS均为0；逻辑14014/23040（61%）、寄存器7182/23280（31%）、BSRAM 33/56（59%）、DSP 14.5/28（52%）。这组数据包括16复音FM、USB软核、Chorus、Reverb和I2S。

## 主要源码

| 文件 | 作用 |
|---|---|
| `src/fm_poly_synth.v` | 16声部、6算子、EG/LFO/反馈与共享流水线 |
| `src/fm_uart_control.v` | FM参数、UART MIDI和扫弦配置协议 |
| `src/top.v` | USB、UART、FM核、效果器与I2S集成 |
| `src/audio_effects.v` | Chorus与Reverb |
| `tools/core/fm_patch.py` | SysEx解析、DX7参数编译与串口打包 |
| `tools/apps/fm_editor.py` | FM图形编辑器 |

## 当前边界与后续升级

- 动态IIR不放在FM核中：独立算子EG本身就能让频谱随时间变化，先省下滤波乘法器和控制复杂度；
- Chorus与Reverb保留，旧Delay未接入最终音频链；
- 吉他扫弦暂不接入FM实时链，优先保证16复音FM稳定性；
- Portamento接口保留，当前FM核尚未恢复逐声部滑音状态；
- 上电默认音色是内置电钢琴风格参数，UART导入音色只在本次上电期间保留；
- Mega60K优先增加声部数、参数双缓冲和反馈精度，再考虑动态滤波或更多效果器并行。
