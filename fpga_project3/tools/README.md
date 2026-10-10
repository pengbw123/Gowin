# Tools目录导航

现有程序按用途分为三组。为避免破坏已经验证过的启动方式，本次不移动旧文件，只增加统一说明和阶段二入口。

不想区分文件名时可直接运行`run_tools_menu.bat`，用数字选择音色编辑器、阶段一或阶段二。保留所有旧入口是为了不破坏已经验证过的脚本和README路径。

## 直接运行的程序

| 阶段 | 启动文件 | 用途 |
|---|---|---|
| FPGA音色编辑 | `run_harmonic_editor.bat` | 手工编辑C2/C3/C4/C6四基准幅度、相对衰减、ADSR并通过UART发送 |
| 阶段一 | `run_instrument_analyzer.bat` | 分析一个真实乐器单音，观察热力图、32谐波绝对幅度和绝对衰减 |
| 阶段二 | `run_timbre_builder.bat` | 同时分析C2/C3/C4/C6，分离总包络和相对谐波衰减，A/B试听并导出FPGA参数 |

首次运行任一图形程序前执行一次`install_python_requirements.bat`。

## 程序与公共模块

- `instrument_spectrum_analyzer.py`：阶段一界面，作为已保存基线不再承担阶段二逻辑；
- `instrument_analysis_core.py`：STFT、基频跟踪和绝对谐波测量；
- `instrument_timbre_builder.py`：阶段二四基准界面；
- `instrument_timbre_builder_core.py`：总包络分离、相对衰减拟合和Q0.24系数生成；
- `harmonic_editor.py`：现有UART音色编辑器；
- `additive_uart_protocol.py`：UART数据包和参数换算；
- `generate_additive_tables.py`、`generate_midi_phase_table.py`：生成FPGA初始化表；
- `uart_synth_control.py`：命令行UART调试工具；
- `test_*.py`：参数提取单元测试；
- `audiofiles/`：用户自己的参考录音，不属于程序依赖。

## 为什么阶段二不能直接导出阶段一的衰减时间

FPGA中第k次谐波的输出近似为：

```text
y_k(t) = A_k × PartialDecay_k(t) × ADSR(t) × sin(kωt)
```

阶段一测得的是录音里的绝对幅度`H_k(t)`，其中已经包含真实乐器的总响度包络。如果把它的绝对时间常数直接写成`PartialDecay_k`，再乘一次ADSR，总衰减会过快。

阶段二先计算：

```text
G(t)   = sqrt(sum(H_k(t)^2))
R_k(t) = H_k(t) / G(t)
```

然后只把`R_k(t)`的变化拟合为FPGA单谐波衰减。ADSR负责总响度，单谐波系数只负责音色随时间变暗或变亮。当前RTL不支持相对幅度随时间增长，因此遇到正斜率时导出`alpha=0`并在界面中标记“相对上升→保持”。

阶段二JSON顶层的`anchors`是可直接发送给当前25K工程的4×16参数；`analysis_32`保存完整4×32分析，供A/B试听和以后迁移32谐波硬件使用。`harmonic_editor.py`现在直接显示和编辑C2/C3/C4/C6各自的16项相对衰减，载入后不会再经过全局频率曲线近似，直接点击“发送全部”即可。超过UART协议上限的极慢衰减会在FPGA发送字段中限制为120000 ms。

## 吉他扫弦模式

`harmonic_editor.py`顶部的“演奏方式”区域可以切换普通演奏和吉他扫弦。扫弦模式下先在MIDI键盘上按住3～4个音形成和弦，再使用Pad 1下扫、Pad 2上扫。Pad力度主要控制音量，同时影响弦间隔和高次谐波亮度。

- `弦间隔`：相邻和弦音触发的基础时间，推荐12～25 ms；
- `Gate`：每个音从Note On到Note Off的时间，推荐80～180 ms；
- `力度→速度`：0表示力度不改变速度，127表示重击时最多把间隔缩短约一半；
- `力度→亮度`：0表示不改变谐波，值越大，轻击时高次谐波越弱。

点击“只发送演奏设置”不会重发64组谐波；点击“发送全部”会把音色、ADSR和演奏设置一起发送。板上复位后的默认模式仍为普通演奏，避免未打开上位机时琴键没有直接声音。
