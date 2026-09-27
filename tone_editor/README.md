# Gowin FPGA 音色设计器

面向 Tang Primer 25K 电子乐器工程的离线音色设计工具。软件不依赖 PySide、sounddevice 或额外音频驱动，使用 Python 自带的 Tkinter 和 Windows `winsound`。

## 功能

- 2048点、16位 signed Q1.15 单周期波表。
- 前16次谐波编辑与鼠标手绘。
- Vital式门控ADSR：按住依次执行Attack/Decay并停在Sustain，任意阶段松键立即从当前电平进入Release。
- 幅度和滤波包络均支持Attack/Decay/Release曲率。
- 一阶动态低通IIR，支持跟随幅度ADSR或使用独立Filter ADSR。
- FPGA定点试听与高精度浮点试听。
- 内置钢琴、小提琴、管弦弦乐、铜管、管风琴和长笛预设。
- 保存和打开完整 `.tone.json` 配置。
- 导出波表、曲率ROM、滤波系数ROM和Verilog定点参数头文件。
- “部署到FPGA工程”一键覆盖 `../fpga_project/src/generated`，重新综合和下载即可试听新音色。

## 启动

双击：

```text
run_tone_editor.bat
```

或在当前目录运行：

```text
python main.py
```

唯一第三方依赖是 NumPy。当前开发机已经安装。

## FPGA导出文件

- `*_wavetable.mem`：2048行16位二补码十六进制数，可用于 `$readmemh` 初始化波表ROM。
- `filter_alpha_lut.mem`：256行unsigned Q0.16一阶低通系数。
- `*_amp/filter_*_curve.mem`：幅度/滤波包络的A/D/R曲率LUT，每张256点Q0.16。
- `*_params.vh`：采样率、ADSR采样数、阶段相位增量、Sustain、滤波索引和增益参数。
- `*_fpga_manifest.json`：导出格式说明。

FPGA v1推荐数据通路：

```text
波表DDS -> 一阶动态低通 -> 幅度ADSR -> 混音/限幅 -> I2S
```

波表和曲线ROM仍通过BRAM初始化；FPGA工程已经另外加入UART运行时控制，可修改幅度/滤波ADSR时间、Sustain、滤波包络跟随模式以及Unison参数。UART暂不重写整张波表。

## 快速上板试听

1. 在软件中选择预设或编辑波表、幅度包络和滤波包络。
2. 先点击“试听”确认声音，再点击“部署到FPGA工程”。
3. 打开 `../fpga_project/fpga_project.gprj`，重新运行综合、布局布线。
4. 下载新的bitstream。FPGA代码会自动读取固定路径下的新波表和参数，不需要手改Verilog。

## 测试

```text
python -m unittest discover -s tests -v
```
