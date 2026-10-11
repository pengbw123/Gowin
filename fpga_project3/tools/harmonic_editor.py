"""Four-anchor editor with independent amplitude and decay per partial."""

from __future__ import annotations

import json
import math
import subprocess
import time
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from additive_uart_protocol import (
    ANCHOR_MIDI_NOTES, ANCHOR_NAMES, DEFAULT_PERFORMANCE, load_preset,
    envelope_packets, preset_packets, send_packets, strum_packet,
)
from envelope_tools import (
    CURVE_POINTS, SAMPLE_RATE,
    amplitude_envelope_samples, curve_lut, normalise_envelopes,
    preview_harmonic_frames, render_additive_preview, stage_timing,
)
from generate_additive_tables import export_additive_preset

HERE = Path(__file__).resolve().parent
DEFAULT_PRESET = HERE / "instrument_4anchor_relative_decay.json"
MIN_DECAY_MS, MAX_DECAY_MS = 50.0, 120000.0
PITCH_RANGE_OPTIONS = {
    "±0.5半音（微颤音）": 0.5,
    "±1半音（精细）": 1.0,
    "±2半音（常用）": 2.0,
    "±12半音（八度）": 12.0,
    "±24半音（音效）": 24.0,
}


class HarmonicBarCanvas(tk.Canvas):
    """Draggable sixteen-bin harmonic spectrum."""

    def __init__(self, master: tk.Misc, anchor: int,
                 on_change: Callable[[int, int, float], None],
                 on_select: Callable[[int, int], None]) -> None:
        super().__init__(master, height=215, background="#0b111b",
                         highlightthickness=1, highlightbackground="#31445a",
                         cursor="sb_v_double_arrow")
        self.anchor, self.on_change, self.on_select = anchor, on_change, on_select
        self.values, self.selected = [0.0] * 16, 0
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._mouse_update)
        self.bind("<B1-Motion>", self._mouse_update)

    def set_values(self, values: list[float]) -> None:
        self.values = [max(0.0, min(1.0, float(value))) for value in values]
        self.redraw()

    def _geometry(self) -> tuple[float, float, float, float]:
        return 42.0, 15.0, max(320.0, self.winfo_width() - 12.0), max(140.0, self.winfo_height() - 31.0)

    def _mouse_update(self, event: tk.Event) -> None:
        left, top, right, bottom = self._geometry()
        if not left <= event.x <= right:
            return
        harmonic = max(0, min(15, int((event.x - left) / ((right - left) / 16.0))))
        value = max(0.0, min(1.0, (bottom - event.y) / max(1.0, bottom - top)))
        self.selected, self.values[harmonic] = harmonic, value
        self.on_select(self.anchor, harmonic)
        self.on_change(self.anchor, harmonic, value)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._geometry()
        for level in range(5):
            value = level / 4.0
            y = bottom - value * (bottom - top)
            self.create_line(left, y, right, y, fill="#26364a")
            self.create_text(left - 7, y, text=f"{value:.2g}", fill="#91a4b7", anchor="e", font=("Segoe UI", 8))
        bar_width = (right - left) / 16.0
        for harmonic, value in enumerate(self.values):
            x0, x1 = left + harmonic * bar_width + 3, left + (harmonic + 1) * bar_width - 3
            y = bottom - value * (bottom - top)
            selected = harmonic == self.selected
            self.create_rectangle(x0, y, x1, bottom,
                                  fill="#60e4d0" if selected else "#69aee8",
                                  outline="#e7fff9" if selected else "#82c5ff",
                                  width=2 if selected else 1)
            self.create_text((x0 + x1) / 2, bottom + 11, text=f"{harmonic + 1}×",
                             fill="#b8c8d8", font=("Segoe UI", 8))


class HarmonicDecayCanvas(tk.Canvas):
    """Draggable 16-bin log-time editor for one anchor's partial decays."""

    def __init__(self, master: tk.Misc, anchor: int,
                 on_change: Callable[[int, int, float], None],
                 on_select: Callable[[int, int], None]) -> None:
        super().__init__(master, height=160, background="#120e25",
                         highlightthickness=1, highlightbackground="#493b70",
                         cursor="sb_v_double_arrow")
        self.anchor, self.on_change, self.on_select = anchor, on_change, on_select
        self.values, self.selected = [0.0] * 16, 0
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._mouse_update)
        self.bind("<B1-Motion>", self._mouse_update)

    def set_anchor_values(self, anchor: int, values: list[float],
                          selected: int = 0) -> None:
        self.anchor = anchor
        self.values = [max(0.0, min(MAX_DECAY_MS, float(value))) for value in values]
        self.selected = max(0, min(15, selected))
        self.redraw()

    def _geometry(self) -> tuple[float, float, float, float]:
        return 62.0, 12.0, max(420.0, self.winfo_width() - 12.0), max(115.0, self.winfo_height() - 29.0)

    def _y(self, milliseconds: float) -> float:
        _left, top, _right, bottom = self._geometry()
        if milliseconds <= 0.0:
            return bottom
        value = max(MIN_DECAY_MS, min(MAX_DECAY_MS, milliseconds))
        fraction = math.log(value / MIN_DECAY_MS) / math.log(MAX_DECAY_MS / MIN_DECAY_MS)
        return bottom - fraction * (bottom - top)

    def _value_at_y(self, y: float) -> float:
        _left, top, _right, bottom = self._geometry()
        fraction = max(0.0, min(1.0, (bottom - y) / max(1.0, bottom - top)))
        if fraction < 0.018:
            return 0.0
        return math.exp(math.log(MIN_DECAY_MS) + fraction * math.log(MAX_DECAY_MS / MIN_DECAY_MS))

    def _mouse_update(self, event: tk.Event) -> None:
        left, _top, right, _bottom = self._geometry()
        if not left <= event.x <= right:
            return
        harmonic = max(0, min(15, int((event.x - left) / ((right - left) / 16.0))))
        value = self._value_at_y(event.y)
        self.selected, self.values[harmonic] = harmonic, value
        self.on_select(self.anchor, harmonic)
        self.on_change(self.anchor, harmonic, value)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._geometry()
        for milliseconds in (100, 500, 1000, 5000, 20000, 120000):
            y = self._y(milliseconds)
            self.create_line(left, y, right, y, fill="#2d2548")
            label = f"{milliseconds / 1000:g}s" if milliseconds >= 1000 else f"{milliseconds}ms"
            self.create_text(left - 7, y, text=label, fill="#a79ac4", anchor="e", font=("Segoe UI", 8))
        bar_width = (right - left) / 16.0
        for harmonic, milliseconds in enumerate(self.values):
            x0 = left + harmonic * bar_width + 3
            x1 = left + (harmonic + 1) * bar_width - 3
            y = self._y(milliseconds)
            selected = harmonic == self.selected
            self.create_rectangle(
                x0, y, x1, bottom,
                fill="#ffe47a" if selected else "#b57cff",
                outline="#fff5bd" if selected else "#d1a9ff",
                width=2 if selected else 1,
            )
            self.create_text((x0 + x1) / 2, bottom + 11, text=f"{harmonic + 1}×",
                             fill="#c4b6dc", font=("Segoe UI", 8))


