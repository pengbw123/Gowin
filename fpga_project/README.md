# 单音可配置波表 + Vital式门控ADSR + 动态低通 + MAX98357

这是电子乐器工程的第一个可上板音色内核：板载 `S1` 控制Do4（261.626 Hz），FPGA读取音色设计器生成的2048点波表、幅度/滤波ADSR与定点参数，再通过I2S送入MAX98357。运行中还可通过板载USB-UART修改ADSR时间、Sustain、滤波包络模式、Unison数量和失谐量。

## 模块链路

```text
S1 -> 同步/1ms消抖 -> 幅度/滤波 ADSR
                      1~8路失谐波表 DDS (Unison)
                                  ↓
                   动态一阶低通 -> 幅度包络 -> 主增益
                                  ↓
                  16-bit stereo I2S -> MAX98357 -> 喇叭
```

- 系统时钟：Primer 25K Dock 板载 50 MHz；不使用 PLL。
- I2S：BCLK = 3.125 MHz，LRCLK = 48.828125 kHz，16-bit、立体声双声道相同。
- 音高：Do4 = 261.626 Hz。
- 包络：Attack、Decay、Sustain、Release均由 `src/generated/tone_params.vh` 配置；按住后停在Sustain，任意阶段松键都会立即从当前电平进入Release。
- 曲率：幅度和滤波包络的A/D/R阶段分别读取256点Q0.16曲率ROM。
- 动态滤波：`dynamic_iir_lpf.v` 实现 `y[n]=y[n-1]+alpha[n]*(x[n]-y[n-1])`；滤波包络每个采样改变 `alpha`，即实时移动截止频率。
- Unison：1~8个同音振荡器对称失谐后混合并按声部数归一化，运行中可调整数量和每级失谐。
- 当前内置音色：软件生成的“钢琴（合成）”，不再使用三角波。

## 更换音色

1. 启动 `../tone_editor/run_tone_editor.bat`。
2. 编辑或加载音色，先在电脑上试听。
3. 点击“部署到FPGA工程”。软件会更新 `src/generated` 中的波表、曲率表、滤波LUT和 `tone_params.vh`。
4. 回到Gowin IDE重新运行综合、布局布线并下载。Verilog文件不需要手动修改。

BRAM初始化属于重新生成bitstream的方式，目前不是运行时UART换音色。

## 当前 CST 引脚与接线

> 重要：本工程按实际芯片选择 `GW5A-25B`、封装 `GW5A-LV25MG121NC1/I0`。Primer 25K 的板载 50 MHz 时钟接在 `E2`，该脚与 CPU/SSPI 功能复用。Dual-Purpose Pin 中必须同时勾选 **Use SSPI as regular IO** 和 **Use CPU as regular IO**，否则会出现 `PR2017 ... dedicated pin (CPU/SSPI)`。

| 信号 | FPGA 管脚 | 接线 |
| --- | --- | --- |
| `i2s_bclk` | F1 / J3-15 | MAX98357 `BCLK` |
| `i2s_lrclk` | F2 / J3-16 | MAX98357 `LRC` / `LRCLK` |
| `i2s_din` | A1 / J3-17 | MAX98357 `DIN` |
| `+5V` | J3-11 | MAX98357 `VIN` |
| `GND` | J3-12 | MAX98357 `GND` |
| `note_gate` | G8 | 0 / 3.3 V 触发输入；可用跳线从 3.3 V 拉高 |
| GND | Dock 的任一 GND | MAX98357 `GND`，必须共地 |
| `key_s1` | H11 | Dock 板载S1，按下发音、松开Release |
| `key_s2` | H10 | Dock 板载S2，按下复位 |
| `uart_rx_pin` | B3 | Dock板载USB-UART发送端到FPGA接收端，115200 8-N-1 |

MAX98357 模块单独供电：`VIN` 接其模块要求的 3.3 V 或 5 V，`GND` 与 Dock 共地；扬声器接模块 `SPK+`、`SPK-`，不要将扬声器任一端接地。若模块引出了 `SD` / `EN`，第一版将它接 3.3 V（否则芯片处于关断）；若引出了 `GAIN`，接 GND 可得到 12 dB 增益。常见模块若已在板上完成这些配置，无需重复接线。

> `BCLK/LRC/DIN` 均为 3.3 V 逻辑输出。接线前请在 Gowin Pin Planner 对照实际 Dock 的 PMOD 丝印确认 G5/F5/G7/G8 所在插针位置；若换用其他 PMOD 针脚，只改 `src/fpga_project.cst` 即可。

## 上板验证顺序

1. 编译并 SRAM 下载；不接喇叭时先用示波器/ILA 检查 `i2s_bclk`、`i2s_lrclk` 和 `i2s_din`。
2. 下载后按一下S2复位。S1未按：`i2s_din`输出静音采样。
3. 按住S1：执行当前音色的Attack/Decay，并保持在Sustain。
4. 在任意阶段松开S1：立即从当时的包络电平进入Release，最终回到静音。

## UART运行时控制

电脑通过Dock板载USB串口发送固定12字节帧：

```text
A5 5A CMD DATA0 DATA1 ... DATA7 CHECKSUM
```

`DATA`采用小端序，`CHECKSUM`是CMD和8个DATA字节的异或。时间命令同时发送32位阶段采样数和32位相位步进，保证256点曲线在指定时间内完整遍历。

可以直接使用 `tools/uart_synth_control.py`：

```text
python -m pip install pyserial
python tools/uart_synth_control.py COM6 amp-attack 25
python tools/uart_synth_control.py COM6 amp-decay 800
python tools/uart_synth_control.py COM6 amp-sustain 0.35
python tools/uart_synth_control.py COM6 amp-release 1200
python tools/uart_synth_control.py COM6 filter-follow 0
python tools/uart_synth_control.py COM6 filter-attack 80
python tools/uart_synth_control.py COM6 unison 8
python tools/uart_synth_control.py COM6 detune-cents 4
```

时间单位是毫秒；Sustain范围是0.0~1.0；Unison范围是1~8；`detune-cents`表示相邻失谐级的近似音分数。UART修改的是运行时寄存器，重新上电或按S2后恢复到 `tone_params.vh` 中的默认参数。
