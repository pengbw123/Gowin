"""Interactive spectrum and harmonic analyser for isolated instrument notes.

Phase 1 deliberately stays independent from the UART tone editor.  It reads
WAV/AIFF/FLAC, presents linked time/frequency views, tracks up to 32 partials,
fits their exponential decays, and exports reproducible JSON/CSV analysis.
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf
from scipy import signal

from matplotlib import rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure

from instrument_analysis_core import (
    AnalysisResult,
    analyse_audio,
    frequency_to_midi,
    midi_frequency,
    midi_note_name,
)


APP_TITLE = "乐器音色频谱分析器 - 第一阶段"
SPECTROGRAM_CMAP = LinearSegmentedColormap.from_list(
    "instrument_heat",
    (
        "#020207", "#12082c", "#35106b", "#243ca5", "#087fca",
        "#10c7c4", "#62dc65", "#d6eb3d", "#ff9d24", "#ff321d",
    ),
    N=256,
)
SUPPORTED_FILES = (
    ("音频文件", "*.wav *.aif *.aiff *.flac"),
    ("WAV", "*.wav"),
    ("AIFF", "*.aif *.aiff"),
    ("FLAC", "*.flac"),
    ("所有文件", "*.*"),
)


def parse_midi_text(text: str) -> Optional[float]:
    value = text.strip()
    if not value:
        return None
    try:
        number = float(value)
        if 0.0 <= number <= 127.0:
            return number
    except ValueError:
        pass
    match = re.fullmatch(r"\s*([A-Ga-g])([#b]?)(-?\d+)\s*", value)
    if not match:
        raise ValueError("MIDI音高请填写0~127，或C4、F#3、Bb2一类音名")
    semitones = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pitch = semitones[match.group(1).upper()]
    accidental = match.group(2)
    if accidental == "#":
        pitch += 1
    elif accidental == "b":
        pitch -= 1
    note = (int(match.group(3)) + 1) * 12 + pitch
    if note < 0 or note > 127:
        raise ValueError("音名超出MIDI 0~127范围")
    return float(note)


def note_from_filename(path: Path) -> Optional[int]:
    # Iowa names commonly contain tokens such as E2, C4 or Bb4.  The first
    # pitch is only a suggestion; range recordings still require manual choice.
    matches = re.findall(r"(?<![A-Za-z])([A-Ga-g])([#b]?)(-?\d)(?!\d)", path.stem)
    if not matches:
        return None
    try:
        return int(parse_midi_text("".join(matches[0])))
    except (TypeError, ValueError):
        return None


def finite_or_none(value: float, digits: int = 7) -> Optional[float]:
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def array_to_json(values: np.ndarray, digits: int = 7) -> List[Any]:
    if values.ndim == 1:
        return [finite_or_none(value, digits) for value in values]
    return [array_to_json(row, digits) for row in values]


def analysis_to_dict(result: AnalysisResult, source_path: Path, channels: int,
                     channel_mode: str) -> Dict[str, Any]:
    harmonics = []
    for index, fit in enumerate(result.harmonic_fits):
        harmonics.append({
            "number": index + 1,
            "frequency_hz": array_to_json(result.harmonic_frequency_hz[index], 4),
            "amplitude_relative": array_to_json(
                result.harmonic_amplitude_relative[index], 8
            ),
            "amplitude_db": array_to_json(result.harmonic_db[index], 4),
            "phase_rad": array_to_json(result.harmonic_phase_rad[index], 6),
            "decay_fit": {
                "tau_ms": finite_or_none(fit.tau_ms) if fit.tau_ms is not None else None,
                "t60_ms": finite_or_none(fit.t60_ms) if fit.t60_ms is not None else None,
                "r_squared": finite_or_none(fit.r_squared) if fit.r_squared is not None else None,
                "peak_time_s": finite_or_none(fit.peak_time_s),
                "peak_amplitude": finite_or_none(fit.peak_amplitude, 10),
            },
        })
    return {
        "format": "gowin-instrument-analysis-v1",
        "source": {
            "path": str(source_path),
            "file_name": source_path.name,
            "sample_rate_hz": result.sample_rate_hz,
            "channels": channels,
            "channel_mode": channel_mode,
        },
        "selection": {
            "start_s": result.selection_start_s,
            "end_s": result.selection_end_s,
        },
        "analysis": {
            "fft_size": result.n_fft,
            "hop_length": result.hop_length,
            "frame_times_s": array_to_json(result.times_s, 7),
            "fundamental_hz": array_to_json(result.fundamental_hz, 5),
            "median_fundamental_hz": finite_or_none(result.median_fundamental_hz, 5),
            "estimated_midi": finite_or_none(result.estimated_midi, 4),
            "estimated_note": midi_note_name(int(round(result.estimated_midi))),
            "harmonics": harmonics,
        },
    }


class InstrumentSpectrumAnalyzer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1600x900")
        self.minsize(1120, 700)

        rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        rcParams["axes.unicode_minus"] = False

        self.source_path: Optional[Path] = None
        self.source_audio: Optional[np.ndarray] = None
        self.source_rate = 0.0
        self.source_channels = 0
        self.result: Optional[AnalysisResult] = None
        self.current_frame = 0
        self.selected_harmonic = 0
        self.analysis_running = False
        self.playback_state = "stopped"
        self.playback_audio: Optional[np.ndarray] = None
        self.playback_sample_rate = 0.0
        self.playback_selection_start_s = 0.0
        self.playback_duration_s = 0.0
        self.playback_gain_db = 0.0
        self.playback_last_analysis_ui_s = -1.0
        self.playback_elapsed_before_start = 0.0
        self.playback_clock_start = 0.0
        self.playback_timer: Optional[str] = None
        self.hover_frequency_bin: Optional[int] = None
        self.large_spectrogram_window: Optional[tk.Toplevel] = None
        self.large_playback_line: Any = None
        self.large_selection_line: Any = None
        self.large_spectrogram_canvas: Any = None
        self.display_max_frequency = 0.0
        self.spectrogram_vmin = -80.0
        self.spectrogram_vmax = 0.0

        self.path_var = tk.StringVar(value="尚未打开音频")
        self.info_var = tk.StringVar(value="建议使用单音、无混响的AIFF/WAV录音")
        self.start_var = tk.StringVar(value="0.000")
        self.end_var = tk.StringVar(value="")
        self.midi_var = tk.StringVar(value="")
        self.channel_var = tk.StringVar(value="混合为单声道")
        self.fft_var = tk.StringVar(value="4096")
        self.max_frequency_var = tk.StringVar(value="20000")
        self.dynamic_range_var = tk.StringVar(value="80")
        self.frequency_scale_var = tk.StringVar(value="32谐波自适应")
        self.normalize_playback_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="打开一个单音乐器录音开始分析。")
        self.cursor_var = tk.StringVar(value="时间 --  基频 --")
        self.base_cursor_text = "时间 --  基频 --"

        self._build_controls()
        self._build_plots_and_table()
        self._set_export_state("disabled")
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_controls(self) -> None:
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text="打开音频", command=self._open_audio).pack(side="left")
        self.analyse_button = ttk.Button(top, text="开始分析", command=self._start_analysis)
        self.analyse_button.pack(side="left", padx=(6, 18))
        self.play_button = ttk.Button(top, text="播放选段原声", command=self._start_playback)
        self.play_button.pack(side="left", padx=3)
        self.pause_button = ttk.Button(top, text="暂停/继续", command=self._pause_or_resume)
        self.pause_button.pack(side="left", padx=3)
        self.stop_button = ttk.Button(top, text="停止", command=self._stop_playback)
        self.stop_button.pack(side="left", padx=3)
        ttk.Checkbutton(
            top, text="试听自动增益", variable=self.normalize_playback_var
        ).pack(side="left", padx=(10, 3))
        ttk.Button(
            top, text="热力图放大", command=self._open_large_spectrogram
        ).pack(side="left", padx=(10, 3))
        self.json_button = ttk.Button(top, text="导出分析JSON", command=self._export_json)
        self.json_button.pack(side="right", padx=(6, 0))
        self.csv_button = ttk.Button(top, text="导出谐波CSV", command=self._export_csv)
        self.csv_button.pack(side="right")

        settings = ttk.Frame(self, padding=(8, 0, 8, 5))
        settings.pack(fill="x")
        ttk.Label(settings, text="声道").pack(side="left")
        self.channel_box = ttk.Combobox(
            settings, textvariable=self.channel_var, width=13, state="readonly",
            values=("混合为单声道", "左声道", "右声道"),
        )
        self.channel_box.pack(side="left", padx=(3, 10))
        ttk.Label(settings, text="起点/s").pack(side="left")
        ttk.Entry(settings, textvariable=self.start_var, width=7).pack(side="left", padx=(3, 8))
        ttk.Label(settings, text="终点/s").pack(side="left")
        ttk.Entry(settings, textvariable=self.end_var, width=7).pack(side="left", padx=(3, 8))
        ttk.Label(settings, text="已知音高").pack(side="left")
        ttk.Entry(settings, textvariable=self.midi_var, width=7).pack(side="left", padx=(3, 2))
        ttk.Label(settings, text="留空自动/C4/60").pack(side="left", padx=(0, 10))
        ttk.Label(settings, text="FFT").pack(side="left")
        ttk.Combobox(
            settings, textvariable=self.fft_var, width=6, state="readonly",
            values=("2048", "4096", "8192", "16384"),
        ).pack(side="left", padx=(3, 10))
        ttk.Label(settings, text="显示至/Hz").pack(side="left")
        ttk.Entry(settings, textvariable=self.max_frequency_var, width=7).pack(side="left", padx=(3, 8))
        ttk.Combobox(
            settings, textvariable=self.frequency_scale_var, width=12, state="readonly",
            values=("32谐波自适应", "手动频率上限"),
        ).pack(side="left", padx=(0, 8))
        ttk.Label(settings, text="动态/dB").pack(side="left")
        ttk.Entry(settings, textvariable=self.dynamic_range_var, width=5).pack(side="left", padx=(3, 0))

        ttk.Label(self, textvariable=self.path_var, padding=(8, 0, 8, 2)).pack(fill="x")
        ttk.Label(self, textvariable=self.info_var, padding=(8, 0, 8, 4)).pack(fill="x")

    def _build_plots_and_table(self) -> None:
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8)
        plot_frame = ttk.Frame(main)
        table_frame = ttk.Frame(main, width=390)
        main.add(plot_frame, weight=4)
        main.add(table_frame, weight=1)

        self.figure = Figure(figsize=(10.5, 7.2), dpi=100, constrained_layout=True)
        self.figure.set_facecolor("#080b12")
        grid = self.figure.add_gridspec(3, 2, height_ratios=(3.6, 1.25, 1.25))
        self.spectrogram_axis = self.figure.add_subplot(grid[0, :])
        self.wave_axis = self.figure.add_subplot(grid[1, 0])
        self.spectrum_axis = self.figure.add_subplot(grid[1, 1])
        self.frequency_trace_axis = self.figure.add_subplot(grid[2, 0])
        self.envelope_axis = self.figure.add_subplot(grid[2, 1])
        for axis in (
            self.spectrogram_axis, self.wave_axis, self.spectrum_axis,
            self.frequency_trace_axis, self.envelope_axis,
        ):
            axis.set_facecolor("#090e18")
            axis.tick_params(colors="#b7c6da", labelsize=8)
            axis.xaxis.label.set_color("#d7e2f1")
            axis.yaxis.label.set_color("#d7e2f1")
            axis.title.set_color("#eef5ff")
            for spine in axis.spines.values():
                spine.set_color("#34465c")
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(fill="x")
        self.canvas.mpl_connect("button_press_event", self._plot_clicked)
        self.canvas.mpl_connect("motion_notify_event", self._plot_moved)

        ttk.Label(table_frame, text="当前时刻的前32次谐波", padding=(6, 3)).pack(fill="x")
        columns = ("h", "expected", "measured", "cents", "amplitude", "db", "tau")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=24)
        headings = {
            "h": "次",
            "expected": "理论Hz",
            "measured": "峰值Hz",
            "cents": "偏差cent",
            "amplitude": "相对幅度",
            "db": "dB",
            "tau": "衰减ms",
        }
        widths = {"h": 30, "expected": 58, "measured": 58, "cents": 56,
                  "amplitude": 62, "db": 48, "tau": 62}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], minwidth=30, anchor="e")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        scroll.pack(side="right", fill="y", padx=(0, 5), pady=(0, 6))
        self.table.bind("<<TreeviewSelect>>", self._table_selected)

        footer = ttk.Frame(self, padding=(8, 4))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.cursor_var).pack(side="left")
        ttk.Label(footer, textvariable=self.status_var).pack(side="right")

    def _set_export_state(self, state: str) -> None:
        self.json_button.configure(state=state)
        self.csv_button.configure(state=state)

    def _open_audio(self) -> None:
        self._stop_playback(silent=True)
        chosen = filedialog.askopenfilename(title="打开单音乐器录音", filetypes=SUPPORTED_FILES)
        if not chosen:
            return
        try:
            data, sample_rate = sf.read(chosen, dtype="float64", always_2d=True)
            if data.size == 0:
                raise ValueError("音频文件为空")
        except Exception as exc:
            messagebox.showerror("无法读取音频", str(exc))
            return
        self.source_path = Path(chosen)
        self.source_audio = data
        self.source_rate = float(sample_rate)
        self.source_channels = int(data.shape[1])
        duration = data.shape[0] / self.source_rate
        self.start_var.set("0.000")
        self.end_var.set("%.3f" % min(duration, 12.0))
        detected_note = note_from_filename(self.source_path)
        self.midi_var.set(midi_note_name(detected_note) if detected_note is not None else "")
        if detected_note is not None and detected_note < 48:
            self.fft_var.set("8192")
        else:
            self.fft_var.set("4096")
        if self.source_channels == 1:
            self.channel_var.set("混合为单声道")
        self.path_var.set(str(self.source_path))
        self.info_var.set(
            "%.1f kHz · %d声道 · %.3f s%s" % (
                self.source_rate / 1000.0,
                self.source_channels,
                duration,
                "；文件较长，默认先分析前12秒" if duration > 12.0 else "",
            )
        )
        self.result = None
        self._set_export_state("disabled")
        self.status_var.set("音频已载入；确认单音范围和音高后点击开始分析。")

    def _mono_audio(self) -> np.ndarray:
        if self.source_audio is None:
            raise ValueError("请先打开音频")
        if self.source_audio.shape[1] == 1:
            return self.source_audio[:, 0].copy()
        mode = self.channel_var.get()
        if mode == "左声道":
            return self.source_audio[:, 0].copy()
        if mode == "右声道":
            return self.source_audio[:, min(1, self.source_audio.shape[1] - 1)].copy()
        return np.mean(self.source_audio, axis=1)

    def _start_analysis(self) -> None:
        if self.analysis_running:
            return
        self._stop_playback(silent=True)
        try:
            if self.source_audio is None:
                raise ValueError("请先打开AIFF、WAV或FLAC文件")
            start_s = float(self.start_var.get())
            end_s = float(self.end_var.get())
            expected_midi = parse_midi_text(self.midi_var.get())
            n_fft = int(self.fft_var.get())
            max_frequency = float(self.max_frequency_var.get())
            dynamic_range = float(self.dynamic_range_var.get())
            if max_frequency <= 0:
                raise ValueError("显示频率必须大于0")
            if dynamic_range < 20.0 or dynamic_range > 140.0:
                raise ValueError("频谱动态范围建议填写20~140 dB")
            mono = self._mono_audio()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self.analysis_running = True
        self.analyse_button.configure(state="disabled")
        self._set_export_state("disabled")
        self.status_var.set("正在执行STFT和32次谐波跟踪……")

        def worker() -> None:
            try:
                result = analyse_audio(
                    mono,
                    self.source_rate,
                    selection_start_s=start_s,
                    selection_end_s=end_s,
                    expected_midi=expected_midi,
                    n_fft=n_fft,
                    hop_length=n_fft // 8,
                    max_harmonics=32,
                )
                self.after(0, lambda: self._analysis_finished(
                    result, max_frequency, dynamic_range
                ))
            except Exception as exc:
                detail = traceback.format_exc()
                self.after(0, lambda: self._analysis_failed(exc, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _analysis_failed(self, exc: Exception, detail: str) -> None:
        self.analysis_running = False
        self.analyse_button.configure(state="normal")
        self.status_var.set("分析失败。")
        messagebox.showerror("分析失败", "%s\n\n%s" % (exc, detail))

    def _analysis_finished(self, result: AnalysisResult, max_frequency: float,
                           dynamic_range: float) -> None:
        self.analysis_running = False
        self.analyse_button.configure(state="normal")
        self.result = result
        self.current_frame = 0
        self.selected_harmonic = 0
        self._render_analysis(max_frequency, dynamic_range)
        self._set_export_state("normal")
        note = midi_note_name(int(round(result.estimated_midi)))
        self.status_var.set(
            "完成：基频 %.2f Hz（约%s / MIDI %.2f）。" % (
                result.median_fundamental_hz, note, result.estimated_midi
            )
        )

    def _render_analysis(self, requested_max_frequency: float,
                         dynamic_range: float) -> None:
        result = self.result
        if result is None:
            return
        if self.large_spectrogram_window is not None:
            self._close_large_spectrogram()
        for axis in (
            self.wave_axis, self.spectrogram_axis, self.spectrum_axis,
            self.frequency_trace_axis, self.envelope_axis,
        ):
            axis.clear()
            axis.set_facecolor("#090e18")
            axis.tick_params(colors="#b7c6da", labelsize=8)
            axis.xaxis.label.set_color("#d7e2f1")
            axis.yaxis.label.set_color("#d7e2f1")
            axis.title.set_color("#eef5ff")
            for spine in axis.spines.values():
                spine.set_color("#34465c")

        audio_times = result.selection_start_s + np.arange(result.audio.size) / result.sample_rate_hz
        stride = max(1, result.audio.size // 12000)
        self.wave_axis.plot(
            audio_times[::stride], result.audio[::stride], linewidth=0.7,
            color="#46bde8",
        )
        self.wave_axis.set_title("原始波形（点击选择时间）")
        self.wave_axis.set_xlabel("时间 / s")
        self.wave_axis.set_ylabel("幅度")
        self.wave_cursor = self.wave_axis.axvline(
            result.times_s[0], color="#25df9b", linewidth=1.0
        )

        max_frequency = self._display_frequency_limit(requested_max_frequency)
        self.display_max_frequency = max_frequency
        frequency_mask = result.frequencies_hz <= max_frequency
        shown_frequencies = result.frequencies_hz[frequency_mask]
        shown_db = result.spectrum_dbfs[frequency_mask]
        frequency_stride = max(1, shown_db.shape[0] // 1600)
        time_stride = max(1, shown_db.shape[1] // 1400)
        shown_db = shown_db[::frequency_stride, ::time_stride]
        shown_frequencies = shown_frequencies[::frequency_stride]
        shown_times = result.times_s[::time_stride]
        upper = float(np.percentile(shown_db, 99.7))
        lower = upper - dynamic_range
        self.spectrogram_vmin = lower
        self.spectrogram_vmax = upper
        self.spectrogram_image = self.spectrogram_axis.imshow(
            shown_db,
            origin="lower",
            aspect="auto",
            interpolation="nearest",
            extent=(shown_times[0], shown_times[-1],
                    shown_frequencies[0], shown_frequencies[-1]),
            cmap=SPECTROGRAM_CMAP,
            vmin=lower,
            vmax=upper,
        )
        self.spectrogram_axis.set_title("频谱瀑布图 / dBFS（点击选择时间）")
        self.spectrogram_axis.set_xlabel("时间 / s")
        self.spectrogram_axis.set_ylabel("频率 / Hz")
        self.spec_cursor = self.spectrogram_axis.axvline(
            result.times_s[0], color="#43ffd2", linewidth=1.0
        )
        self.spec_hover_line = self.spectrogram_axis.axhline(
            0.0, color="#f4f7ff", linewidth=0.65, alpha=0.75, visible=False
        )
        self.spec_tooltip = self.spectrogram_axis.annotate(
            "", xy=(0.0, 0.0), xytext=(12, 12), textcoords="offset points",
            color="#f5f8ff", fontsize=8,
            bbox={"boxstyle": "round,pad=0.25", "fc": "#101724", "ec": "#526a86", "alpha": 0.94},
            visible=False,
        )

        harmonic_numbers = np.arange(1, result.harmonic_amplitude_relative.shape[0] + 1)
        self.harmonic_bars = self.spectrum_axis.bar(
            harmonic_numbers,
            np.zeros_like(harmonic_numbers, dtype=np.float64),
            width=0.78,
            color="#4dc7ef",
            edgecolor="#a3eaff",
            linewidth=0.35,
        )
        self.spectrum_axis.set_title("光标时刻的32次谐波幅度")
        self.spectrum_axis.set_xlabel("谐波次数")
        self.spectrum_axis.set_ylabel("相对全段峰值")
        self.spectrum_axis.set_xlim(0.25, len(harmonic_numbers) + 0.75)
        self.spectrum_axis.set_ylim(0.0, 1.05)
        self.spectrum_axis.set_xticks(np.arange(1, len(harmonic_numbers) + 1, 4))
        self.spectrum_axis.grid(True, linewidth=0.3, alpha=0.35)

        self.frequency_trace_line, = self.frequency_trace_axis.plot(
            [], [], linewidth=1.0, color="#f2d33f"
        )
        self.frequency_trace_cursor = self.frequency_trace_axis.axvline(
            result.times_s[0], color="#25df9b", linewidth=1.0
        )
        self.frequency_trace_axis.set_title("将鼠标移到瀑布图上，查看该频点随时间的幅度")
        self.frequency_trace_axis.set_xlabel("时间 / s")
        self.frequency_trace_axis.set_ylabel("幅度 / dBFS")
        self.frequency_trace_axis.set_xlim(result.times_s[0], result.times_s[-1])
        self.frequency_trace_axis.set_ylim(lower, max(3.0, upper + 3.0))
        self.frequency_trace_axis.grid(True, linewidth=0.3, alpha=0.25, color="#7890aa")

        self.envelope_line, = self.envelope_axis.plot(
            [], [], linewidth=1.2, color="#a882ff"
        )
        self.envelope_cursor = self.envelope_axis.axvline(
            result.times_s[0], color="#00a878", linewidth=1.0
        )
        self.envelope_axis.set_xlabel("时间 / s")
        self.envelope_axis.set_ylabel("相对自身峰值 / dB")
        self.envelope_axis.set_ylim(-80.0, 3.0)
        self.envelope_axis.grid(True, linewidth=0.3, alpha=0.25, color="#7890aa")
        start_time = float(result.times_s[0])
        self.playback_lines = [
            self.spectrogram_axis.axvline(start_time, color="#ffffff", linewidth=1.35,
                                          alpha=0.9, visible=False),
            self.wave_axis.axvline(start_time, color="#ffffff", linewidth=1.35,
                                   alpha=0.9, visible=False),
            self.frequency_trace_axis.axvline(start_time, color="#ffffff", linewidth=1.35,
                                              alpha=0.9, visible=False),
            self.envelope_axis.axvline(start_time, color="#ffffff", linewidth=1.35,
                                       alpha=0.9, visible=False),
        ]
        self.hover_frequency_bin = None
        self._update_cursor(0, redraw=False)
        self.canvas.draw_idle()

    def _display_frequency_limit(self, requested_max_frequency: float) -> float:
        """Use the 32nd partial as the default top, with the entry as a ceiling."""
        result = self.result
        if result is None:
            return requested_max_frequency
        nyquist = result.sample_rate_hz * 0.5
        manual_ceiling = min(float(requested_max_frequency), nyquist)
        if self.frequency_scale_var.get() == "手动频率上限":
            return manual_ceiling

        harmonic_count = result.harmonic_frequency_hz.shape[0]
        theoretical_top = result.median_fundamental_hz * harmonic_count
        measured = result.harmonic_frequency_hz[:harmonic_count]
        finite = measured[np.isfinite(measured)]
        measured_top = (float(np.percentile(finite, 99.5))
                        if finite.size else theoretical_top)
        adaptive_top = max(theoretical_top * 1.045, measured_top * 1.025)
        adaptive_top = max(adaptive_top, result.median_fundamental_hz * 4.0)
        return max(
            result.sample_rate_hz / result.n_fft * 4.0,
            min(manual_ceiling, adaptive_top),
        )

    def _update_cursor(self, frame: int, redraw: bool = True) -> None:
        result = self.result
        if result is None:
            return
        frame = max(0, min(int(frame), len(result.times_s) - 1))
        self.current_frame = frame
        time_s = float(result.times_s[frame])
        self.wave_cursor.set_xdata([time_s, time_s])
        self.spec_cursor.set_xdata([time_s, time_s])
        self.envelope_cursor.set_xdata([time_s, time_s])
        self.frequency_trace_cursor.set_xdata([time_s, time_s])
        if self.large_selection_line is not None:
            self.large_selection_line.set_xdata([time_s, time_s])

        amplitudes = result.harmonic_amplitude_relative[:, frame]
        for index, (bar, amplitude) in enumerate(zip(self.harmonic_bars, amplitudes)):
            bar.set_height(float(amplitude))
            if index == self.selected_harmonic:
                bar.set_facecolor("#ff9d32")
                bar.set_edgecolor("#ffe1a6")
            else:
                bar.set_facecolor("#4dc7ef")
                bar.set_edgecolor("#a3eaff")
        self.spectrum_axis.set_title("t=%.4f s 的32次谐波幅度" % time_s)

        harmonic_db = result.harmonic_db[self.selected_harmonic]
        harmonic_db = harmonic_db - float(np.max(harmonic_db))
        self.envelope_line.set_data(result.times_s, harmonic_db)
        fit = result.harmonic_fits[self.selected_harmonic]
        tau = "无法拟合" if fit.tau_ms is None else "τ=%.1f ms, T60=%.1f ms" % (
            fit.tau_ms, fit.t60_ms
        )
        self.envelope_axis.set_title("第%d次谐波随时间变化（%s）" % (
            self.selected_harmonic + 1, tau
        ))
        self.envelope_axis.set_xlim(result.times_s[0], result.times_s[-1])
        self._update_table()
        self.base_cursor_text = (
            "时间 %.4f s  基频 %.2f Hz  约%s" % (
                time_s,
                result.fundamental_hz[frame],
                midi_note_name(int(round(frequency_to_midi(result.fundamental_hz[frame])))),
            )
        )
        self.cursor_var.set(self.base_cursor_text)
        if self.large_spectrogram_canvas is not None:
            self.large_spectrogram_canvas.draw_idle()
        if redraw:
            self.canvas.draw_idle()

    def _update_table(self) -> None:
        result = self.result
        if result is None:
            return
        selected = self.selected_harmonic
        for item in self.table.get_children():
            self.table.delete(item)
        f0 = float(result.fundamental_hz[self.current_frame])
        for index in range(result.harmonic_frequency_hz.shape[0]):
            expected = f0 * (index + 1)
            measured = float(result.harmonic_frequency_hz[index, self.current_frame])
            if math.isfinite(measured) and expected > 0:
                cents = 1200.0 * math.log(measured / expected, 2.0)
                measured_text = "%.2f" % measured
                cents_text = "%+.1f" % cents
                amplitude_text = "%.5f" % result.harmonic_amplitude_relative[index, self.current_frame]
                db_text = "%.1f" % result.harmonic_db[index, self.current_frame]
            else:
                measured_text = cents_text = amplitude_text = db_text = "--"
            fit = result.harmonic_fits[index]
            tau_text = "--" if fit.tau_ms is None else "%.1f" % fit.tau_ms
            item = self.table.insert("", "end", iid=str(index), values=(
                index + 1,
                "%.2f" % expected,
                measured_text,
                cents_text,
                amplitude_text,
                db_text,
                tau_text,
            ))
            if index == selected:
                self.table.selection_set(item)
                self.table.focus(item)

    def _plot_clicked(self, event: Any) -> None:
        result = self.result
        if result is None or event.xdata is None:
            return
        if event.inaxes == self.spectrum_axis:
            harmonic = max(0, min(
                result.harmonic_amplitude_relative.shape[0] - 1,
                int(round(float(event.xdata))) - 1,
            ))
            self.selected_harmonic = harmonic
            self._update_cursor(self.current_frame)
            return
        if event.inaxes not in (self.wave_axis, self.spectrogram_axis, self.envelope_axis):
            return
        frame = int(np.argmin(np.abs(result.times_s - float(event.xdata))))
        self._update_cursor(frame)

    def _plot_moved(self, event: Any) -> None:
        result = self.result
        if result is None or event.xdata is None or event.ydata is None:
            self.cursor_var.set(self.base_cursor_text)
            return
        if event.inaxes == self.spectrogram_axis:
            frame = int(np.argmin(np.abs(result.times_s - float(event.xdata))))
            frequency_bin = int(np.argmin(np.abs(result.frequencies_hz - float(event.ydata))))
            frequency_hz = float(result.frequencies_hz[frequency_bin])
            point_db = float(result.spectrum_dbfs[frequency_bin, frame])
            if self.hover_frequency_bin != frequency_bin:
                self.hover_frequency_bin = frequency_bin
                trace = result.spectrum_dbfs[frequency_bin, :]
                self.frequency_trace_line.set_data(result.times_s, trace)
                self.frequency_trace_axis.set_title(
                    "%.2f Hz频点的幅度随时间变化" % frequency_hz
                )
            self.spec_hover_line.set_ydata([frequency_hz, frequency_hz])
            self.spec_hover_line.set_visible(True)
            self.spec_tooltip.xy = (float(event.xdata), float(event.ydata))
            self.spec_tooltip.set_text(
                "t %.4f s\nf %.2f Hz\n%.2f dBFS" % (
                    result.times_s[frame], frequency_hz, point_db
                )
            )
            self.spec_tooltip.set_visible(True)
            self.cursor_var.set(
                "%s  |  指针 t=%.4f s, f=%.2f Hz, %.2f dBFS" % (
                    self.base_cursor_text,
                    result.times_s[frame],
                    frequency_hz,
                    point_db,
                )
            )
            self.canvas.draw_idle()
        elif event.inaxes == self.spectrum_axis:
            self.spec_tooltip.set_visible(False)
            harmonic = max(0, min(
                result.harmonic_amplitude_relative.shape[0] - 1,
                int(round(float(event.xdata))) - 1,
            ))
            amplitude = float(
                result.harmonic_amplitude_relative[harmonic, self.current_frame]
            )
            peak_hz = float(result.harmonic_frequency_hz[harmonic, self.current_frame])
            self.cursor_var.set(
                "%s  |  第%d次谐波：峰值 %.2f Hz，相对幅度 %.5f" % (
                    self.base_cursor_text, harmonic + 1, peak_hz, amplitude
                )
            )
        else:
            if hasattr(self, "spec_tooltip"):
                self.spec_tooltip.set_visible(False)
            self.cursor_var.set(self.base_cursor_text)

    def _open_large_spectrogram(self) -> None:
        result = self.result
        if result is None:
            messagebox.showinfo("尚无频谱", "请先打开音频并完成分析。")
            return
        if (self.large_spectrogram_window is not None and
                self.large_spectrogram_window.winfo_exists()):
            self.large_spectrogram_window.lift()
            self.large_spectrogram_window.focus_force()
            return

        window = tk.Toplevel(self)
        self.large_spectrogram_window = window
        window.title("放大频谱热力图 - Esc关闭")
        window.configure(bg="#03050a")
        try:
            window.state("zoomed")
        except tk.TclError:
            window.geometry("1500x850")
        window.protocol("WM_DELETE_WINDOW", self._close_large_spectrogram)
        window.bind("<Escape>", lambda _event: self._close_large_spectrogram())

        header = ttk.Frame(window, padding=(8, 5))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="鼠标悬停：浮窗显示该频点的幅度变化；单击：选择时刻并联动32谐波柱状图",
        ).pack(side="left")
        ttk.Button(header, text="关闭 Esc", command=self._close_large_spectrogram).pack(side="right")

        figure = Figure(figsize=(15, 8), dpi=100, facecolor="#03050a")
        axis = figure.add_axes([0.055, 0.075, 0.925, 0.875])
        trace_axis = figure.add_axes([0.615, 0.675, 0.35, 0.245])
        for item in (axis, trace_axis):
            item.set_facecolor("#080b14")
            item.tick_params(colors="#d6e2f0", labelsize=9)
            item.xaxis.label.set_color("#eff6ff")
            item.yaxis.label.set_color("#eff6ff")
            item.title.set_color("#ffffff")
            for spine in item.spines.values():
                spine.set_color("#526a86")

        frequency_mask = result.frequencies_hz <= self.display_max_frequency
        shown_frequencies = result.frequencies_hz[frequency_mask]
        shown_db = result.spectrum_dbfs[frequency_mask]
        frequency_stride = max(1, shown_db.shape[0] // 1900)
        time_stride = max(1, shown_db.shape[1] // 1900)
        shown_frequencies = shown_frequencies[::frequency_stride]
        shown_db = shown_db[::frequency_stride, ::time_stride]
        shown_times = result.times_s[::time_stride]
        axis.imshow(
            shown_db,
            origin="lower",
            aspect="auto",
            interpolation="nearest",
            extent=(shown_times[0], shown_times[-1],
                    shown_frequencies[0], shown_frequencies[-1]),
            cmap=SPECTROGRAM_CMAP,
            vmin=self.spectrogram_vmin,
            vmax=self.spectrogram_vmax,
        )
        mode_text = ("前32次谐波自适应" if self.frequency_scale_var.get() == "32谐波自适应"
                     else "手动频率上限")
        axis.set_title("频谱热力图（%s，0–%.1f Hz）" % (
            mode_text, self.display_max_frequency
        ))
        axis.set_xlabel("时间 / s")
        axis.set_ylabel("频率 / Hz")
        self.large_selection_line = axis.axvline(
            result.times_s[self.current_frame], color="#39ffd0", linewidth=1.15
        )
        self.large_playback_line = axis.axvline(
            result.times_s[0], color="#ffffff", linewidth=1.5,
            alpha=0.95, visible=self.playback_state != "stopped",
        )
        self.large_hover_line = axis.axhline(
            0.0, color="#ffffff", linewidth=0.7, alpha=0.8, visible=False
        )
        self.large_tooltip = axis.annotate(
            "", xy=(0.0, 0.0), xytext=(12, 12), textcoords="offset points",
            color="#ffffff", fontsize=9,
            bbox={"boxstyle": "round,pad=0.3", "fc": "#101724",
                  "ec": "#8aa5c3", "alpha": 0.96},
            visible=False,
        )
        self.large_trace_line, = trace_axis.plot(
            [], [], color="#ffe14d", linewidth=1.25
        )
        self.large_trace_cursor = trace_axis.axvline(
            result.times_s[self.current_frame], color="#39ffd0", linewidth=0.9
        )
        trace_axis.set_title("悬停频点的幅度随时间变化", fontsize=10)
        trace_axis.set_xlabel("时间 / s", fontsize=8)
        trace_axis.set_ylabel("dBFS", fontsize=8)
        trace_axis.set_xlim(result.times_s[0], result.times_s[-1])
        trace_axis.set_ylim(self.spectrogram_vmin, max(3.0, self.spectrogram_vmax + 3.0))
        trace_axis.grid(True, linewidth=0.3, alpha=0.3)
        trace_axis.patch.set_alpha(0.91)

        canvas = FigureCanvasTkAgg(figure, master=window)
        self.large_spectrogram_canvas = canvas
        self.large_spectrogram_axis = axis
        self.large_trace_axis = trace_axis
        canvas.mpl_connect("motion_notify_event", self._large_plot_moved)
        canvas.mpl_connect("button_press_event", self._large_plot_clicked)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _large_plot_moved(self, event: Any) -> None:
        result = self.result
        if (result is None or self.large_spectrogram_canvas is None or
                event.inaxes != getattr(self, "large_spectrogram_axis", None) or
                event.xdata is None or event.ydata is None):
            return
        frame = int(np.argmin(np.abs(result.times_s - float(event.xdata))))
        frequency_bin = int(np.argmin(
            np.abs(result.frequencies_hz - float(event.ydata))
        ))
        frequency_hz = float(result.frequencies_hz[frequency_bin])
        point_db = float(result.spectrum_dbfs[frequency_bin, frame])
        self.large_trace_line.set_data(
            result.times_s, result.spectrum_dbfs[frequency_bin, :]
        )
        self.large_trace_axis.set_title(
            "%.2f Hz 的幅度随时间变化" % frequency_hz, fontsize=10
        )
        self.large_trace_cursor.set_xdata([
            result.times_s[self.current_frame], result.times_s[self.current_frame]
        ])
        self.large_hover_line.set_ydata([frequency_hz, frequency_hz])
        self.large_hover_line.set_visible(True)
        self.large_tooltip.xy = (float(event.xdata), float(event.ydata))
        self.large_tooltip.set_text(
            "t %.4f s\nf %.2f Hz\n%.2f dBFS" % (
                result.times_s[frame], frequency_hz, point_db
            )
        )
        self.large_tooltip.set_visible(True)
        self.large_spectrogram_canvas.draw_idle()

    def _large_plot_clicked(self, event: Any) -> None:
        result = self.result
        if (result is None or event.xdata is None or
                event.inaxes != getattr(self, "large_spectrogram_axis", None)):
            return
        frame = int(np.argmin(np.abs(result.times_s - float(event.xdata))))
        self._update_cursor(frame)

    def _close_large_spectrogram(self) -> None:
        window = self.large_spectrogram_window
        self.large_spectrogram_window = None
        self.large_spectrogram_canvas = None
        self.large_playback_line = None
        self.large_selection_line = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _prepare_playback(self) -> None:
        if self.source_audio is None:
            raise ValueError("请先打开音频文件")
        start_s = max(0.0, float(self.start_var.get()))
        end_s = min(
            self.source_audio.shape[0] / self.source_rate,
            float(self.end_var.get()),
        )
        if end_s <= start_s:
            raise ValueError("播放终点必须晚于起点")
        mono = self._mono_audio()
        first = int(round(start_s * self.source_rate))
        last = int(round(end_s * self.source_rate))
        audio = mono[first:last]
        if audio.size == 0:
            raise ValueError("播放范围为空")

        import sounddevice as sd

        device_info = sd.query_devices(kind="output")
        output_rate = int(round(float(device_info.get("default_samplerate", 48000.0))))
        if output_rate < 8000:
            output_rate = 48000
        source_rate_int = int(round(self.source_rate))
        if source_rate_int != output_rate:
            divisor = math.gcd(source_rate_int, output_rate)
            audio = signal.resample_poly(
                audio, output_rate // divisor, source_rate_int // divisor
            )
        self.playback_gain_db = 0.0
        if self.normalize_playback_var.get():
            peak = float(np.max(np.abs(audio)))
            if peak > 1.0e-9:
                target = 10.0 ** (-1.0 / 20.0)
                # Limit boost to +42 dB so a nearly silent selection does not
                # turn background noise into a dangerous full-scale blast.
                gain = min(target / peak, 10.0 ** (42.0 / 20.0))
                audio = audio * gain
                self.playback_gain_db = 20.0 * math.log10(max(gain, 1.0e-12))
        self.playback_audio = np.asarray(audio, dtype=np.float32)
        self.playback_sample_rate = float(output_rate)
        self.playback_selection_start_s = start_s
        self.playback_duration_s = end_s - start_s
        self.playback_elapsed_before_start = 0.0

    def _start_playback(self) -> None:
        try:
            self._stop_playback(silent=True)
            self._prepare_playback()
            import sounddevice as sd
            sd.play(self.playback_audio, int(self.playback_sample_rate), blocking=False)
        except Exception as exc:
            messagebox.showerror(
                "无法播放原声",
                "%s\n\n若提示缺少sounddevice，请重新运行install_python_requirements.bat。" % exc,
            )
            return
        self.playback_state = "playing"
        self.playback_clock_start = time.perf_counter()
        self.playback_last_analysis_ui_s = -1.0
        self._set_playback_lines_visible(True)
        gain_text = ("（试听自动增益 %+.1f dB，仅影响播放）" % self.playback_gain_db
                     if self.normalize_playback_var.get() else "")
        self.status_var.set("正在播放导入音频的当前选段%s……" % gain_text)
        self._schedule_playback_tick()

    def _pause_or_resume(self) -> None:
        if self.playback_state == "playing":
            self.playback_elapsed_before_start += time.perf_counter() - self.playback_clock_start
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            self.playback_state = "paused"
            self.status_var.set("播放已暂停。")
            return
        if self.playback_state != "paused" or self.playback_audio is None:
            return
        sample_offset = int(round(
            self.playback_elapsed_before_start * self.playback_sample_rate
        ))
        if sample_offset >= len(self.playback_audio):
            self._stop_playback()
            return
        try:
            import sounddevice as sd
            sd.play(
                self.playback_audio[sample_offset:],
                int(self.playback_sample_rate),
                blocking=False,
            )
        except Exception as exc:
            messagebox.showerror("无法继续播放", str(exc))
            self._stop_playback(silent=True)
            return
        self.playback_state = "playing"
        self.playback_clock_start = time.perf_counter()
        self.status_var.set("继续播放原声……")
        self._schedule_playback_tick()

    def _schedule_playback_tick(self) -> None:
        if self.playback_timer is not None:
            try:
                self.after_cancel(self.playback_timer)
            except Exception:
                pass
        self.playback_timer = self.after(33, self._playback_tick)

    def _playback_tick(self) -> None:
        self.playback_timer = None
        if self.playback_state != "playing":
            return
        elapsed = self.playback_elapsed_before_start + (
            time.perf_counter() - self.playback_clock_start
        )
        if elapsed >= self.playback_duration_s:
            self._set_playback_line_time(
                self.playback_selection_start_s + self.playback_duration_s
            )
            self._stop_playback(silent=True, keep_line=True)
            self.status_var.set("原声播放完成。")
            return
        self._set_playback_line_time(self.playback_selection_start_s + elapsed)
        self._schedule_playback_tick()

    def _set_playback_line_time(self, time_s: float) -> None:
        if (self.result is not None and
                (self.playback_last_analysis_ui_s < 0.0 or
                 abs(time_s - self.playback_last_analysis_ui_s) >= 0.075)):
            frame = int(np.argmin(np.abs(self.result.times_s - time_s)))
            self.playback_last_analysis_ui_s = time_s
            self._update_cursor(frame, redraw=False)
        if hasattr(self, "playback_lines"):
            for line in self.playback_lines:
                line.set_xdata([time_s, time_s])
            self.canvas.draw_idle()
        if self.large_playback_line is not None:
            self.large_playback_line.set_xdata([time_s, time_s])
            if self.large_spectrogram_canvas is not None:
                self.large_spectrogram_canvas.draw_idle()

    def _set_playback_lines_visible(self, visible: bool) -> None:
        if hasattr(self, "playback_lines"):
            for line in self.playback_lines:
                line.set_visible(visible)
            self.canvas.draw_idle()
        if self.large_playback_line is not None:
            self.large_playback_line.set_visible(visible)
            if self.large_spectrogram_canvas is not None:
                self.large_spectrogram_canvas.draw_idle()

    def _stop_playback(self, silent: bool = False, keep_line: bool = False) -> None:
        if self.playback_timer is not None:
            try:
                self.after_cancel(self.playback_timer)
            except Exception:
                pass
            self.playback_timer = None
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        was_active = self.playback_state != "stopped"
        self.playback_state = "stopped"
        self.playback_elapsed_before_start = 0.0
        if not keep_line:
            self._set_playback_lines_visible(False)
        if was_active and not silent:
            self.status_var.set("播放已停止。")

    def _close(self) -> None:
        self._stop_playback(silent=True)
        self._close_large_spectrogram()
        self.destroy()

    def _table_selected(self, _event: Any) -> None:
        selection = self.table.selection()
        if not selection or self.result is None:
            return
        try:
            harmonic = int(selection[0])
        except ValueError:
            return
        if harmonic == self.selected_harmonic:
            return
        self.selected_harmonic = harmonic
        self._update_cursor(self.current_frame)

    def _export_json(self) -> None:
        if self.result is None or self.source_path is None:
            return
        default = self.source_path.with_suffix(".analysis.json").name
        chosen = filedialog.asksaveasfilename(
            title="导出完整分析JSON", defaultextension=".json",
            initialfile=default, filetypes=(("JSON", "*.json"),),
        )
        if not chosen:
            return
        try:
            payload = analysis_to_dict(
                self.result, self.source_path, self.source_channels, self.channel_var.get()
            )
            Path(chosen).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            self.status_var.set("已导出 %s" % chosen)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _export_csv(self) -> None:
        if self.result is None or self.source_path is None:
            return
        default = self.source_path.with_suffix(".harmonics.csv").name
        chosen = filedialog.asksaveasfilename(
            title="导出逐帧谐波CSV", defaultextension=".csv",
            initialfile=default, filetypes=(("CSV", "*.csv"),),
        )
        if not chosen:
            return
        result = self.result
        try:
            with Path(chosen).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow((
                    "time_s", "harmonic", "fundamental_hz", "expected_hz",
                    "measured_hz", "deviation_cents", "amplitude_relative",
                    "amplitude_db", "phase_rad", "decay_tau_ms", "decay_t60_ms",
                    "fit_r_squared",
                ))
                for frame, time_s in enumerate(result.times_s):
                    f0 = float(result.fundamental_hz[frame])
                    for index, fit in enumerate(result.harmonic_fits):
                        measured = float(result.harmonic_frequency_hz[index, frame])
                        expected = f0 * (index + 1)
                        cents = (1200.0 * math.log(measured / expected, 2.0)
                                 if math.isfinite(measured) and expected > 0 else "")
                        writer.writerow((
                            "%.7f" % time_s,
                            index + 1,
                            "%.5f" % f0,
                            "%.5f" % expected,
                            "%.5f" % measured if math.isfinite(measured) else "",
                            "%.5f" % cents if cents != "" else "",
                            "%.9f" % result.harmonic_amplitude_relative[index, frame],
                            "%.5f" % result.harmonic_db[index, frame],
                            "%.7f" % result.harmonic_phase_rad[index, frame]
                            if math.isfinite(result.harmonic_phase_rad[index, frame]) else "",
                            "%.5f" % fit.tau_ms if fit.tau_ms is not None else "",
                            "%.5f" % fit.t60_ms if fit.t60_ms is not None else "",
                            "%.7f" % fit.r_squared if fit.r_squared is not None else "",
                        ))
            self.status_var.set("已导出 %s" % chosen)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))


def main() -> None:
    app = InstrumentSpectrumAnalyzer()
    app.mainloop()


if __name__ == "__main__":
    main()