class EditableCurveCanvas(tk.Canvas):
    """Freehand 256-point editor used by amplitude and pitch envelopes."""

    def __init__(self, master: tk.Misc, minimum: float, maximum: float,
                 colour: str, on_change: Callable[[list[float]], None]) -> None:
        super().__init__(master, height=235, background="#0b111b",
                         highlightthickness=1, highlightbackground="#31445a",
                         cursor="crosshair")
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.colour, self.on_change = colour, on_change
        self.values = [0.0] * CURVE_POINTS
        self._last_index = None
        self._last_value = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._start_edit)
        self.bind("<B1-Motion>", self._continue_edit)
        self.bind("<ButtonRelease-1>", self._finish_edit)

    def set_values(self, values: list[float]) -> None:
        self.values = [
            max(self.minimum, min(self.maximum, float(value))) for value in values
        ]
        self.redraw()

    def set_range(self, minimum: float, maximum: float) -> None:
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.values = [
            max(self.minimum, min(self.maximum, float(value)))
            for value in self.values
        ]
        self.redraw()

    def _geometry(self) -> tuple[float, float, float, float]:
        return 48.0, 12.0, max(300.0, self.winfo_width() - 12.0), max(80.0, self.winfo_height() - 27.0)

    def _point(self, x: float, y: float) -> tuple[int, float]:
        left, top, right, bottom = self._geometry()
        index = round((max(left, min(right, x)) - left) / max(1.0, right - left) * (CURVE_POINTS - 1))
        fraction = (bottom - max(top, min(bottom, y))) / max(1.0, bottom - top)
        return index, self.minimum + fraction * (self.maximum - self.minimum)

    def _start_edit(self, event: tk.Event) -> None:
        self._last_index, self._last_value = self._point(event.x, event.y)
        self.values[self._last_index] = self._last_value
        self.redraw()

    def _continue_edit(self, event: tk.Event) -> None:
        index, value = self._point(event.x, event.y)
        if self._last_index is None:
            self._last_index, self._last_value = index, value
        start, end = sorted((self._last_index, index))
        first = self._last_value if self._last_index <= index else value
        last = value if self._last_index <= index else self._last_value
        span = max(1, end - start)
        for offset in range(end - start + 1):
            self.values[start + offset] = first + (last - first) * offset / span
        self._last_index, self._last_value = index, value
        self.redraw()

    def _finish_edit(self, _event: tk.Event) -> None:
        self._last_index = self._last_value = None
        self.on_change(list(self.values))

    def redraw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._geometry()
        for division in range(5):
            fraction = division / 4.0
            y = bottom - fraction * (bottom - top)
            value = self.minimum + fraction * (self.maximum - self.minimum)
            self.create_line(left, y, right, y,
                             fill="#486078" if abs(value) < 1.0e-9 else "#26364a")
            self.create_text(left - 6, y, text=f"{value:g}", anchor="e",
                             fill="#9db0c4", font=("Segoe UI", 8))
        for division in range(9):
            x = left + division * (right - left) / 8.0
            self.create_line(x, top, x, bottom, fill="#26364a")
        points: list[float] = []
        span = max(1.0e-9, self.maximum - self.minimum)
        for index, value in enumerate(self.values):
            x = left + index * (right - left) / (CURVE_POINTS - 1)
            y = bottom - (value - self.minimum) / span * (bottom - top)
            points.extend((x, y))
        self.create_line(*points, fill=self.colour, width=2)


