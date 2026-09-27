import math
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import winsound

from audio_engine import build_envelope, filter_cutoff_curve, render_tone, write_preview_temp, write_wav
from fpga_export import export_tone, export_tone_to_fpga_project
from presets import build_presets
from tone_model import (
    FILTER_LUT_MAX_HZ,
    FILTER_LUT_MIN_HZ,
    SAMPLE_RATE,
    WAVETABLE_POINTS,
    Envelope,
    Tone,
    generate_wavetable,
    normalize_wavetable,
)


APP_TITLE = "Gowin FPGA 音色设计器"
BACKGROUND = "#10141d"
PANEL = "#18202d"
GRID = "#304057"
TEXT = "#e9eef7"
CYAN = "#50d5ff"
AMBER = "#ffbe55"
GREEN = "#70e1a1"

NOTES = {
    "C3  130.81 Hz": 130.8128,
    "G3  196.00 Hz": 195.9977,
    "C4  261.63 Hz": 261.6256,
    "E4  329.63 Hz": 329.6276,
    "G4  392.00 Hz": 391.9954,
    "C5  523.25 Hz": 523.2511,
}


class ToneDesigner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1060, 720)
        self.configure(bg=BACKGROUND)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.presets = build_presets()
        self.tone = Tone.from_dict(self.presets["钢琴（合成）"].to_dict())
        self.preview_path = None
        self.last_draw_index = None
        self.last_draw_value = None
        self.status_var = tk.StringVar(value="就绪：内置钢琴预设已加载")
        self._configure_style()
        self._build_variables()
        self._build_ui()
        self._envelope_redraw_job = None
        self._install_envelope_traces()
        self.load_tone_into_ui(self.tone)

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=BACKGROUND, foreground=TEXT, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Hint.TLabel", background=PANEL, foreground="#aebbd0", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(12, 7), foreground="#081018", background=CYAN)
        style.map("Accent.TButton", background=[("active", "#8be5ff")])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10), padding=(18, 8))
        style.configure("TLabelframe", background=PANEL, foreground=TEXT)
        style.configure("TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Microsoft YaHei UI", 10, "bold"))

    def _build_variables(self):
        self.preset_var = tk.StringVar(value="钢琴（合成）")
        self.name_var = tk.StringVar(value=self.tone.name)
        self.note_var = tk.StringVar(value="C4  261.63 Hz")
        self.hold_var = tk.DoubleVar(value=1.8)
        self.fixed_var = tk.BooleanVar(value=True)
        self.harmonic_vars = [tk.DoubleVar(value=0.0) for _ in range(16)]

        self.amp_vars = {
            "attack_ms": tk.DoubleVar(), "decay_ms": tk.DoubleVar(),
            "sustain": tk.DoubleVar(), "release_ms": tk.DoubleVar(),
            "attack_curve": tk.DoubleVar(), "decay_curve": tk.DoubleVar(),
            "release_curve": tk.DoubleVar(),
        }
        self.filter_enabled_var = tk.BooleanVar(value=True)
        self.filter_follow_var = tk.BooleanVar(value=True)
        self.filter_min_var = tk.DoubleVar(value=700.0)
        self.filter_max_var = tk.DoubleVar(value=12000.0)
        self.filter_amount_var = tk.DoubleVar(value=1.0)
        self.pressure_amount_var = tk.DoubleVar(value=0.0)
        self.filter_env_vars = {
            "attack_ms": tk.DoubleVar(), "decay_ms": tk.DoubleVar(),
            "sustain": tk.DoubleVar(), "release_ms": tk.DoubleVar(),
            "attack_curve": tk.DoubleVar(), "decay_curve": tk.DoubleVar(),
            "release_curve": tk.DoubleVar(),
        }
        self.master_gain_var = tk.DoubleVar(value=0.72)

    def _build_ui(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")

        toolbar = ttk.Frame(self, padding=(18, 2, 18, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="预设").pack(side="left", padx=(0, 6))
        preset_box = ttk.Combobox(toolbar, textvariable=self.preset_var, values=list(self.presets.keys()), state="readonly", width=18)
        preset_box.pack(side="left", padx=(0, 12))
        preset_box.bind("<<ComboboxSelected>>", self.on_preset_selected)
        ttk.Label(toolbar, text="音色名称").pack(side="left", padx=(0, 6))
        ttk.Entry(toolbar, textvariable=self.name_var, width=22).pack(side="left", padx=(0, 12))
        ttk.Button(toolbar, text="打开配置", command=self.open_tone).pack(side="left", padx=3)
        ttk.Button(toolbar, text="保存配置", command=self.save_tone).pack(side="left", padx=3)
        ttk.Button(toolbar, text="导出FPGA文件", command=self.export_fpga).pack(side="left", padx=3)
        ttk.Button(toolbar, text="部署到FPGA工程", command=self.deploy_fpga_project).pack(side="left", padx=3)
        ttk.Button(toolbar, text="试听", style="Accent.TButton", command=self.play_preview).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="停止", command=self.stop_preview).pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        wave_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        env_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        preview_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        notebook.add(wave_tab, text="波表与谐波")
        notebook.add(env_tab, text="包络与动态滤波")
        notebook.add(preview_tab, text="试听与导出说明")
        self._build_wave_tab(wave_tab)
        self._build_envelope_tab(env_tab)
        self._build_preview_tab(preview_tab)

        status = tk.Label(self, textvariable=self.status_var, anchor="w", bg="#0b0f16", fg="#b8c5d8", padx=16, pady=7, font=("Microsoft YaHei UI", 9))
        status.pack(fill="x", side="bottom")

    def _build_wave_tab(self, parent):
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent, style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(left, text="单周期波表 · 2048点 signed Q1.15", style="Panel.TLabel").pack(anchor="w")
        ttk.Label(left, text="在图中按住鼠标左键可直接绘制；水平中心线为零电平。", style="Hint.TLabel").pack(anchor="w", pady=(2, 8))
        self.wave_canvas = tk.Canvas(left, bg="#0b1018", highlightthickness=1, highlightbackground=GRID, cursor="crosshair")
        self.wave_canvas.pack(fill="both", expand=True)
        self.wave_canvas.bind("<Configure>", lambda _event: self.draw_waveform())
        self.wave_canvas.bind("<Button-1>", self.start_wave_draw)
        self.wave_canvas.bind("<B1-Motion>", self.continue_wave_draw)
        self.wave_canvas.bind("<ButtonRelease-1>", self.end_wave_draw)

        controls = ttk.Frame(left, style="Panel.TFrame")
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="由谐波重新生成", command=self.generate_from_harmonics).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="归一化/去直流", command=self.normalize_wave).pack(side="left", padx=6)
        ttk.Button(controls, text="纯正弦", command=self.make_sine).pack(side="left", padx=6)

        right = ttk.LabelFrame(parent, text="前16次谐波幅值", padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        for i, variable in enumerate(self.harmonic_vars):
            ttk.Label(right, text="H{:02d}".format(i + 1), style="Panel.TLabel", width=5).grid(row=i, column=0, sticky="w", pady=2)
            scale = ttk.Scale(right, from_=0.0, to=1.0, variable=variable)
            scale.grid(row=i, column=1, sticky="ew", padx=5, pady=2)
            label = ttk.Label(right, textvariable=variable, style="Hint.TLabel", width=7)
            label.grid(row=i, column=2, sticky="e")
        ttk.Label(right, text="谐波滑块改变后，点击“由谐波重新生成”。手绘不会反向推导谐波。", style="Hint.TLabel", wraplength=330).grid(row=16, column=0, columnspan=3, sticky="ew", pady=(10, 0))

    def _number_entry(self, parent, row, label, variable, unit="", column=0):
        base = column * 3
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=base, sticky="w", padx=(0, 6), pady=5)
        ttk.Entry(parent, textvariable=variable, width=11).grid(row=row, column=base + 1, sticky="w", pady=5)
        ttk.Label(parent, text=unit, style="Hint.TLabel").grid(row=row, column=base + 2, sticky="w", padx=(4, 14), pady=5)

    def _build_envelope_fields(self, frame, variables):
        self._number_entry(frame, 0, "Attack", variables["attack_ms"], "ms")
        self._number_entry(frame, 1, "Decay", variables["decay_ms"], "ms")
        self._number_entry(frame, 2, "Sustain", variables["sustain"], "0～1")
        self._number_entry(frame, 3, "Release", variables["release_ms"], "ms")
        self._number_entry(frame, 0, "Attack曲率", variables["attack_curve"], "-1～1", 1)
        self._number_entry(frame, 1, "Decay曲率", variables["decay_curve"], "-1～1", 1)
        self._number_entry(frame, 2, "Release曲率", variables["release_curve"], "-1～1", 1)

    def _build_envelope_tab(self, parent):
        parent.columnconfigure(0, weight=0, minsize=535)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        controls_column = ttk.Frame(parent, style="Panel.TFrame")
        controls_column.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        envelope_notebook = ttk.Notebook(controls_column)
        envelope_notebook.pack(fill="both", expand=True, pady=(0, 8))

        amp_frame = ttk.LabelFrame(controls_column, text="幅度ADSR", padding=10)
        envelope_notebook.add(amp_frame, text="幅度 ADSR")
        self._build_envelope_fields(amp_frame, self.amp_vars)
        self._number_entry(amp_frame, 4, "主音量", self.master_gain_var, "0～1")

        filter_frame = ttk.LabelFrame(controls_column, text="一阶动态低通 IIR", padding=10)
        filter_frame.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(filter_frame, text="启用滤波器", variable=self.filter_enabled_var).grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(filter_frame, text="FPGA v1：跟随幅度ADSR", variable=self.filter_follow_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        self._number_entry(filter_frame, 2, "最低截止频率", self.filter_min_var, "Hz")
        self._number_entry(filter_frame, 3, "最高截止频率", self.filter_max_var, "Hz")
        self._number_entry(filter_frame, 4, "包络作用深度", self.filter_amount_var, "0～1")
        self._number_entry(filter_frame, 5, "压力作用深度", self.pressure_amount_var, "保存备用")

        filter_env = ttk.LabelFrame(controls_column, text="独立Filter ADSR（取消“跟随幅度”时生效）", padding=10)
        envelope_notebook.add(filter_env, text="独立 Filter ADSR")
        self._build_envelope_fields(filter_env, self.filter_env_vars)

        chart_frame = ttk.Frame(parent, style="Panel.TFrame")
        chart_frame.grid(row=0, column=1, sticky="nsew")
        chart_frame.rowconfigure(1, weight=1)
        chart_frame.rowconfigure(2, weight=1)
        chart_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(chart_frame, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(bar, text="应用参数并刷新曲线", command=self.apply_parameters).pack(side="left")
        ttk.Label(
            bar,
            text="上图：幅度ADSR（0～1）\n下图：IIR实际截止频率（Hz，对数）；参数修改后自动刷新",
            style="Hint.TLabel",
            justify="left",
        ).pack(side="left", padx=12)
        self.amp_env_canvas = tk.Canvas(
            chart_frame, bg="#0b1018", highlightthickness=1, highlightbackground=GRID, height=150
        )
        self.amp_env_canvas.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.filter_cutoff_canvas = tk.Canvas(
            chart_frame, bg="#0b1018", highlightthickness=1, highlightbackground=GRID, height=150
        )
        self.filter_cutoff_canvas.grid(row=2, column=0, sticky="nsew")
        self.amp_env_canvas.bind("<Configure>", lambda _event: self._schedule_envelope_redraw())
        self.filter_cutoff_canvas.bind("<Configure>", lambda _event: self._schedule_envelope_redraw())

    def _install_envelope_traces(self):
        variables = list(self.amp_vars.values()) + list(self.filter_env_vars.values())
        variables.extend([
            self.hold_var,
            self.filter_enabled_var,
            self.filter_follow_var,
            self.filter_min_var,
            self.filter_max_var,
            self.filter_amount_var,
        ])
        for variable in variables:
            variable.trace_add("write", self._schedule_envelope_redraw)

    def _schedule_envelope_redraw(self, *_args):
        if self._envelope_redraw_job is not None:
            try:
                self.after_cancel(self._envelope_redraw_job)
            except tk.TclError:
                pass
        self._envelope_redraw_job = self.after(70, self.draw_envelopes)

    def _build_preview_tab(self, parent):
        preview = ttk.LabelFrame(parent, text="试听", padding=16)
        preview.pack(fill="x")
        ttk.Label(preview, text="音高", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Combobox(preview, textvariable=self.note_var, values=list(NOTES.keys()), state="readonly", width=20).grid(row=0, column=1, sticky="w", pady=6)
        ttk.Label(preview, text="按住时间", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(preview, textvariable=self.hold_var, width=12).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(preview, text="秒", style="Hint.TLabel").grid(row=1, column=2, sticky="w", padx=5)
        ttk.Checkbutton(preview, text="使用FPGA定点仿真试听（推荐）", variable=self.fixed_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Button(preview, text="播放当前音色", style="Accent.TButton", command=self.play_preview).grid(row=3, column=0, sticky="w", pady=(12, 4))
        ttk.Button(preview, text="导出试听WAV", command=self.export_preview_wav).grid(row=3, column=1, sticky="w", pady=(12, 4))

        info = ttk.LabelFrame(parent, text="FPGA导出内容", padding=16)
        info.pack(fill="both", expand=True, pady=(14, 0))
        text = (
            "保存配置：生成 .tone.json，包含波表、谐波、幅度ADSR、Filter ADSR与元数据。\n\n"
            "导出FPGA文件：\n"
            "  · *_wavetable.mem：2048行16位二补码十六进制波表\n"
            "  · filter_alpha_lut.mem：256行Q0.16低通系数\n"
            "  · *_params.vh：ADSR采样数/相位增量、滤波索引和增益定点参数\n"
            "  · *_amp/filter_*_curve.mem：A/D/R曲率Q0.16查找表\n"
            "  · *_fpga_manifest.json：文件格式清单\n\n"
            "当前版本采用BRAM初始化，不包含UART上传。音色预设是合成近似音色，不使用商业采样素材。"
        )
        ttk.Label(info, text=text, style="Panel.TLabel", justify="left", wraplength=900).pack(anchor="nw")

    def _set_env_vars(self, variables, env):
        for key, variable in variables.items():
            variable.set(getattr(env, key))

    def _read_env_vars(self, variables):
        def clamp(value, low, high):
            return max(low, min(high, float(value)))
        return Envelope(
            attack_ms=max(0.1, float(variables["attack_ms"].get())),
            decay_ms=max(0.1, float(variables["decay_ms"].get())),
            sustain=clamp(variables["sustain"].get(), 0.0, 1.0),
            release_ms=max(0.1, float(variables["release_ms"].get())),
            attack_curve=clamp(variables["attack_curve"].get(), -1.0, 1.0),
            decay_curve=clamp(variables["decay_curve"].get(), -1.0, 1.0),
            release_curve=clamp(variables["release_curve"].get(), -1.0, 1.0),
        )

    def load_tone_into_ui(self, tone):
        self.tone = Tone.from_dict(tone.to_dict())
        self.name_var.set(self.tone.name)
        for var, value in zip(self.harmonic_vars, self.tone.harmonics):
            var.set(round(value, 4))
        self._set_env_vars(self.amp_vars, self.tone.amplitude_envelope)
        self.filter_enabled_var.set(self.tone.filter.enabled)
        self.filter_follow_var.set(self.tone.filter.follow_amplitude_envelope)
        self.filter_min_var.set(self.tone.filter.min_cutoff_hz)
        self.filter_max_var.set(self.tone.filter.max_cutoff_hz)
        self.filter_amount_var.set(self.tone.filter.envelope_amount)
        self.pressure_amount_var.set(self.tone.filter.pressure_amount)
        self._set_env_vars(self.filter_env_vars, self.tone.filter.envelope)
        self.master_gain_var.set(self.tone.master_gain)
        self.after_idle(self.draw_waveform)
        self.after_idle(self.draw_envelopes)

    def update_tone_from_ui(self):
        self.tone.name = self.name_var.get().strip() or "Unnamed Tone"
        self.tone.harmonics = [float(v.get()) for v in self.harmonic_vars]
        self.tone.amplitude_envelope = self._read_env_vars(self.amp_vars)
        self.tone.filter.enabled = bool(self.filter_enabled_var.get())
        self.tone.filter.follow_amplitude_envelope = bool(self.filter_follow_var.get())
        self.tone.filter.min_cutoff_hz = max(FILTER_LUT_MIN_HZ, float(self.filter_min_var.get()))
        self.tone.filter.max_cutoff_hz = min(FILTER_LUT_MAX_HZ, max(self.tone.filter.min_cutoff_hz, float(self.filter_max_var.get())))
        self.tone.filter.envelope_amount = max(0.0, min(1.0, float(self.filter_amount_var.get())))
        self.tone.filter.pressure_amount = max(0.0, min(1.0, float(self.pressure_amount_var.get())))
        self.tone.filter.envelope = self._read_env_vars(self.filter_env_vars)
        self.tone.master_gain = max(0.0, min(1.0, float(self.master_gain_var.get())))
        self.tone.ensure_valid()

    def draw_waveform(self):
        if not hasattr(self, "wave_canvas"):
            return
        canvas = self.wave_canvas
        width = max(2, canvas.winfo_width())
        height = max(2, canvas.winfo_height())
        canvas.delete("all")
        for i in range(1, 8):
            x = i * width / 8
            canvas.create_line(x, 0, x, height, fill=GRID)
        for i in range(1, 4):
            y = i * height / 4
            canvas.create_line(0, y, width, y, fill=GRID)
        canvas.create_line(0, height / 2, width, height / 2, fill="#60738f", width=1)
        wave = np.asarray(self.tone.wavetable, dtype=np.float64)
        if wave.size != WAVETABLE_POINTS:
            return
        points = []
        for index, value in enumerate(wave):
            x = index * (width - 1) / (WAVETABLE_POINTS - 1)
            y = (0.5 - value / 65536.0) * (height - 1)
            points.extend((x, y))
        canvas.create_line(points, fill=CYAN, width=1.4, smooth=False)
        canvas.create_text(10, 10, anchor="nw", text="+32767", fill="#75859b", font=("Consolas", 9))
        canvas.create_text(10, height - 10, anchor="sw", text="-32768", fill="#75859b", font=("Consolas", 9))

    def _draw_position(self, event):
        width = max(1, self.wave_canvas.winfo_width() - 1)
        height = max(1, self.wave_canvas.winfo_height() - 1)
        index = int(round(max(0, min(width, event.x)) / width * (WAVETABLE_POINTS - 1)))
        value = int(round((0.5 - max(0, min(height, event.y)) / height) * 65535.0))
        value = max(-32768, min(32767, value))
        return index, value

    def start_wave_draw(self, event):
        self.last_draw_index, self.last_draw_value = self._draw_position(event)
        self.tone.wavetable[self.last_draw_index] = self.last_draw_value
        self.draw_waveform()

    def continue_wave_draw(self, event):
        index, value = self._draw_position(event)
        if self.last_draw_index is None:
            self.last_draw_index, self.last_draw_value = index, value
        start, end = sorted((self.last_draw_index, index))
        if start == end:
            self.tone.wavetable[index] = value
        else:
            first = self.last_draw_value if self.last_draw_index <= index else value
            last = value if self.last_draw_index <= index else self.last_draw_value
            interpolated = np.linspace(first, last, end - start + 1)
            for offset, sample in enumerate(interpolated):
                self.tone.wavetable[start + offset] = int(round(sample))
        self.last_draw_index, self.last_draw_value = index, value
        self.draw_waveform()

    def end_wave_draw(self, _event):
        self.last_draw_index = None
        self.last_draw_value = None
        self.status_var.set("波表已手绘修改；保存配置或导出FPGA文件可保留结果")

    def generate_from_harmonics(self):
        harmonics = [float(v.get()) for v in self.harmonic_vars]
        self.tone.harmonics = harmonics
        self.tone.wavetable = generate_wavetable(harmonics, self.tone.harmonic_phases).tolist()
        self.draw_waveform()
        self.status_var.set("已由前16次谐波重新生成2048点波表")

    def normalize_wave(self):
        self.tone.wavetable = normalize_wavetable(self.tone.wavetable).tolist()
        self.draw_waveform()
        self.status_var.set("已去除直流并归一化至96%满量程")

    def make_sine(self):
        for i, variable in enumerate(self.harmonic_vars):
            variable.set(1.0 if i == 0 else 0.0)
        self.tone.harmonic_phases = [0.0] * 16
        self.generate_from_harmonics()

    def apply_parameters(self):
        try:
            self.update_tone_from_ui()
            self.draw_envelopes()
            self.status_var.set("包络和滤波参数已应用")
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("参数错误", "请检查输入的数值。\n\n{}".format(exc))

    def draw_envelopes(self):
        self._envelope_redraw_job = None
        if not hasattr(self, "amp_env_canvas") or not hasattr(self, "filter_cutoff_canvas"):
            return
        try:
            amp = self._read_env_vars(self.amp_vars)
            filt = self._read_env_vars(self.filter_env_vars)
            follow = bool(self.filter_follow_var.get())
            amount = max(0.0, min(1.0, float(self.filter_amount_var.get())))
            minimum_hz = float(self.filter_min_var.get())
            maximum_hz = float(self.filter_max_var.get())
            hold = max(0.05, min(8.0, float(self.hold_var.get())))
        except (ValueError, tk.TclError):
            return

        # build_envelope uses SAMPLE_RATE, so this chart must use real audio
        # sample counts as well. The old 1000-point pseudo timeline compressed
        # ordinary millisecond ADSR stages until the curve looked nearly flat.
        release_seconds = max(amp.release_ms, filt.release_ms) / 1000.0
        total_seconds = hold + min(12.0, release_seconds) + 0.10
        total_samples = max(2, int(round(total_seconds * SAMPLE_RATE)))
        note_on_samples = min(total_samples - 1, int(round(hold * SAMPLE_RATE)))
        amp_full = build_envelope(amp, note_on_samples, total_samples)
        filter_full = amp_full if follow else build_envelope(filt, note_on_samples, total_samples)
        filter_control = np.clip(filter_full * amount, 0.0, 1.0)
        cutoff_full = filter_cutoff_curve(filter_control, minimum_hz, maximum_hz)

        display_count = max(500, min(1800, max(
            self.amp_env_canvas.winfo_width(), self.filter_cutoff_canvas.winfo_width()
        )))
        sample_indices = np.linspace(0, total_samples - 1, display_count).astype(np.int64)
        times = sample_indices.astype(np.float64) / SAMPLE_RATE
        amp_values = amp_full[sample_indices]
        cutoff_values = cutoff_full[sample_indices]
        gate_time = note_on_samples / SAMPLE_RATE

        self._draw_amplitude_chart(times, amp_values, total_seconds, gate_time)
        self._draw_cutoff_chart(times, cutoff_values, total_seconds, gate_time)

    @staticmethod
    def _format_frequency(value):
        if value >= 1000.0:
            return "{:.0f}k".format(value / 1000.0) if value % 1000 == 0 else "{:.1f}k".format(value / 1000.0)
        return "{:.0f}".format(value)

    def _draw_time_grid(self, canvas, title, total_seconds, gate_time, y_ticks, y_mapper):
        canvas.delete("all")
        width = max(120, canvas.winfo_width())
        height = max(80, canvas.winfo_height())
        left, right, top, bottom = 58, 12, 24, 25
        plot_w = max(1, width - left - right)
        plot_h = max(1, height - top - bottom)
        canvas.create_text(left, 6, anchor="nw", text=title, fill=TEXT, font=("Microsoft YaHei UI", 9, "bold"))
        for i in range(6):
            x = left + plot_w * i / 5.0
            seconds = total_seconds * i / 5.0
            canvas.create_line(x, top, x, top + plot_h, fill=GRID)
            canvas.create_text(x, top + plot_h + 5, anchor="n", text="{:.2g}s".format(seconds), fill="#91a0b5", font=("Consolas", 8))
        for value, label in y_ticks:
            y = y_mapper(value, top, plot_h)
            canvas.create_line(left, y, left + plot_w, y, fill=GRID)
            canvas.create_text(left - 6, y, anchor="e", text=label, fill="#91a0b5", font=("Consolas", 8))
        gate_x = left + plot_w * min(1.0, max(0.0, gate_time / total_seconds))
        canvas.create_line(gate_x, top, gate_x, top + plot_h, fill=GREEN, dash=(5, 4), width=1.4)
        canvas.create_text(min(gate_x + 6, width - 62), top + 3, anchor="nw", text="Note Off", fill=GREEN, font=("Consolas", 8))
        return left, top, plot_w, plot_h

    def _draw_amplitude_chart(self, times, values, total_seconds, gate_time):
        mapper = lambda value, top, plot_h: top + (1.0 - float(value)) * plot_h
        ticks = [(0.0, "0"), (0.25, "0.25"), (0.5, "0.50"), (0.75, "0.75"), (1.0, "1.00")]
        left, top, plot_w, plot_h = self._draw_time_grid(
            self.amp_env_canvas, "幅度 ADSR", total_seconds, gate_time, ticks, mapper
        )
        points = []
        for time_value, amplitude in zip(times, values):
            points.extend((left + plot_w * time_value / total_seconds, mapper(amplitude, top, plot_h)))
        self.amp_env_canvas.create_line(points, fill=CYAN, width=2.2)

    def _draw_cutoff_chart(self, times, values, total_seconds, gate_time):
        graph_min = FILTER_LUT_MIN_HZ
        graph_max = FILTER_LUT_MAX_HZ
        log_min = math.log10(graph_min)
        log_span = math.log10(graph_max) - log_min

        def mapper(value, top, plot_h):
            clamped = max(graph_min, min(graph_max, float(value)))
            return top + (1.0 - (math.log10(clamped) - log_min) / log_span) * plot_h

        candidates = [80.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 18000.0]
        ticks = [(value, self._format_frequency(value)) for value in candidates if graph_min <= value <= graph_max]
        left, top, plot_w, plot_h = self._draw_time_grid(
            self.filter_cutoff_canvas, "动态低通截止频率 fc（Hz，对数刻度）", total_seconds, gate_time, ticks, mapper
        )
        if not bool(self.filter_enabled_var.get()):
            self.filter_cutoff_canvas.create_text(
                left + plot_w / 2, top + plot_h / 2, text="滤波器已旁路", fill=AMBER,
                font=("Microsoft YaHei UI", 11, "bold")
            )
            return
        points = []
        for time_value, cutoff in zip(times, values):
            points.extend((left + plot_w * time_value / total_seconds, mapper(cutoff, top, plot_h)))
        self.filter_cutoff_canvas.create_line(points, fill=AMBER, width=2.0)
        self.filter_cutoff_canvas.create_text(
            left + plot_w - 5, 7, anchor="ne",
            text="设定范围 {:.0f}～{:.0f} Hz".format(float(self.filter_min_var.get()), float(self.filter_max_var.get())),
            fill=AMBER, font=("Microsoft YaHei UI", 8)
        )

    def on_preset_selected(self, _event=None):
        selected = self.preset_var.get()
        if selected in self.presets:
            self.load_tone_into_ui(self.presets[selected])
            self.status_var.set("已加载预设：{}".format(selected))

    def save_tone(self):
        try:
            self.update_tone_from_ui()
            path = filedialog.asksaveasfilename(
                title="保存音色配置",
                defaultextension=".tone.json",
                filetypes=[("Gowin tone", "*.tone.json"), ("JSON", "*.json")],
                initialfile=self.tone.name + ".tone.json",
            )
            if path:
                self.tone.save(path)
                self.status_var.set("已保存：{}".format(path))
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def open_tone(self):
        path = filedialog.askopenfilename(
            title="打开音色配置",
            filetypes=[("Gowin tone", "*.tone.json"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            tone = Tone.load(path)
            self.preset_var.set("自定义")
            self.load_tone_into_ui(tone)
            self.status_var.set("已打开：{}".format(path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def export_fpga(self):
        try:
            self.update_tone_from_ui()
            directory = filedialog.askdirectory(title="选择FPGA导出目录")
            if not directory:
                return
            paths = export_tone(self.tone, directory)
            self.status_var.set("已导出FPGA文件到：{}".format(directory))
            messagebox.showinfo("导出完成", "已生成：\n\n{}".format("\n".join(p.name for p in paths)))
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def deploy_fpga_project(self):
        try:
            self.update_tone_from_ui()
            project_directory = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fpga_project"))
            project_file = os.path.join(project_directory, "fpga_project.gprj")
            if not os.path.isfile(project_file):
                raise FileNotFoundError("没有找到FPGA工程：{}".format(project_file))
            paths = export_tone_to_fpga_project(self.tone, project_directory)
            self.status_var.set("音色已部署到FPGA工程；重新综合、布局布线并下载即可试听")
            messagebox.showinfo(
                "部署完成",
                "已更新工程中的波表、ADSR曲线和定点参数：\n\n{}\n\n"
                "下一步在 Gowin IDE 中重新运行综合、布局布线并下载。".format(
                    "\n".join(os.path.basename(str(path)) for path in paths)
                ),
            )
        except Exception as exc:
            messagebox.showerror("部署失败", str(exc))

    def _render_preview(self, tone, frequency, hold, fixed_point, note_label, export_path=None):
        try:
            samples = render_tone(tone, frequency, hold, fixed_point)
            if export_path:
                write_wav(samples, export_path)
                self.after(0, lambda: self.status_var.set("已导出试听WAV：{}".format(export_path)))
                return
            preview_path = write_preview_temp(samples)
            old_path = self.preview_path
            self.preview_path = preview_path
            winsound.PlaySound(preview_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            if old_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
            self.after(0, lambda: self.status_var.set("正在试听：{} · {}".format(tone.name, note_label)))
        except Exception as exc:
            self.after(0, lambda exc=exc: messagebox.showerror("试听失败", str(exc)))

    def play_preview(self):
        try:
            self.update_tone_from_ui()
            tone = Tone.from_dict(self.tone.to_dict())
            frequency = NOTES.get(self.note_var.get(), 261.6256)
            hold = max(0.1, min(8.0, float(self.hold_var.get())))
            fixed_point = bool(self.fixed_var.get())
            note_label = self.note_var.get()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self.status_var.set("正在生成FPGA定点试听数据……")
        threading.Thread(
            target=self._render_preview,
            args=(tone, frequency, hold, fixed_point, note_label),
            daemon=True,
        ).start()

    def stop_preview(self):
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.status_var.set("试听已停止")

    def export_preview_wav(self):
        path = filedialog.asksaveasfilename(
            title="导出试听WAV",
            defaultextension=".wav",
            filetypes=[("Wave audio", "*.wav")],
            initialfile=(self.name_var.get().strip() or "tone") + "_preview.wav",
        )
        if path:
            try:
                self.update_tone_from_ui()
                tone = Tone.from_dict(self.tone.to_dict())
                frequency = NOTES.get(self.note_var.get(), 261.6256)
                hold = max(0.1, min(8.0, float(self.hold_var.get())))
                fixed_point = bool(self.fixed_var.get())
                note_label = self.note_var.get()
            except Exception as exc:
                messagebox.showerror("参数错误", str(exc))
                return
            self.status_var.set("正在渲染WAV……")
            threading.Thread(
                target=self._render_preview,
                args=(tone, frequency, hold, fixed_point, note_label, path),
                daemon=True,
            ).start()

    def on_close(self):
        winsound.PlaySound(None, winsound.SND_PURGE)
        if self.preview_path and os.path.exists(self.preview_path):
            try:
                os.remove(self.preview_path)
            except OSError:
                pass
        self.destroy()


if __name__ == "__main__":
    app = ToneDesigner()
    app.mainloop()
