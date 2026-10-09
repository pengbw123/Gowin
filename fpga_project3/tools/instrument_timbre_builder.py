"""Phase-2 four-anchor timbre builder for C2/C3/C4/C6 recordings."""

from __future__ import annotations

import json
import math
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from typing import Any, Dict, Optional

import numpy as np
import soundfile as sf
from matplotlib import rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from instrument_timbre_builder_core import AnchorTimbre, anchor_to_json, extract_anchor_timbre


ANCHORS = (("C2", 36), ("C3", 48), ("C4", 60), ("C6", 84))
SUPPORTED_FILES = (("音频", "*.wav *.aif *.aiff *.flac"), ("所有文件", "*.*"))


class TimbreBuilder(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        rcParams["axes.unicode_minus"] = False
        self.title("乐器音色构建器 - 阶段二（C2/C3/C4/C6）")
        self.geometry("1480x900")
        self.minsize(1120, 700)
        self.anchor_paths: Dict[str, tk.StringVar] = {}
        self.anchor_starts: Dict[str, tk.StringVar] = {}
        self.anchor_ends: Dict[str, tk.StringVar] = {}
        self.anchors: Dict[str, AnchorTimbre] = {}
        self.source_audio: Dict[str, np.ndarray] = {}
        self.source_rates: Dict[str, float] = {}
        self.running = False
        self.selected_anchor_var = tk.StringVar(value="C2")
        self.fft_var = tk.StringVar(value="8192")
        self.status_var = tk.StringVar(
            value="载入四个单音录音。阶段二只拟合相对音色衰减，不重复总幅度包络。"
        )
        self._build_controls()
        self._build_results()
        self._fill_default_paths()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_controls(self) -> None:
        header = ttk.Frame(self, padding=(8, 7))
        header.pack(fill="x")
        self.analyse_button = ttk.Button(
            header, text="分析四基准", command=self._start_analysis
        )
        self.analyse_button.pack(side="left")
        ttk.Label(header, text="FFT").pack(side="left", padx=(14, 3))
        ttk.Combobox(
            header, textvariable=self.fft_var, width=7, state="readonly",
            values=("4096", "8192", "16384"),
        ).pack(side="left")
        ttk.Label(header, text="预览基准").pack(side="left", padx=(16, 3))
        anchor_box = ttk.Combobox(
            header, textvariable=self.selected_anchor_var, width=5,
            state="readonly", values=tuple(name for name, _ in ANCHORS),
        )
        anchor_box.pack(side="left")
        anchor_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_table())
        ttk.Button(header, text="试听原声", command=self._play_original).pack(side="left", padx=(14, 3))
        ttk.Button(header, text="试听16谐波", command=lambda: self._play_rebuild(16)).pack(side="left", padx=3)
        ttk.Button(header, text="试听32谐波", command=lambda: self._play_rebuild(32)).pack(side="left", padx=3)
        ttk.Button(header, text="停止", command=self._stop_audio).pack(side="left", padx=3)
        self.export_button = ttk.Button(
            header, text="导出阶段二JSON", command=self._export_json, state="disabled"
        )
        self.export_button.pack(side="right")

        files = ttk.LabelFrame(self, text="四基准录音与单音范围", padding=(7, 5))
        files.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(files, text="基准", width=6).grid(row=0, column=0)
        ttk.Label(files, text="文件").grid(row=0, column=1, sticky="w")
        ttk.Label(files, text="起点/s").grid(row=0, column=3)
        ttk.Label(files, text="终点/s").grid(row=0, column=4)
        files.columnconfigure(1, weight=1)
        for row, (name, _midi) in enumerate(ANCHORS, 1):
            path_var = tk.StringVar()
            start_var = tk.StringVar(value="0.000")
            end_var = tk.StringVar(value="6.000")
            self.anchor_paths[name] = path_var
            self.anchor_starts[name] = start_var
            self.anchor_ends[name] = end_var
            ttk.Label(files, text=name, width=6).grid(row=row, column=0, padx=2, pady=2)
            ttk.Entry(files, textvariable=path_var).grid(
                row=row, column=1, sticky="ew", padx=3, pady=2
            )
            ttk.Button(
                files, text="浏览", command=lambda n=name: self._browse(n)
            ).grid(row=row, column=2, padx=3, pady=2)
            ttk.Entry(files, textvariable=start_var, width=8).grid(row=row, column=3, padx=3)
            ttk.Entry(files, textvariable=end_var, width=8).grid(row=row, column=4, padx=3)

    def _build_results(self) -> None:
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)
        plot_frame = ttk.Frame(panes)
        table_frame = ttk.Frame(panes, width=500)
        panes.add(plot_frame, weight=3)
        panes.add(table_frame, weight=2)

        self.figure = Figure(figsize=(9, 6.5), dpi=100, constrained_layout=True)
        self.axes = self.figure.subplots(2, 2).flatten()
        for axis, (name, _midi) in zip(self.axes, ANCHORS):
            axis.set_title("%s：等待分析" % name)
            axis.set_xlabel("谐波次数")
            axis.set_ylabel("起始相对幅度")
            axis.set_xlim(0.25, 32.75)
            axis.set_ylim(0.0, 0.3)
            axis.grid(True, alpha=0.25)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(fill="x")

        ttk.Label(
            table_frame,
            text="相对衰减：谐波幅度 ÷ 32谐波合成能量包络",
            padding=(6, 5),
        ).pack(fill="x")
        columns = ("h", "amp", "absolute", "relative", "alpha", "fit", "note")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings")
        headings = {
            "h": "次", "amp": "初始幅度", "absolute": "绝对τ/ms",
            "relative": "相对τ/ms", "alpha": "Q24 alpha",
            "fit": "R²", "note": "限制",
        }
        widths = {"h": 32, "amp": 72, "absolute": 70, "relative": 70,
                  "alpha": 78, "fit": 55, "note": 76}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor="e")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        scroll.pack(side="right", fill="y", padx=(0, 5), pady=(0, 6))
        ttk.Label(self, textvariable=self.status_var, padding=(8, 5)).pack(fill="x")

    def _fill_default_paths(self) -> None:
        audio_dir = Path(__file__).with_name("audiofiles")
        for name, _midi in ANCHORS:
            candidates = sorted(audio_dir.glob("*%s.*" % name))
            if candidates:
                self.anchor_paths[name].set(str(candidates[0]))

    def _browse(self, name: str) -> None:
        chosen = filedialog.askopenfilename(title="选择%s单音录音" % name,
                                            filetypes=SUPPORTED_FILES)
        if chosen:
            self.anchor_paths[name].set(chosen)
            try:
                info = sf.info(chosen)
                self.anchor_ends[name].set("%.3f" % min(info.duration, 8.0))
            except Exception:
                pass

    def _start_analysis(self) -> None:
        if self.running:
            return
        try:
            jobs = []
            for name, midi in ANCHORS:
                path = Path(self.anchor_paths[name].get())
                if not path.is_file():
                    raise ValueError("%s 文件不存在：%s" % (name, path))
                start = float(self.anchor_starts[name].get())
                end = float(self.anchor_ends[name].get())
                if start < 0.0 or end <= start:
                    raise ValueError("%s 的起止时间无效" % name)
                jobs.append((name, midi, path, start, end))
            n_fft = int(self.fft_var.get())
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        self.running = True
        self.analyse_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.status_var.set("正在分析C2/C3/C4/C6，并分离总包络与相对谐波衰减……")

        def worker() -> None:
            try:
                anchors: Dict[str, AnchorTimbre] = {}
                source_audio: Dict[str, np.ndarray] = {}
                source_rates: Dict[str, float] = {}
                for name, midi, path, start, end in jobs:
                    audio, sample_rate = sf.read(str(path), dtype="float64", always_2d=True)
                    mono = np.mean(audio, axis=1)
                    anchors[name] = extract_anchor_timbre(
                        mono, float(sample_rate), name, midi,
                        selection_start_s=start, selection_end_s=end,
                        n_fft=n_fft, max_harmonics=32,
                    )
                    source_audio[name] = mono
                    source_rates[name] = float(sample_rate)
                self.after(0, lambda: self._analysis_finished(
                    anchors, source_audio, source_rates
                ))
            except Exception as exc:
                detail = traceback.format_exc()
                self.after(0, lambda error=exc, text=detail: self._analysis_failed(error, text))

        threading.Thread(target=worker, daemon=True).start()

    def _analysis_finished(self, anchors: Dict[str, AnchorTimbre],
                           source_audio: Dict[str, np.ndarray],
                           source_rates: Dict[str, float]) -> None:
        self.running = False
        self.anchors = anchors
        self.source_audio = source_audio
        self.source_rates = source_rates
        self.analyse_button.configure(state="normal")
        self.export_button.configure(state="normal")
        for axis, (name, _midi) in zip(self.axes, ANCHORS):
            axis.clear()
            anchor = anchors[name]
            numbers = np.arange(1, 33)
            colors = ["#36acd4" if index < 16 else "#8d79d6" for index in range(32)]
            axis.bar(numbers, anchor.amplitudes, color=colors, width=0.78)
            axis.set_title("%s / f0 %.2f Hz" % (
                name, anchor.result.median_fundamental_hz
            ))
            axis.set_xlabel("谐波次数")
            axis.set_ylabel("起始相对幅度")
            axis.set_xlim(0.25, 32.75)
            axis.set_ylim(0.0, max(0.08, float(np.max(anchor.amplitudes)) * 1.12))
            axis.grid(True, alpha=0.25)
        self.canvas.draw_idle()
        self._refresh_table()
        clamped = sum(
            fit.clamped_growth for anchor in anchors.values()
            for fit in anchor.relative_fits[:16]
        )
        self.status_var.set(
            "分析完成：已生成四基准的相对衰减；前16谐波中%d项相对占比上升，当前RTL按保持不衰减导出。" % clamped
        )

    def _analysis_failed(self, exc: Exception, detail: str) -> None:
        self.running = False
        self.analyse_button.configure(state="normal")
        self.status_var.set("阶段二分析失败。")
        messagebox.showerror("分析失败", "%s\n\n%s" % (exc, detail))

    def _refresh_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        anchor = self.anchors.get(self.selected_anchor_var.get())
        if anchor is None:
            return
        for index, fit in enumerate(anchor.relative_fits):
            absolute = anchor.result.harmonic_fits[index]
            self.table.insert("", "end", values=(
                index + 1,
                "%.6f" % anchor.amplitudes[index],
                "--" if absolute.tau_ms is None else "%.1f" % absolute.tau_ms,
                "--" if fit.tau_ms is None else "%.1f" % fit.tau_ms,
                fit.alpha_q24,
                "--" if fit.r_squared is None else "%.3f" % fit.r_squared,
                "相对上升→保持" if fit.clamped_growth else "",
            ))

    def _selected(self) -> Optional[AnchorTimbre]:
        anchor = self.anchors.get(self.selected_anchor_var.get())
        if anchor is None:
            messagebox.showinfo("尚未分析", "请先完成四基准分析。")
        return anchor

    @staticmethod
    def _normalise_audio(audio: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        return np.asarray(audio * (0.89 / peak), dtype=np.float32) if peak > 1.0e-9 else np.asarray(audio, dtype=np.float32)

    def _play_original(self) -> None:
        anchor = self._selected()
        if anchor is None:
            return
        name = anchor.name
        sample_rate = self.source_rates[name]
        start = int(round(anchor.result.selection_start_s * sample_rate))
        end = int(round(anchor.result.selection_end_s * sample_rate))
        self._play(self._normalise_audio(self.source_audio[name][start:end]), sample_rate,
                   "%s原声" % name)

    def _play_rebuild(self, harmonic_count: int) -> None:
        anchor = self._selected()
        if anchor is None:
            return
        result = anchor.result
        sample_rate = result.sample_rate_hz
        duration = result.selection_end_s - result.selection_start_s
        count = int(round(duration * sample_rate))
        time_axis = np.arange(count, dtype=np.float64) / sample_rate
        relative_time = result.times_s - result.selection_start_s
        global_normalised = anchor.global_envelope / max(float(np.max(anchor.global_envelope)), 1.0e-12)
        common = np.interp(time_axis, relative_time, global_normalised,
                           left=0.0, right=float(global_normalised[-1]))
        rebuilt = np.zeros(count, dtype=np.float64)
        fundamental = result.median_fundamental_hz
        for index in range(min(harmonic_count, len(anchor.amplitudes))):
            frequency = fundamental * (index + 1)
            if frequency >= sample_rate * 0.48:
                continue
            fit = anchor.relative_fits[index]
            relative_decay = (np.ones(count) if fit.tau_ms is None else
                              np.exp(-time_axis / (fit.tau_ms / 1000.0)))
            rebuilt += anchor.amplitudes[index] * relative_decay * np.sin(
                2.0 * math.pi * frequency * time_axis
            )
        rebuilt *= common
        self._play(self._normalise_audio(rebuilt), sample_rate,
                   "%s的%d谐波重建" % (anchor.name, harmonic_count))

    def _play(self, audio: np.ndarray, sample_rate: float, label: str) -> None:
        try:
            import sounddevice as sd
            sd.stop()
            sd.play(audio, int(round(sample_rate)), blocking=False)
            self.status_var.set("正在试听%s（自动归一化只影响试听）。" % label)
        except Exception as exc:
            messagebox.showerror("无法播放", str(exc))

    def _stop_audio(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self.status_var.set("试听已停止。")

    def _export_json(self) -> None:
        if len(self.anchors) != len(ANCHORS):
            return
        chosen = filedialog.asksaveasfilename(
            title="导出阶段二音色", defaultextension=".json",
            initialfile="instrument_4anchor_relative_decay.json",
            filetypes=(("JSON", "*.json"),),
        )
        if not chosen:
            return
        payload: Dict[str, Any] = {
            "format": "gowin-relative-timbre-v2",
            "anchor_notes": [name for name, _midi in ANCHORS],
            "fpga_harmonic_count": 16,
            "analysis_harmonic_count": 32,
            "decay_model": {
                "measured": "H_k(t)",
                "common_envelope": "G(t)=sqrt(sum(H_k(t)^2))",
                "fpga_partial": "R_k(t)=H_k(t)/G(t)",
                "rtl": "output=sum(A_k*sin(k*w*t)*R_k(t))*ADSR(t)",
                "growth_policy": "relative growth is exported as alpha=0",
            },
            "adsr": {
                "attack_ms": 30.0, "decay_ms": 500.0,
                "sustain": 0.18, "release_ms": 800.0,
                "note": "ADSR controls total loudness and is intentionally independent of relative timbre decay",
            },
            "anchors": [anchor_to_json(self.anchors[name], 16) for name, _ in ANCHORS],
            "analysis_32": [anchor_to_json(self.anchors[name], 32) for name, _ in ANCHORS],
        }
        try:
            Path(chosen).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.status_var.set("已导出阶段二参数：%s" % chosen)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _close(self) -> None:
        self._stop_audio()
        self.destroy()


if __name__ == "__main__":
    TimbreBuilder().mainloop()