class EnvelopeOverviewCanvas(tk.Canvas):
    """Read-only plot of the complete gate-driven ADSR shape."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, height=235, background="#0b111b",
                         highlightthickness=1, highlightbackground="#31445a")
        self.adsr = None
        self.hold_ms = 900.0
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_envelope(self, adsr: dict, hold_ms: float) -> None:
        self.adsr = deepcopy(adsr)
        self.hold_ms = max(0.1, float(hold_ms))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(360, self.winfo_width()), max(180, self.winfo_height())
        left, top, right, bottom = 54.0, 13.0, width - 13.0, height - 29.0
        for level in range(5):
            value = level / 4.0
            y = bottom - value * (bottom - top)
            self.create_line(left, y, right, y, fill="#26364a")
            self.create_text(left - 7, y, text=f"{value:.2f}", anchor="e",
                             fill="#9db0c4", font=("Segoe UI", 8))
        if not self.adsr:
            return
        try:
            hold_samples = max(1, round(self.hold_ms * SAMPLE_RATE / 1000.0))
            release_samples, _ = stage_timing(float(self.adsr["release_ms"]))
            total_samples = hold_samples + release_samples + round(0.04 * SAMPLE_RATE)
            envelope = amplitude_envelope_samples(
                self.adsr, hold_samples, total_samples
            )
        except (KeyError, TypeError, ValueError):
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text="请输入有效的ADSR时间与Sustain",
                             fill="#ff8c8c", font=("Segoe UI", 10))
            return

        point_count = max(2, int(right - left) + 1)
        points = []
        for column in range(point_count):
            index = min(total_samples - 1,
                        round(column * (total_samples - 1) / (point_count - 1)))
            x = left + column * (right - left) / (point_count - 1)
            y = bottom - float(envelope[index]) * (bottom - top)
            points.extend((x, y))
        self.create_line(*points, fill="#55e6d2", width=2)

        duration_ms = total_samples * 1000.0 / SAMPLE_RATE
        for division in range(5):
            fraction = division / 4.0
            x = left + fraction * (right - left)
            self.create_line(x, top, x, bottom, fill="#26364a")
            self.create_text(x, bottom + 12, text=f"{duration_ms * fraction:.0f} ms",
                             fill="#9db0c4", font=("Segoe UI", 8))
        note_off_x = left + min(1.0, self.hold_ms / duration_ms) * (right - left)
        self.create_line(note_off_x, top, note_off_x, bottom,
                         fill="#ffbf59", dash=(5, 3), width=2)
        self.create_text(note_off_x + 4, top + 4, text="Note Off",
                         anchor="nw", fill="#ffbf59", font=("Segoe UI", 9))


class LiveHarmonicCanvas(tk.Canvas):
    """Read-only sixteen-bin spectrum animated in sync with preview audio."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, height=125, background="#0b111b",
                         highlightthickness=1, highlightbackground="#31445a")
        self.values = [0.0] * 16
        self.ceiling = 1.0
        self.elapsed = 0.0
        self.duration = 0.0
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_reference(self, amplitudes: list[float], duration: float) -> None:
        self.ceiling = max(0.02, max((float(value) for value in amplitudes), default=1.0) * 1.08)
        self.duration = max(0.0, float(duration))
        self.set_values([0.0] * 16, 0.0)

    def set_values(self, values: list[float], elapsed: float) -> None:
        self.values = [max(0.0, float(value)) for value in values]
        self.elapsed = max(0.0, float(elapsed))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(420, self.winfo_width()), max(105, self.winfo_height())
        left, top, right, bottom = 50.0, 10.0, width - 12.0, height - 25.0
        for fraction in (0.0, 0.5, 1.0):
            y = bottom - fraction * (bottom - top)
            self.create_line(left, y, right, y, fill="#26364a")
            self.create_text(left - 6, y, text=f"{self.ceiling * fraction:.3f}",
                             anchor="e", fill="#91a4b7", font=("Segoe UI", 7))
        bar_width = (right - left) / 16.0
        for harmonic, value in enumerate(self.values[:16]):
            fraction = min(1.0, value / self.ceiling)
            x0 = left + harmonic * bar_width + 3
            x1 = left + (harmonic + 1) * bar_width - 3
            y = bottom - fraction * (bottom - top)
            self.create_rectangle(x0, y, x1, bottom, fill="#69aee8", outline="#82c5ff")
            self.create_text((x0 + x1) / 2, bottom + 10, text=str(harmonic + 1),
                             fill="#b8c8d8", font=("Segoe UI", 7))
        self.create_text(right, top, anchor="ne",
                         text=f"{self.elapsed:.2f} / {self.duration:.2f} s",
                         fill="#55e6d2", font=("Segoe UI", 9))


