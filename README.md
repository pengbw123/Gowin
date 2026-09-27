# Gowin FPGA Wavetable Synthesizer

本仓库包含 Tang Primer 25K 电子乐器原型中可以复用的两部分：

- `fpga_project/`：Gowin 工程、RTL、引脚/时序约束、钢琴音色初始化 ROM，以及 UART 实时参数控制工具。
- `tone_editor/`：用于创建、试听、保存并导出 FPGA 定点音色的 Python/Tkinter 桌面工具。

## 快速开始

1. 安装 Python 依赖：`python -m pip install -r tone_editor/requirements.txt`。
2. 运行 `tone_editor/run_tone_editor.bat`，选择或编辑音色。
3. 点击“部署到 FPGA 工程”，更新 `fpga_project/src/generated/`。
4. 使用 Gowin EDA 打开 `fpga_project/fpga_project.gprj`，重新综合、布局布线并下载。

音色编辑器测试：

```text
cd tone_editor
python -m unittest discover -s tests -v
```

仓库有意不包含 `impl/`、位流、综合数据库、用户级 IDE 设置、Python 缓存和日志。这些内容均可由源码重新生成。

更详细的硬件接线和 UART 命令见 `fpga_project/README.md`；音色编辑器格式和导出说明见 `tone_editor/README.md`。
