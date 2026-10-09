"""Four-anchor editor with independent amplitude and decay per partial."""

from __future__ import annotations

import json
import math
import subprocess
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from additive_uart_protocol import (
    ANCHOR_MIDI_NOTES, ANCHOR_NAMES, load_preset, preset_packets, send_packets,
)

HERE = Path(__file__).resolve().parent
DEFAULT_PRESET = HERE / "pianotone2.json"
MIN_DECAY_MS, MAX_DECAY_MS = 50.0, 120000.0


class HarmonicBarCanvas(tk.Canvas):
    """Draggable sixteen-bin harmonic spectrum."""

    def __init__(self, master: tk.Misc, anchor: int,
                 on_change: Callable[[int, int, float], None],
                 on_select: Callable[[int, int], None]) -> None:
        super().__init__(master, height=245, background="#0b111b",
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
        super().__init__(master, height=190, background="#120e25",
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


class HarmonicEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Primer 25K 四基准加法音色编辑器")
        self.geometry("1060x760")
        self.minsize(920, 680)
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
        # Keep Python 3.7 compatibility (the bundled Gowin-side Python on some
        # machines is older than the interpreter used during development).
        self._preview_job = None
        self._selected_anchor, self._selected_harmonic = 0, 0
        self.adsr_vars = {name: tk.StringVar() for name in ("attack_ms", "decay_ms", "sustain", "release_ms")}
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

        adsr = ttk.LabelFrame(self, text="全局幅度 ADSR", padding=(8, 5)); adsr.pack(fill="x", padx=8, pady=(0, 3))
        labels = (("attack_ms", "Attack ms"), ("decay_ms", "Decay ms"),
                  ("sustain", "Sustain 0..1"), ("release_ms", "Release ms"))
        for column, (key, label) in enumerate(labels):
            ttk.Label(adsr, text=label).grid(row=0, column=column * 2, padx=(4, 2))
            ttk.Entry(adsr, textvariable=self.adsr_vars[key], width=10).grid(row=0, column=column * 2 + 1, padx=(0, 12))

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
        self.preset = deepcopy(preset)
        for key, variable in self.adsr_vars.items(): variable.set(str(preset["adsr"][key]))
        for anchor, data in enumerate(preset["anchors"]):
            self.amp_values[anchor] = [float(value) for value in data["amplitudes"]]
            self.decay_values[anchor] = [float(value) for value in data["decay_ms"]]
            self.bar_canvases[anchor].set_values(self.amp_values[anchor])
        self.decay_canvas.set_anchor_values(
            self._selected_anchor, self.decay_values[self._selected_anchor],
            self._selected_harmonic,
        )
        self._sync_selected_amplitude(); self._sync_selected_decay(); self._schedule_preview()

    def _collect(self) -> dict:
        preset = deepcopy(self.preset); preset["format"] = "gowin-additive-tone-v2"
        preset["adsr"] = {key: float(variable.get()) for key, variable in self.adsr_vars.items()}
        for anchor, data in enumerate(preset["anchors"]):
            data["amplitudes"] = [round(value, 6) for value in self.amp_values[anchor]]
            data["decay_ms"] = [round(value, 3) for value in self.decay_values[anchor]]
        preset.pop("decay_curve", None)
        preset["decay_table_source"] = "independent-4anchor-x-16partial"
        preset_packets(preset)
        return preset

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