class EnvelopeEditorWindow(tk.Toplevel):
    """Edit/preview the two shared envelope tables without leaving the tool."""

    def __init__(self, master: "HarmonicEditor") -> None:
        super().__init__(master)
        self.editor = master
        self.title("幅度ADSR与Pitch包络")
        self.geometry("1040x690")
        self.minsize(900, 620)
        self.stage_var = tk.StringVar(value="Attack")
        self.preview_anchor_var = tk.StringVar(value=ANCHOR_NAMES[2])
        maximum_pitch = max(
            (abs(float(value)) for value in master.pitch_points), default=0.0
        )
        initial_range = 2.0 if maximum_pitch <= 2.0 else (12.0 if maximum_pitch <= 12.0 else 24.0)
        self.pitch_range_var = tk.StringVar(
            value=next(label for label, value in PITCH_RANGE_OPTIONS.items()
                       if value == initial_range)
        )
        self._preview_after = None
        self._preview_started = 0.0
        self._live_times = None
        self._live_levels = None
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._sync_adsr_stage()
        self.pitch_canvas.set_values(self.editor.pitch_points)
        self._refresh_overview()

    def _build(self) -> None:
        amp = ttk.LabelFrame(self, text="幅度ADSR：时间决定遍历速度，三张RAM保存归一化进度曲线", padding=8)
        amp.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        controls = ttk.Frame(amp); controls.pack(fill="x")
        for key, label in (("attack_ms", "Attack/ms"), ("decay_ms", "Decay/ms"),
                           ("sustain", "Sustain"), ("release_ms", "Release/ms")):
            ttk.Label(controls, text=label).pack(side="left", padx=(4, 2))
            entry = ttk.Entry(controls, textvariable=self.editor.adsr_vars[key], width=8)
            entry.pack(side="left", padx=(0, 8))
            entry.bind("<KeyRelease>", lambda _event: self._refresh_overview())
        ttk.Label(controls, text="编辑阶段").pack(side="left", padx=(8, 2))
        stage = ttk.Combobox(controls, textvariable=self.stage_var,
                             values=("Attack", "Decay", "Release"), state="readonly", width=9)
        stage.pack(side="left"); stage.bind("<<ComboboxSelected>>", lambda _e: self._sync_adsr_stage())
        ttk.Label(controls, text="曲率 -1..1").pack(side="left", padx=(10, 2))
        self.curve_entry = ttk.Entry(controls, width=7); self.curve_entry.pack(side="left")
        ttk.Button(controls, text="按曲率生成", command=self._generate_adsr_stage).pack(side="left", padx=5)

        self.amp_notebook = ttk.Notebook(amp)
        self.amp_notebook.pack(fill="both", expand=True, pady=(6, 0))
        stage_page = ttk.Frame(self.amp_notebook)
        overview_page = ttk.Frame(self.amp_notebook)
        self.amp_notebook.add(stage_page, text="单段进度曲线")
        self.amp_notebook.add(overview_page, text="完整ADSR总形状")
        self.amp_notebook.bind("<<NotebookTabChanged>>", lambda _event: self._refresh_overview())
        self.adsr_canvas = EditableCurveCanvas(stage_page, 0.0, 1.0, "#55e6d2", self._adsr_changed)
        self.adsr_canvas.configure(height=145)
        self.adsr_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.adsr_overview = EnvelopeOverviewCanvas(overview_page)
        self.adsr_overview.configure(height=145)
        self.adsr_overview.pack(fill="both", expand=True)

        pitch = ttk.LabelFrame(
            self,
            text="Pitch包络：一次遍历；到末点保持；ADSR结束时声部与Pitch包络一起停止",
            padding=8,
        )
        pitch.pack(fill="both", expand=True, padx=8, pady=4)
        pitch_controls = ttk.Frame(pitch); pitch_controls.pack(fill="x")
        ttk.Label(pitch_controls, text="遍历时间/ms").pack(side="left")
        ttk.Entry(pitch_controls, textvariable=self.editor.pitch_time_var, width=9).pack(side="left", padx=4)
        ttk.Label(pitch_controls, text="编辑量程").pack(side="left", padx=(12, 2))
        pitch_range = ttk.Combobox(
            pitch_controls, textvariable=self.pitch_range_var,
            values=tuple(PITCH_RANGE_OPTIONS), state="readonly", width=18,
        )
        pitch_range.pack(side="left")
        pitch_range.bind("<<ComboboxSelected>>", self._pitch_range_changed)
        ttk.Label(pitch_controls, text="纵轴单位：半音；FPGA在原音附近约0.42 cent/LSB").pack(side="left", padx=12)
        ttk.Button(pitch_controls, text="归零", command=self._clear_pitch).pack(side="right")
        pitch_limit = PITCH_RANGE_OPTIONS[self.pitch_range_var.get()]
        self.pitch_canvas = EditableCurveCanvas(
            pitch, -pitch_limit, pitch_limit,
            "#ffbf59", self._pitch_changed,
        )
        self.pitch_canvas.configure(height=145)
        self.pitch_canvas.pack(fill="both", expand=True, pady=(6, 0))

        actions = ttk.Frame(self, padding=(8, 5)); actions.pack(fill="x")
        ttk.Label(actions, text="试听基准").pack(side="left")
        ttk.Combobox(actions, textvariable=self.preview_anchor_var,
                     values=ANCHOR_NAMES, state="readonly", width=6).pack(side="left", padx=4)
        ttk.Label(actions, text="按住/ms").pack(side="left", padx=(8, 2))
        hold_entry = ttk.Entry(actions, textvariable=self.editor.preview_hold_var, width=8)
        hold_entry.pack(side="left")
        hold_entry.bind("<KeyRelease>", lambda _event: self._refresh_overview())
        ttk.Button(actions, text="试听当前音色", command=self._play).pack(side="left", padx=(12, 4))
        ttk.Button(actions, text="停止", command=self._stop).pack(side="left")
        ttk.Button(actions, text="发送两组包络", command=self._send).pack(side="right", padx=4)
        ttk.Button(actions, text="导出为上电默认", command=self.editor._export_fpga_files).pack(side="right", padx=4)

        live = ttk.LabelFrame(
            self,
            text="试听实时谐波幅度：已包含单谐波衰减和完整ADSR（固定纵轴，便于观察真实消失过程）",
            padding=(6, 3),
        )
        live.pack(fill="x", padx=8, pady=(0, 5), before=actions)
        self.live_harmonics = LiveHarmonicCanvas(live)
        self.live_harmonics.pack(fill="x")

    def _stage_key(self) -> str:
        return self.stage_var.get().lower()

    def _sync_adsr_stage(self) -> None:
        key = self._stage_key()
        self.adsr_canvas.set_values(self.editor.adsr_tables[key])
        self.curve_entry.delete(0, "end")
        self.curve_entry.insert(0, self.editor.adsr_curve_vars[key].get())

    def _current_adsr(self) -> dict:
        adsr = {
            key: float(variable.get())
            for key, variable in self.editor.adsr_vars.items()
        }
        for key in ("attack", "decay", "release"):
            adsr[key + "_curve"] = float(self.editor.adsr_curve_vars[key].get())
            adsr[key + "_table"] = list(self.editor.adsr_tables[key])
        return adsr

    def _refresh_overview(self) -> None:
        try:
            self.adsr_overview.set_envelope(
                self._current_adsr(), float(self.editor.preview_hold_var.get())
            )
        except (TypeError, ValueError):
            self.adsr_overview.adsr = None
            self.adsr_overview.redraw()

    def _generate_adsr_stage(self) -> None:
        key = self._stage_key()
        try:
            value = float(self.curve_entry.get())
        except ValueError:
            messagebox.showerror("曲率错误", "曲率必须是-1～1之间的数字。", parent=self); return
        if not -1.0 <= value <= 1.0:
            messagebox.showerror("曲率错误", "曲率必须是-1～1之间的数字。", parent=self); return
        self.editor.adsr_curve_vars[key].set(str(value))
        self.editor.adsr_tables[key] = curve_lut(value)
        self.adsr_canvas.set_values(self.editor.adsr_tables[key])
        self._refresh_overview()

    def _adsr_changed(self, values: list[float]) -> None:
        key = self._stage_key()
        values[0], values[-1] = 0.0, 1.0
        self.editor.adsr_tables[key] = values
        self._refresh_overview()

    def _pitch_changed(self, values: list[float]) -> None:
        self.editor.pitch_points = values

    def _pitch_range_changed(self, _event: object = None) -> None:
        limit = PITCH_RANGE_OPTIONS[self.pitch_range_var.get()]
        self.editor.pitch_points = [
            max(-limit, min(limit, float(value)))
            for value in self.editor.pitch_points
        ]
        self.pitch_canvas.set_range(-limit, limit)
        self.pitch_canvas.set_values(self.editor.pitch_points)
        self.editor.status_var.set(
            f"Pitch编辑量程已切换为±{limit:g}半音；超出新量程的点已钳位。"
        )

    def _clear_pitch(self) -> None:
        self.editor.pitch_points = [0.0] * CURVE_POINTS
        self.pitch_canvas.set_values(self.editor.pitch_points)

    def _play(self) -> None:
        try:
            import sounddevice as sd
            preset = self.editor._collect()
            anchor = ANCHOR_NAMES.index(self.preview_anchor_var.get())
            samples = render_additive_preview(
                preset, anchor, float(self.editor.preview_hold_var.get())
            )
            self._live_times, self._live_levels = preview_harmonic_frames(
                preset, anchor, float(self.editor.preview_hold_var.get())
            )
            self.live_harmonics.set_reference(
                preset["anchors"][anchor]["amplitudes"], float(self._live_times[-1])
            )
            if self._preview_after is not None:
                self.after_cancel(self._preview_after)
                self._preview_after = None
            sd.stop(); sd.play(samples, int(round(SAMPLE_RATE)), blocking=False)
            self._preview_started = time.perf_counter()
            self._animate_harmonics()
            self._refresh_overview()
            self.editor.status_var.set("正在试听：包含单谐波衰减、ADSR和Pitch包络。")
        except Exception as error:
            messagebox.showerror("试听失败", str(error), parent=self)

    def _animate_harmonics(self) -> None:
        self._preview_after = None
        if self._live_times is None or self._live_levels is None:
            return
        elapsed = time.perf_counter() - self._preview_started
        if len(self._live_times) > 1:
            step = max(1.0e-6, float(self._live_times[1] - self._live_times[0]))
            index = min(len(self._live_times) - 1, int(elapsed / step))
        else:
            index = 0
        self.live_harmonics.set_values(self._live_levels[index], elapsed)
        if elapsed <= float(self._live_times[-1]):
            self._preview_after = self.after(33, self._animate_harmonics)

    def _stop(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        if hasattr(self, "live_harmonics"):
            self.live_harmonics.set_values([0.0] * 16, 0.0)

    def _close(self) -> None:
        self._stop()
        self.destroy()

    def _send(self) -> None:
        port = self.editor.port_var.get().strip()
        if not port:
            messagebox.showerror("没有串口", "请选择板载调试器对应的COM口。", parent=self); return
        try:
            preset = self.editor._collect()
            count = send_packets(port, envelope_packets(preset))
            self.editor.status_var.set(
                f"已发送{count}包ADSR/Pitch数据；建议无音符发声时更新曲线。"
            )
        except Exception as error:
            messagebox.showerror("发送失败", str(error), parent=self)


class HarmonicEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Primer 25K 四基准加法音色编辑器")
        self.geometry("1060x720")
        self.minsize(920, 640)
        self.preset = load_preset(DEFAULT_PRESET)
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="四基准各自保存16组独立相对衰减；修改后点击“发送全部”。")
        self.scale_factor_var = tk.StringVar(value="1.0")
        self.preview_summary_var = tk.StringVar()
        self.selected_amp_var = tk.StringVar(value="0")
        self.selected_amp_label_var = tk.StringVar(value="1×")
        self.selected_decay_var = tk.StringVar(value="0")
        self.selected_decay_label_var = tk.StringVar(value="C2 / 1×")
        self.decay_point_var = tk.StringVar()
        self.performance_mode_var = tk.StringVar(value="普通演奏")
        self.strum_interval_var = tk.StringVar(value="18")
        self.strum_gate_var = tk.StringVar(value="120")
        self.strum_speed_depth_var = tk.StringVar(value="80")
        self.strum_brightness_depth_var = tk.StringVar(value="96")
        self.pitch_time_var = tk.StringVar(value="250")
        self.preview_hold_var = tk.StringVar(value="900")
        # Keep Python 3.7 compatibility (the bundled Gowin-side Python on some
        # machines is older than the interpreter used during development).
        self._preview_job = None
        self._selected_anchor, self._selected_harmonic = 0, 0
        self.adsr_vars = {name: tk.StringVar() for name in ("attack_ms", "decay_ms", "sustain", "release_ms")}
        self.adsr_curve_vars = {
            name: tk.StringVar() for name in ("attack", "decay", "release")
        }
        self.adsr_tables = {
            name: curve_lut(default) for name, default in
            (("attack", 0.9), ("decay", 0.6), ("release", 0.3))
        }
        self.pitch_points = [0.0] * CURVE_POINTS
        self.amp_values = [[0.0] * 16 for _ in range(4)]
        self.decay_values = [[0.0] * 16 for _ in range(4)]
        self.bar_canvases: list[HarmonicBarCanvas] = []
        self._build()
        self._load_into_widgets(self.preset)
        self._refresh_ports()

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 5)); toolbar.pack(fill="x")
        ttk.Label(toolbar, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(toolbar, textvariable=self.port_var, width=12)
        self.port_box.pack(side="left", padx=5)
        ttk.Button(toolbar, text="刷新", command=self._refresh_ports).pack(side="left")
        ttk.Button(toolbar, text="载入", command=self._load_file).pack(side="left", padx=(18, 4))
        ttk.Button(toolbar, text="保存", command=self._save_file).pack(side="left", padx=4)
        ttk.Button(toolbar, text="发送全部", command=self._send_all).pack(side="left", padx=(18, 4))
        ttk.Button(toolbar, text="包络编辑/试听", command=self._open_envelope_editor).pack(side="left", padx=4)
        ttk.Button(toolbar, text="导出上电默认", command=self._export_fpga_files).pack(side="left", padx=4)

        adsr = ttk.LabelFrame(self, text="全局幅度 ADSR", padding=(8, 5)); adsr.pack(fill="x", padx=8, pady=(0, 3))
        labels = (("attack_ms", "Attack ms"), ("decay_ms", "Decay ms"),
                  ("sustain", "Sustain 0..1"), ("release_ms", "Release ms"))
        for column, (key, label) in enumerate(labels):
            ttk.Label(adsr, text=label).grid(row=0, column=column * 2, padx=(4, 2))
            ttk.Entry(adsr, textvariable=self.adsr_vars[key], width=10).grid(row=0, column=column * 2 + 1, padx=(0, 12))

        performance = ttk.LabelFrame(self, text="演奏方式（扫弦模式：按住3～4个琴键，Pad 1下扫，Pad 2上扫）", padding=(8, 4))
        performance.pack(fill="x", padx=8, pady=(0, 3))
        ttk.Label(performance, text="模式").pack(side="left")
        ttk.Combobox(performance, textvariable=self.performance_mode_var,
                     values=("普通演奏", "吉他扫弦"), state="readonly", width=9).pack(side="left", padx=(3, 10))
        ttk.Label(performance, text="弦间隔/ms").pack(side="left")
        ttk.Entry(performance, textvariable=self.strum_interval_var, width=6).pack(side="left", padx=(3, 10))
        ttk.Label(performance, text="Gate/ms").pack(side="left")
        ttk.Entry(performance, textvariable=self.strum_gate_var, width=7).pack(side="left", padx=(3, 10))
        ttk.Label(performance, text="力度→速度 0..127").pack(side="left")
        ttk.Entry(performance, textvariable=self.strum_speed_depth_var, width=5).pack(side="left", padx=(3, 10))
        ttk.Label(performance, text="力度→亮度 0..127").pack(side="left")
        ttk.Entry(performance, textvariable=self.strum_brightness_depth_var, width=5).pack(side="left", padx=(3, 8))
        ttk.Button(performance, text="只发送演奏设置", command=self._send_performance).pack(side="right")

        middle = ttk.Panedwindow(self, orient="horizontal"); middle.pack(fill="both", expand=True, padx=8, pady=2)
        spectra = ttk.LabelFrame(middle, text="四基准谐波幅度（拖动柱形）", padding=(6, 3)); middle.add(spectra, weight=3)
        self.notebook = ttk.Notebook(spectra); self.notebook.pack(fill="both", expand=True)
        for anchor, name in enumerate(ANCHOR_NAMES):
            frame = ttk.Frame(self.notebook, padding=(4, 3)); self.notebook.add(frame, text=name)
            canvas = HarmonicBarCanvas(frame, anchor, self._bar_changed, self._bar_selected)
            canvas.pack(fill="both", expand=True); self.bar_canvases.append(canvas)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        exact = ttk.Frame(spectra); exact.pack(fill="x", pady=(4, 0))
        ttk.Label(exact, text="选中").pack(side="left")
        ttk.Label(exact, textvariable=self.selected_amp_label_var, width=4).pack(side="left", padx=(3, 8))
        ttk.Label(exact, text="幅度").pack(side="left")
        ttk.Entry(exact, textvariable=self.selected_amp_var, width=8).pack(side="left", padx=3)
        ttk.Button(exact, text="应用", command=self._apply_exact_amplitude).pack(side="left")
        ttk.Label(exact, text="倍率").pack(side="left", padx=(10, 2))
        ttk.Entry(exact, textvariable=self.scale_factor_var, width=6).pack(side="left", padx=2)
        ttk.Button(exact, text="缩放当前", command=lambda: self._apply_harmonic_scale(False)).pack(side="left", padx=2)
        ttk.Button(exact, text="缩放全部", command=lambda: self._apply_harmonic_scale(True)).pack(side="left", padx=2)
        ttk.Button(exact, text="当前归一化", command=self._normalize_current_anchor).pack(side="left", padx=(6, 2))

        preview = ttk.LabelFrame(middle, text="当前基准单周期预览", padding=(5, 3)); middle.add(preview, weight=2)
        self.wave_canvas = tk.Canvas(preview, background="#0c1420", highlightthickness=1, highlightbackground="#31445a")
        self.wave_canvas.pack(fill="both", expand=True); self.wave_canvas.bind("<Configure>", self._schedule_preview)
        ttk.Label(preview, textvariable=self.preview_summary_var, wraplength=330).pack(fill="x", pady=(3, 0))

        decay = ttk.LabelFrame(
            self,
            text="当前基准的16次谐波独立相对衰减（纵轴为时间常数；越高衰减越慢）",
            padding=(6, 3),
        )
        decay.pack(fill="x", padx=8, pady=(2, 1))
        self.decay_canvas = HarmonicDecayCanvas(
            decay, 0, self._decay_changed, self._decay_selected
        )
        self.decay_canvas.pack(fill="x")
        decay_exact = ttk.Frame(decay); decay_exact.pack(fill="x", pady=(2, 0))
        ttk.Label(decay_exact, text="选中").pack(side="left")
        ttk.Label(decay_exact, textvariable=self.selected_decay_label_var, width=9).pack(side="left", padx=(3, 8))
        ttk.Label(decay_exact, text="相对衰减τ/ms（0=保持）").pack(side="left")
        ttk.Entry(decay_exact, textvariable=self.selected_decay_var, width=12).pack(side="left", padx=3)
        ttk.Button(decay_exact, text="应用", command=self._apply_exact_decay).pack(side="left")
        ttk.Button(decay_exact, text="设为保持", command=self._disable_selected_decay).pack(side="left", padx=4)
        ttk.Label(decay_exact, textvariable=self.decay_point_var).pack(side="left", padx=(12, 0))
        ttk.Label(self, textvariable=self.status_var, padding=(8, 4)).pack(fill="x")

    def _current_anchor_index(self) -> int:
        try:
            return int(self.notebook.index("current"))
        except (tk.TclError, ValueError):
            return 0

    def _tab_changed(self, *_args) -> None:
        self._selected_anchor = self._current_anchor_index()
        self._selected_harmonic = self.bar_canvases[self._selected_anchor].selected
        self.decay_canvas.set_anchor_values(
            self._selected_anchor, self.decay_values[self._selected_anchor],
            self._selected_harmonic,
        )
        self._sync_selected_amplitude(); self._sync_selected_decay(); self._schedule_preview()

    def _bar_selected(self, anchor: int, harmonic: int) -> None:
        self._selected_anchor, self._selected_harmonic = anchor, harmonic
        self.decay_canvas.set_anchor_values(anchor, self.decay_values[anchor], harmonic)
        self._sync_selected_amplitude(); self._sync_selected_decay()

    def _bar_changed(self, anchor: int, harmonic: int, value: float) -> None:
        self.amp_values[anchor][harmonic] = value
        self._selected_anchor, self._selected_harmonic = anchor, harmonic
        self.decay_canvas.set_anchor_values(anchor, self.decay_values[anchor], harmonic)
        self._sync_selected_amplitude(); self._sync_selected_decay(); self._schedule_preview()

    def _sync_selected_amplitude(self) -> None:
        value = self.amp_values[self._selected_anchor][self._selected_harmonic]
        self.selected_amp_label_var.set(f"{self._selected_harmonic + 1}×")
        self.selected_amp_var.set(f"{value:.5f}")

    def _apply_exact_amplitude(self) -> None:
        try:
            value = float(self.selected_amp_var.get())
        except ValueError:
            value = -1.0
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            messagebox.showerror("幅度错误", "幅度必须是0～1之间的数字。"); return
        self.amp_values[self._selected_anchor][self._selected_harmonic] = value
        self.bar_canvases[self._selected_anchor].set_values(self.amp_values[self._selected_anchor])
        self._schedule_preview()

    def _decay_selected(self, anchor: int, harmonic: int) -> None:
        self._selected_anchor, self._selected_harmonic = anchor, harmonic
        self.bar_canvases[anchor].selected = harmonic
        self.bar_canvases[anchor].redraw()
        self._sync_selected_amplitude(); self._sync_selected_decay()

    def _decay_changed(self, anchor: int, harmonic: int, value: float) -> None:
        self.decay_values[anchor][harmonic] = value
        self._selected_anchor, self._selected_harmonic = anchor, harmonic
        self._sync_selected_decay()

    def _sync_selected_decay(self) -> None:
        value = self.decay_values[self._selected_anchor][self._selected_harmonic]
        self.selected_decay_label_var.set(
            f"{ANCHOR_NAMES[self._selected_anchor]} / {self._selected_harmonic + 1}×"
        )
        self.selected_decay_var.set(f"{value:.3f}")
        frequency = (440.0 * 2.0 ** ((ANCHOR_MIDI_NOTES[self._selected_anchor] - 69) / 12.0)
                     * (self._selected_harmonic + 1))
        self.decay_point_var.set(f"该分音约 {frequency:.1f} Hz")

    def _apply_exact_decay(self) -> None:
        try:
            value = float(self.selected_decay_var.get())
        except ValueError:
            value = -1.0
        if not math.isfinite(value) or not 0.0 <= value <= MAX_DECAY_MS:
            messagebox.showerror("衰减错误", "时间常数必须为0～120000 ms；0表示不额外衰减。"); return
        anchor, harmonic = self._selected_anchor, self._selected_harmonic
        self.decay_values[anchor][harmonic] = value
        self.decay_canvas.set_anchor_values(anchor, self.decay_values[anchor], harmonic)
        self._sync_selected_decay()

    def _disable_selected_decay(self) -> None:
        self.selected_decay_var.set("0")
        self._apply_exact_decay()

    def _schedule_preview(self, *_args) -> None:
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(60, self._draw_waveform_preview)

    def _draw_waveform_preview(self) -> None:
        self._preview_job = None; canvas = self.wave_canvas; canvas.delete("all")
        width, height = max(280, canvas.winfo_width()), max(160, canvas.winfo_height()); middle = height / 2.0
        for division in range(9):
            x = division * (width - 1) / 8.0; canvas.create_line(x, 0, x, height, fill="#203247")
        for level in (-1.0, -0.5, 0.0, 0.5, 1.0):
            y = middle - level * height * 0.43
            canvas.create_line(0, y, width, y, fill="#52677d" if level == 0.0 else "#203247")
        anchor = self._current_anchor_index(); amplitudes = self.amp_values[anchor]
        count = max(256, min(1024, width))
        waveform = [sum(amp * math.sin((harmonic + 1) * 2 * math.pi * sample / count)
                        for harmonic, amp in enumerate(amplitudes)) for sample in range(count)]
        peak = max((abs(value) for value in waveform), default=0.0)
        rms = math.sqrt(sum(value * value for value in waveform) / len(waveform)) if waveform else 0.0
        points: list[float] = []; display_peak = max(1.0, peak)
        for sample, value in enumerate(waveform):
            points.extend((sample * (width - 1) / (count - 1), middle - value / display_peak * height * 0.43))
        canvas.create_line(*points, fill="#ff7070" if peak > 1.0 else "#55e6d2", width=2)
        warning = "；已缩放显示，实际可能削顶" if peak > 1.0 else ""
        self.preview_summary_var.set(f"{ANCHOR_NAMES[anchor]}\nΣ幅度={sum(amplitudes):.3f}\n峰值={peak:.3f}  RMS={rms:.3f}{warning}")

    def _apply_harmonic_scale(self, all_anchors: bool) -> None:
        try:
            factor = float(self.scale_factor_var.get())
        except ValueError:
            factor = -1.0
        if not math.isfinite(factor) or not 0.0 <= factor <= 4.0:
            messagebox.showerror("倍率错误", "整体谐波倍率范围为0～4。"); return
        anchors = range(4) if all_anchors else (self._current_anchor_index(),); clipped = False
        for anchor in anchors:
            clipped |= any(value * factor > 1.0 for value in self.amp_values[anchor])
            self.amp_values[anchor] = [min(1.0, max(0.0, value * factor)) for value in self.amp_values[anchor]]
            self.bar_canvases[anchor].set_values(self.amp_values[anchor])
        self._sync_selected_amplitude(); self._schedule_preview()
        self.status_var.set("谐波幅度已缩放" + ("；超过1的值已钳位。" if clipped else "。"))

    def _normalize_current_anchor(self) -> None:
        anchor = self._current_anchor_index(); total = sum(self.amp_values[anchor])
        if total <= 0.0:
            messagebox.showerror("无法归一化", "当前16个谐波幅度全为0。"); return
        self.amp_values[anchor] = [value / total for value in self.amp_values[anchor]]
        self.bar_canvases[anchor].set_values(self.amp_values[anchor])
        self._sync_selected_amplitude(); self._schedule_preview()

    def _refresh_ports(self) -> None:
        available, source = True, "pyserial"
        try:
            from serial.tools import list_ports
            ports = [port.device for port in list_ports.comports()]
        except ImportError:
            available, ports = False, []
        if not ports:
            source = "dotnet"
            try:
                result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                         "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object"],
                                        check=False, capture_output=True, text=True, timeout=3)
                ports = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            except (OSError, subprocess.SubprocessError):
                pass
        if not ports:
            source = "pnp"
            try:
                result = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | ForEach-Object { "
                    "if ($_.FriendlyName -match '\\((COM[0-9]+)\\)') { $Matches[1] } } | Sort-Object -Unique"],
                    check=False, capture_output=True, text=True, timeout=5)
                ports = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            except (OSError, subprocess.SubprocessError):
                pass
        self.port_box["values"] = ports
        if ports and not self.port_var.get(): self.port_var.set(ports[0])
        if not available:
            self.status_var.set("串口发送需要pyserial；请运行install_python_requirements.bat。")
        elif not ports:
            self.status_var.set("没有发现串口；也可以直接输入COM号。")
        elif source == "pnp":
            self.status_var.set("这些COM号来自Windows设备列表；请选择板载调试器端口。")

    def _load_into_widgets(self, preset: dict) -> None:
        preset = normalise_envelopes(preset)
        self.preset = deepcopy(preset)
        for key, variable in self.adsr_vars.items(): variable.set(str(preset["adsr"][key]))
        for key in ("attack", "decay", "release"):
            self.adsr_curve_vars[key].set(str(preset["adsr"][key + "_curve"]))
            self.adsr_tables[key] = list(preset["adsr"][key + "_table"])
        self.pitch_time_var.set(str(preset["pitch_envelope"]["time_ms"]))
        self.pitch_points = list(preset["pitch_envelope"]["points_semitones"])
        for anchor, data in enumerate(preset["anchors"]):
            self.amp_values[anchor] = [float(value) for value in data["amplitudes"]]
            self.decay_values[anchor] = [float(value) for value in data["decay_ms"]]
            self.bar_canvases[anchor].set_values(self.amp_values[anchor])
        performance = preset.get("performance", DEFAULT_PERFORMANCE)
        self.performance_mode_var.set(
            "吉他扫弦" if performance.get("mode") == "strum" else "普通演奏")
        self.strum_interval_var.set(str(performance.get("strum_interval_ms", 18)))
        self.strum_gate_var.set(str(performance.get("strum_gate_ms", 120)))
        self.strum_speed_depth_var.set(str(performance.get("velocity_speed_depth", 80)))
        self.strum_brightness_depth_var.set(str(performance.get("velocity_brightness_depth", 96)))
        self.decay_canvas.set_anchor_values(
            self._selected_anchor, self.decay_values[self._selected_anchor],
            self._selected_harmonic,
        )
        self._sync_selected_amplitude(); self._sync_selected_decay(); self._schedule_preview()

    def _collect(self) -> dict:
        preset = deepcopy(self.preset); preset["format"] = "gowin-additive-tone-v2"
        preset["adsr"] = {key: float(variable.get()) for key, variable in self.adsr_vars.items()}
        for key in ("attack", "decay", "release"):
            preset["adsr"][key + "_curve"] = float(self.adsr_curve_vars[key].get())
            preset["adsr"][key + "_table"] = [
                round(value, 7) for value in self.adsr_tables[key]
            ]
        preset["pitch_envelope"] = {
            "time_ms": float(self.pitch_time_var.get()),
            "points_semitones": [round(value, 5) for value in self.pitch_points],
        }
        for anchor, data in enumerate(preset["anchors"]):
            data["amplitudes"] = [round(value, 6) for value in self.amp_values[anchor]]
            data["decay_ms"] = [round(value, 3) for value in self.decay_values[anchor]]
        preset.pop("decay_curve", None)
        preset["decay_table_source"] = "independent-4anchor-x-16partial"
        preset["performance"] = {
            "mode": "strum" if self.performance_mode_var.get() == "吉他扫弦" else "normal",
            "strum_interval_ms": int(self.strum_interval_var.get()),
            "strum_gate_ms": int(self.strum_gate_var.get()),
            "velocity_speed_depth": int(self.strum_speed_depth_var.get()),
            "velocity_brightness_depth": int(self.strum_brightness_depth_var.get()),
        }
        preset_packets(preset)
        return preset

    def _open_envelope_editor(self) -> None:
        EnvelopeEditorWindow(self)

    def _export_fpga_files(self) -> None:
        try:
            preset = self._collect()
            output = HERE.parent / "src" / "generated"
            export_additive_preset(preset, output)
            self.preset = preset
            self.status_var.set(
                "已更新src/generated：重新综合后该音色和两组包络成为上电默认。"
            )
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self)

    def _send_performance(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("没有串口", "请选择板载调试器对应的COM口。"); return
        try:
            preset = self._collect()
            count = send_packets(port, [strum_packet(preset["performance"])])
            self.preset = preset
            mode = "扫弦" if preset["performance"]["mode"] == "strum" else "普通"
            self.status_var.set(f"已发送{count}包演奏设置；当前为{mode}模式。")
        except Exception as error:
            messagebox.showerror("发送失败", f"{error}\n\n请关闭占用同一COM口的串口助手后重试。")

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("Tone JSON", "*.json"), ("All files", "*.*")))
        if path:
            try:
                self._load_into_widgets(load_preset(path)); self.status_var.set(f"已载入 {path}")
            except Exception as error:
                messagebox.showerror("载入失败", str(error))

    def _save_file(self) -> None:
        try:
            preset = self._collect()
        except Exception as error:
            messagebox.showerror("参数错误", str(error)); return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("Tone JSON", "*.json"),))
        if path:
            Path(path).write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
            self.preset = preset; self.status_var.set(f"已保存 {path}")

    def _send_all(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("没有串口", "请选择板载调试器对应的COM口。"); return
        try:
            preset = self._collect(); count = send_packets(port, preset_packets(preset)); self.preset = preset
            self.status_var.set(f"已向 {port} 发送 {count}包。请重新按下琴键试听新音色。")
        except Exception as error:
            messagebox.showerror("发送失败", f"{error}\n\n请关闭占用同一COM口的串口助手后重试。")


if __name__ == "__main__":
    HarmonicEditor().mainloop()
