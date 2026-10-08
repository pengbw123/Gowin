"""Tkinter editor for four pitch anchors and sixteen additive harmonics."""

from __future__ import annotations

import json
import math
import subprocess
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from additive_uart_protocol import ANCHOR_NAMES, load_preset, preset_packets, send_packets


HERE = Path(__file__).resolve().parent
DEFAULT_PRESET = HERE / "additive_piano_4anchor.json"


class HarmonicEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Primer 25K 四基准谐波编辑器")
        self.geometry("1020x820")
        self.minsize(920, 700)
        self.preset = load_preset(DEFAULT_PRESET)
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="修改后点击“发送全部”；重新按键后使用新谐波参数。")
        self.scale_factor_var = tk.StringVar(value="1.0")
        self.preview_summary_var = tk.StringVar(value="")
        self._preview_job = None
        self.adsr_vars = {name: tk.StringVar() for name in ("attack_ms", "decay_ms", "sustain", "release_ms")}
        self.amp_vars: list[list[tk.StringVar]] = []
        self.decay_vars: list[list[tk.StringVar]] = []
        self._build()
        self._load_into_widgets(self.preset)
        self._refresh_ports()

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 5))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(toolbar, textvariable=self.port_var, width=12)
        self.port_box.pack(side="left", padx=5)
        ttk.Button(toolbar, text="刷新", command=self._refresh_ports).pack(side="left")
        ttk.Button(toolbar, text="载入", command=self._load_file).pack(side="left", padx=(18, 4))
        ttk.Button(toolbar, text="保存", command=self._save_file).pack(side="left", padx=4)
        ttk.Button(toolbar, text="发送全部", command=self._send_all).pack(side="left", padx=(18, 4))

        adsr = ttk.LabelFrame(self, text="全局幅度 ADSR", padding=(8, 5))
        adsr.pack(fill="x", padx=8, pady=(0, 3))
        labels = (("attack_ms", "Attack ms"), ("decay_ms", "Decay ms"),
                  ("sustain", "Sustain 0..1"), ("release_ms", "Release ms"))
        for column, (key, label) in enumerate(labels):
            ttk.Label(adsr, text=label).grid(row=0, column=column * 2, padx=(4, 2))
            ttk.Entry(adsr, textvariable=self.adsr_vars[key], width=10).grid(row=0, column=column * 2 + 1, padx=(0, 12))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=2)
        for anchor_index, anchor_name in enumerate(ANCHOR_NAMES):
            frame = ttk.Frame(self.notebook, padding=(6, 3))
            self.notebook.add(frame, text=anchor_name)
            ttk.Label(frame, text="谐波").grid(row=0, column=0, padx=5)
            ttk.Label(frame, text="幅度 0..1").grid(row=0, column=1, columnspan=2, padx=5)
            ttk.Label(frame, text="衰减时间常数 ms（越大越慢，0=不额外衰减）").grid(row=0, column=3, padx=8)
            anchor_amp: list[tk.StringVar] = []
            anchor_decay: list[tk.StringVar] = []
            for harmonic in range(16):
                amp_var = tk.StringVar()
                decay_var = tk.StringVar()
                anchor_amp.append(amp_var)
                anchor_decay.append(decay_var)
                amp_var.trace_add("write", self._schedule_preview)
                ttk.Label(frame, text=f"{harmonic + 1}×").grid(row=harmonic + 1, column=0, sticky="e", padx=5)
                scale = ttk.Scale(frame, from_=0.0, to=1.0, variable=amp_var, orient="horizontal", length=360)
                scale.grid(row=harmonic + 1, column=1, sticky="ew", padx=5)
                ttk.Entry(frame, textvariable=amp_var, width=9).grid(row=harmonic + 1, column=2, padx=5)
                ttk.Entry(frame, textvariable=decay_var, width=12).grid(row=harmonic + 1, column=3, padx=8)
            frame.columnconfigure(1, weight=1)
            self.amp_vars.append(anchor_amp)
            self.decay_vars.append(anchor_decay)

        self.notebook.bind("<<NotebookTabChanged>>", self._schedule_preview)

        preview = ttk.LabelFrame(
            self,
            text="当前基准的初始谐波单周期（不含ADSR与谐波衰减）",
            padding=(6, 3),
        )
        preview.pack(fill="x", padx=8, pady=(2, 1))
        controls = ttk.Frame(preview)
        controls.pack(fill="x", pady=(0, 3))
        ttk.Label(controls, text="整体谐波倍率").pack(side="left")
        ttk.Entry(controls, textvariable=self.scale_factor_var, width=8).pack(side="left", padx=5)
        ttk.Button(
            controls, text="缩放当前基准", command=lambda: self._apply_harmonic_scale(False)
        ).pack(side="left", padx=3)
        ttk.Button(
            controls, text="缩放全部基准", command=lambda: self._apply_harmonic_scale(True)
        ).pack(side="left", padx=3)
        ttk.Button(
            controls, text="当前幅度和归一至1", command=self._normalize_current_anchor
        ).pack(side="left", padx=(12, 3))
        ttk.Label(controls, textvariable=self.preview_summary_var).pack(side="right")

        self.wave_canvas = tk.Canvas(
            preview,
            height=105,
            background="#0c1420",
            highlightthickness=1,
            highlightbackground="#31445a",
        )
        self.wave_canvas.pack(fill="x")
        self.wave_canvas.bind("<Configure>", self._schedule_preview)

        ttk.Label(self, textvariable=self.status_var, padding=(8, 4)).pack(fill="x")

    def _current_anchor_index(self) -> int:
        try:
            return int(self.notebook.index("current"))
        except (tk.TclError, ValueError):
            return 0

    def _schedule_preview(self, *_args) -> None:
        if self._preview_job is not None:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(80, self._draw_waveform_preview)

    def _anchor_amplitudes(self, anchor_index: int) -> list[float] | None:
        try:
            values = [float(variable.get()) for variable in self.amp_vars[anchor_index]]
        except (ValueError, IndexError):
            return None
        if any(not math.isfinite(value) for value in values):
            return None
        return values

    def _draw_waveform_preview(self) -> None:
        self._preview_job = None
        canvas = self.wave_canvas
        canvas.delete("all")
        width = max(400, canvas.winfo_width())
        height = max(120, canvas.winfo_height())
        middle = height / 2.0

        for division in range(9):
            x = division * (width - 1) / 8.0
            canvas.create_line(x, 0, x, height, fill="#203247")
        for level in (-1.0, -0.5, 0.0, 0.5, 1.0):
            y = middle - level * (height * 0.43)
            canvas.create_line(
                0, y, width, y,
                fill="#52677d" if level == 0.0 else "#203247",
            )

        anchor_index = self._current_anchor_index()
        amplitudes = self._anchor_amplitudes(anchor_index)
        if amplitudes is None:
            self.preview_summary_var.set("幅度输入无效")
            canvas.create_text(
                width / 2, middle,
                text="请输入有效的0～1谐波幅度",
                fill="#ff8080",
            )
            return

        sample_count = max(256, min(1024, width))
        waveform = []
        for sample_index in range(sample_count):
            phase = 2.0 * math.pi * sample_index / sample_count
            waveform.append(sum(
                amplitude * math.sin((harmonic + 1) * phase)
                for harmonic, amplitude in enumerate(amplitudes)
            ))

        peak = max(abs(value) for value in waveform) if waveform else 0.0
        rms = math.sqrt(sum(value * value for value in waveform) / len(waveform)) if waveform else 0.0
        points = []
        for sample_index, value in enumerate(waveform):
            x = sample_index * (width - 1) / (sample_count - 1)
            y = middle - value * (height * 0.43)
            points.extend((x, y))
        canvas.create_line(
            *points,
            fill="#ff7070" if peak > 1.0 else "#55e6d2",
            width=2,
            smooth=False,
        )
        warning = "，可能削顶" if peak > 1.0 else ""
        self.preview_summary_var.set(
            f"{ANCHOR_NAMES[anchor_index]}  Σ幅度={sum(amplitudes):.3f}  "
            f"峰值={peak:.3f}  RMS={rms:.3f}{warning}"
        )

    def _apply_harmonic_scale(self, all_anchors: bool) -> None:
        try:
            factor = float(self.scale_factor_var.get())
        except ValueError:
            messagebox.showerror("倍率错误", "整体谐波倍率必须是数字。")
            return
        if not math.isfinite(factor) or not 0.0 <= factor <= 4.0:
            messagebox.showerror("倍率错误", "整体谐波倍率范围为0～4。")
            return

        indices = range(len(self.amp_vars)) if all_anchors else (self._current_anchor_index(),)
        clipped = False
        for anchor_index in indices:
            amplitudes = self._anchor_amplitudes(anchor_index)
            if amplitudes is None:
                messagebox.showerror("参数错误", f"{ANCHOR_NAMES[anchor_index]}存在无效幅度。")
                return
            for variable, amplitude in zip(self.amp_vars[anchor_index], amplitudes):
                scaled = amplitude * factor
                if scaled > 1.0:
                    clipped = True
                variable.set(f"{min(1.0, max(0.0, scaled)):.5f}")

        target = "全部四个基准" if all_anchors else ANCHOR_NAMES[self._current_anchor_index()]
        suffix = "；个别谐波已限制到1.0" if clipped else ""
        self.status_var.set(f"已将{target}的16个谐波同时乘以{factor:g}{suffix}。")
        self._schedule_preview()

    def _normalize_current_anchor(self) -> None:
        anchor_index = self._current_anchor_index()
        amplitudes = self._anchor_amplitudes(anchor_index)
        if amplitudes is None:
            messagebox.showerror("参数错误", "当前基准存在无效幅度。")
            return
        total = sum(amplitudes)
        if total <= 0.0:
            messagebox.showerror("无法归一化", "当前16个谐波幅度全为0。")
            return
        for variable, amplitude in zip(self.amp_vars[anchor_index], amplitudes):
            variable.set(f"{amplitude / total:.5f}")
        self.status_var.set(f"已将{ANCHOR_NAMES[anchor_index]}的16个谐波幅度和归一到1。")
        self._schedule_preview()

    def _refresh_ports(self) -> None:
        pyserial_available = True
        port_source = "pyserial"
        try:
            from serial.tools import list_ports
            ports = [port.device for port in list_ports.comports()]
        except ImportError:
            pyserial_available = False
            ports = []

        # Port discovery does not have to fail silently merely because this
        # particular Python installation lacks pyserial.  Windows/.NET can
        # still enumerate the same COM names shown by Device Manager.
        if not ports:
            port_source = "dotnet"
            try:
                result = subprocess.run(
                    [
                        "powershell", "-NoProfile", "-Command",
                        "[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                ports = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            except (OSError, subprocess.SubprocessError):
                pass

        # Some BL616/FTDI driver combinations are visible in Device Manager
        # but absent from GetPortNames/list_ports (especially after reconnect
        # or when Windows reports an Unknown PnP status).  Keep those names as
        # a final selectable fallback; opening the port still proves whether
        # it is currently present.
        if not ports:
            port_source = "pnp"
            try:
                result = subprocess.run(
                    [
                        "powershell", "-NoProfile", "-Command",
                        "Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue | "
                        "ForEach-Object { if ($_.FriendlyName -match '\\((COM[0-9]+)\\)') "
                        "{ $Matches[1] } } | Sort-Object -Unique",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                ports = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            except (OSError, subprocess.SubprocessError):
                pass
        self.port_box["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        if not pyserial_available:
            self.status_var.set(
                "已尝试列出Windows串口，但发送功能需要pyserial；"
                "请运行 tools\\install_python_requirements.bat。"
            )
        elif not ports:
            self.status_var.set("没有发现串口；也可以在串口框中直接输入COM号。")
        elif port_source == "pnp":
            self.status_var.set(
                "这些COM号来自Windows设备列表，可能包含已断开的端口；"
                "请选择板载调试器端口，或直接输入COM号。"
            )

    def _load_into_widgets(self, preset: dict) -> None:
        self.preset = deepcopy(preset)
        for key, variable in self.adsr_vars.items():
            variable.set(str(preset["adsr"][key]))
        for anchor_index, anchor in enumerate(preset["anchors"]):
            for harmonic in range(16):
                self.amp_vars[anchor_index][harmonic].set(f"{float(anchor['amplitudes'][harmonic]):.5f}")
                self.decay_vars[anchor_index][harmonic].set(str(anchor["decay_ms"][harmonic]))
        self._schedule_preview()

    def _collect(self) -> dict:
        preset = deepcopy(self.preset)
        preset["adsr"] = {key: float(variable.get()) for key, variable in self.adsr_vars.items()}
        for anchor_index, anchor in enumerate(preset["anchors"]):
            anchor["amplitudes"] = [float(value.get()) for value in self.amp_vars[anchor_index]]
            anchor["decay_ms"] = [float(value.get()) for value in self.decay_vars[anchor_index]]
        # preset_packets performs the complete range and shape validation.
        preset_packets(preset)
        return preset

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("Tone JSON", "*.json"), ("All files", "*.*")))
        if not path:
            return
        try:
            self._load_into_widgets(load_preset(path))
            self.status_var.set(f"已载入 {path}")
        except Exception as error:
            messagebox.showerror("载入失败", str(error))

    def _save_file(self) -> None:
        try:
            preset = self._collect()
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=(("Tone JSON", "*.json"),))
        if path:
            Path(path).write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_var.set(f"已保存 {path}")

    def _send_all(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("没有串口", "请选择板载调试器对应的COM口。")
            return
        try:
            preset = self._collect()
            packets = preset_packets(preset)
            count = send_packets(port, packets)
            self.preset = preset
            self.status_var.set(f"已向 {port} 发送 {count}包。请重新按下琴键试听新音色。")
        except Exception as error:
            messagebox.showerror(
                "发送失败",
                f"{error}\n\n请关闭占用同一COM口的串口助手后重试。",
            )


if __name__ == "__main__":
    HarmonicEditor().mainloop()
