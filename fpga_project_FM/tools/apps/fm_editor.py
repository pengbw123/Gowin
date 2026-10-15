"""Six-operator FM patch editor and DX7 SysEx importer."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from core.fm_patch import (  # noqa: E402
    ALGORITHM_FLAGS,
    default_patch,
    load_patch,
    load_syx,
    midi_event_packet,
    normalise_patch,
    patch_packets,
    save_patch,
    send_packets,
)


LFO_NAMES = ("Triangle", "Saw Down", "Saw Up", "Square", "Sine", "Sample/Hold")


class FmEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Primer 25K · 6算子FM音色编辑器")
        self.geometry("1180x760")
        self.minsize(980, 650)
        self.patch = default_patch()
        self.bank: list[dict] = []
        self.busy = False
        self.status_var = tk.StringVar(value="就绪：可载入DX7 .syx或手动编辑")
        self.port_var = tk.StringVar()
        self.global_vars: dict[str, tk.Variable] = {}
        self.operator_vars: list[dict[str, tk.Variable]] = []
        self._build()
        self._refresh_ports()
        self._show_patch(self.patch)

    @staticmethod
    def _spin(parent, variable, low, high, width=5):
        return ttk.Spinbox(parent, from_=low, to=high, width=width,
                           textvariable=variable, increment=1)

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="载入DX7 .syx", command=self._open_syx).pack(side="left")
        ttk.Button(toolbar, text="载入FM JSON", command=self._open_json).pack(side="left", padx=4)
        ttk.Button(toolbar, text="保存FM JSON", command=self._save_json).pack(side="left")
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(toolbar, text="串口").pack(side="left")
        self.port_box = ttk.Combobox(toolbar, textvariable=self.port_var, width=30,
                                     state="readonly")
        self.port_box.pack(side="left", padx=4)
        ttk.Button(toolbar, text="刷新", command=self._refresh_ports).pack(side="left")
        self.send_button = ttk.Button(toolbar, text="发送完整音色到FPGA",
                                      command=self._send_patch)
        self.send_button.pack(side="left", padx=(12, 4))
        ttk.Button(toolbar, text="C3试听", command=lambda: self._audition(48)).pack(side="left")
        ttk.Button(toolbar, text="C4试听", command=lambda: self._audition(60)).pack(side="left", padx=3)
        ttk.Button(toolbar, text="C5试听", command=lambda: self._audition(72)).pack(side="left")

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=(0, 5))
        bank_frame = ttk.LabelFrame(pane, text="SysEx音色库", padding=5)
        pane.add(bank_frame, weight=1)
        self.bank_list = tk.Listbox(bank_frame, width=22, exportselection=False)
        self.bank_list.pack(side="left", fill="both", expand=True)
        bank_scroll = ttk.Scrollbar(bank_frame, command=self.bank_list.yview)
        bank_scroll.pack(side="right", fill="y")
        self.bank_list.configure(yscrollcommand=bank_scroll.set)
        self.bank_list.bind("<<ListboxSelect>>", self._select_bank_patch)

        editor = ttk.Frame(pane)
        pane.add(editor, weight=5)
        self._build_global(editor)
        self._build_operators(editor)

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w",
                  padding=(5, 3)).pack(fill="x")

    def _build_global(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="全局与算法", padding=7)
        frame.pack(fill="x", pady=(0, 5))
        definitions = (
            ("name", "名称", "text", 0, 10),
            ("algorithm", "算法", "int", 1, 32),
            ("feedback", "反馈", "int", 0, 7),
            ("transpose", "移调", "int", 0, 48),
            ("lfo_speed", "LFO速度", "int", 0, 99),
            ("lfo_delay", "LFO延迟", "int", 0, 99),
            ("lfo_pitch_depth", "Pitch深度", "int", 0, 99),
            ("lfo_amp_depth", "Amp深度", "int", 0, 99),
            ("pitch_sensitivity", "Pitch灵敏度", "int", 0, 7),
        )
        for column, (key, label, kind, low, high) in enumerate(definitions):
            ttk.Label(frame, text=label).grid(row=0, column=column, padx=3, sticky="w")
            variable = tk.StringVar() if kind == "text" else tk.IntVar()
            self.global_vars[key] = variable
            if kind == "text":
                ttk.Entry(frame, textvariable=variable, width=12).grid(row=1, column=column, padx=3)
            else:
                widget = self._spin(frame, variable, low, high)
                widget.grid(row=1, column=column, padx=3)
                if key == "algorithm":
                    widget.configure(command=self._update_algorithm_text)
        self.global_vars["osc_sync"] = tk.BooleanVar()
        ttk.Checkbutton(frame, text="按键相位同步", variable=self.global_vars["osc_sync"]).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.global_vars["lfo_sync"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="LFO按键同步", variable=self.global_vars["lfo_sync"]).grid(
            row=2, column=5, columnspan=2, sticky="w", pady=(5, 0))
        self.global_vars["lfo_waveform"] = tk.IntVar()
        ttk.Label(frame, text="LFO波形").grid(row=2, column=2, sticky="e")
        ttk.Combobox(frame, width=12, state="readonly", values=LFO_NAMES,
                     textvariable=tk.StringVar(value=LFO_NAMES[4])).grid(row=2, column=3, padx=3)
        # The waveform box is managed explicitly because its visible value is text.
        self.lfo_box = frame.grid_slaves(row=2, column=3)[0]
        self.algorithm_text = ttk.Label(frame, foreground="#315a8b")
        self.algorithm_text.grid(row=2, column=4, columnspan=5, sticky="w", padx=8)

        peg = ttk.Frame(frame)
        peg.grid(row=3, column=0, columnspan=9, sticky="w", pady=(6, 0))
        ttk.Label(peg, text="Pitch EG  R1..R4").pack(side="left")
        self.pitch_rate_vars = [tk.IntVar() for _ in range(4)]
        self.pitch_level_vars = [tk.IntVar() for _ in range(4)]
        for var in self.pitch_rate_vars:
            self._spin(peg, var, 0, 99).pack(side="left", padx=2)
        ttk.Label(peg, text="  L1..L4").pack(side="left")
        for var in self.pitch_level_vars:
            self._spin(peg, var, 0, 99).pack(side="left", padx=2)

    def _build_operators(self, parent) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        for display_number in range(1, 7):
            index = 6 - display_number  # patch order OP6..OP1
            frame = ttk.Frame(notebook, padding=10)
            notebook.add(frame, text=f"OP{display_number}")
            variables: dict[str, tk.Variable] = {
                "enabled": tk.BooleanVar(), "mode": tk.StringVar(),
                "coarse": tk.IntVar(), "fine": tk.IntVar(), "detune": tk.IntVar(),
                "output_level": tk.IntVar(), "rate_scaling": tk.IntVar(),
                "velocity_sensitivity": tk.IntVar(), "amp_mod_sensitivity": tk.IntVar(),
                "breakpoint": tk.IntVar(), "left_depth": tk.IntVar(),
                "right_depth": tk.IntVar(), "left_curve": tk.IntVar(),
                "right_curve": tk.IntVar(),
            }
            while len(self.operator_vars) <= index:
                self.operator_vars.append({})
            self.operator_vars[index] = variables
            ttk.Checkbutton(frame, text="启用算子", variable=variables["enabled"]).grid(row=0, column=0, sticky="w")
            ttk.Label(frame, text="频率模式").grid(row=0, column=1)
            ttk.Combobox(frame, values=("ratio", "fixed"), state="readonly", width=8,
                         textvariable=variables["mode"]).grid(row=0, column=2)
            items = (
                ("coarse", "Coarse", 0, 31), ("fine", "Fine", 0, 99),
                ("detune", "Detune", 0, 14), ("output_level", "输出电平", 0, 99),
                ("rate_scaling", "Rate Scaling", 0, 7),
                ("velocity_sensitivity", "力度灵敏度", 0, 7),
                ("amp_mod_sensitivity", "AM灵敏度", 0, 3),
            )
            for item_index, (key, label, low, high) in enumerate(items):
                row, col = 1 + item_index // 4, (item_index % 4) * 2
                ttk.Label(frame, text=label).grid(row=row, column=col, sticky="e", padx=(6, 2), pady=4)
                self._spin(frame, variables[key], low, high).grid(row=row, column=col + 1, sticky="w")
            ttk.Separator(frame).grid(row=3, column=0, columnspan=8, sticky="ew", pady=8)
            ttk.Label(frame, text="包络 Rate 1..4").grid(row=4, column=0, sticky="w")
            variables["eg_rates"] = [tk.IntVar() for _ in range(4)]
            variables["eg_levels"] = [tk.IntVar() for _ in range(4)]
            for stage, var in enumerate(variables["eg_rates"]):
                self._spin(frame, var, 0, 99).grid(row=4, column=stage + 1, padx=3)
            ttk.Label(frame, text="包络 Level 1..4").grid(row=5, column=0, sticky="w")
            for stage, var in enumerate(variables["eg_levels"]):
                self._spin(frame, var, 0, 99).grid(row=5, column=stage + 1, padx=3)
            ttk.Separator(frame).grid(row=6, column=0, columnspan=8, sticky="ew", pady=8)
            scale_items = (
                ("breakpoint", "键位分界点", 0, 99),
                ("left_depth", "左深度", 0, 99), ("right_depth", "右深度", 0, 99),
                ("left_curve", "左曲线", 0, 3), ("right_curve", "右曲线", 0, 3),
            )
            for item_index, (key, label, low, high) in enumerate(scale_items):
                row, col = 7 + item_index // 3, (item_index % 3) * 2
                ttk.Label(frame, text=label).grid(row=row, column=col, sticky="e", padx=3, pady=4)
                self._spin(frame, variables[key], low, high).grid(row=row, column=col + 1)
            ttk.Label(frame, text="说明：Ratio模式的Coarse/Fine控制频率倍数；Fixed模式控制固定Hz。\n"
                                  "包络为DX7四段Rate/Level，数值0~99。OP页签按人类习惯显示，内部仍按OP6→OP1计算。",
                      foreground="#555").grid(row=10, column=0, columnspan=8, sticky="w", pady=12)

    def _collect_patch(self) -> dict:
        lfo_name = self.lfo_box.get()
        raw = {
            "name": self.global_vars["name"].get(),
            "algorithm": self.global_vars["algorithm"].get(),
            "feedback": self.global_vars["feedback"].get(),
            "osc_sync": self.global_vars["osc_sync"].get(),
            "transpose": self.global_vars["transpose"].get(),
            "lfo": {
                "speed": self.global_vars["lfo_speed"].get(),
                "delay": self.global_vars["lfo_delay"].get(),
                "pitch_depth": self.global_vars["lfo_pitch_depth"].get(),
                "amp_depth": self.global_vars["lfo_amp_depth"].get(),
                "waveform": LFO_NAMES.index(lfo_name) if lfo_name in LFO_NAMES else 4,
                "pitch_sensitivity": self.global_vars["pitch_sensitivity"].get(),
                "sync": self.global_vars["lfo_sync"].get(),
            },
            "pitch_eg_rates": [var.get() for var in self.pitch_rate_vars],
            "pitch_eg_levels": [var.get() for var in self.pitch_level_vars],
            "operators": [],
        }
        for index, variables in enumerate(self.operator_vars):
            op = {key: var.get() for key, var in variables.items()
                  if key not in ("eg_rates", "eg_levels")}
            op["number"] = 6 - index
            op["eg_rates"] = [var.get() for var in variables["eg_rates"]]
            op["eg_levels"] = [var.get() for var in variables["eg_levels"]]
            raw["operators"].append(op)
        return normalise_patch(raw)

    def _show_patch(self, patch: dict) -> None:
        self.patch = normalise_patch(patch)
        values = self.patch
        for key in ("name", "algorithm", "feedback", "osc_sync", "transpose"):
            self.global_vars[key].set(values[key])
        for key in ("speed", "delay", "pitch_depth", "amp_depth"):
            self.global_vars[f"lfo_{key}"].set(values["lfo"][key])
        self.global_vars["pitch_sensitivity"].set(values["lfo"]["pitch_sensitivity"])
        self.global_vars["lfo_sync"].set(values["lfo"].get("sync", True))
        self.lfo_box.set(LFO_NAMES[min(5, values["lfo"]["waveform"])])
        for variable, value in zip(self.pitch_rate_vars, values["pitch_eg_rates"]):
            variable.set(value)
        for variable, value in zip(self.pitch_level_vars, values["pitch_eg_levels"]):
            variable.set(value)
        for variables, op in zip(self.operator_vars, values["operators"]):
            for key, variable in variables.items():
                if key in ("eg_rates", "eg_levels"):
                    for item, value in zip(variable, op[key]):
                        item.set(value)
                else:
                    variable.set(op[key])
        self._update_algorithm_text()

    def _update_algorithm_text(self) -> None:
        try:
            number = max(1, min(32, int(self.global_vars["algorithm"].get())))
        except (ValueError, tk.TclError):
            return
        flags = ALGORITHM_FLAGS[number - 1]
        carriers = [str(6 - index) for index, flag in enumerate(flags) if (flag & 3) == 0]
        self.algorithm_text.configure(text=f"算法{number} · 载波 OP{'/'.join(carriers)}")

    def _open_syx(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("DX7 SysEx", "*.syx"), ("所有文件", "*.*")))
        if not path:
            return
        try:
            self.bank = load_syx(path)
        except Exception as error:
            messagebox.showerror("载入失败", str(error))
            return
        self.bank_list.delete(0, "end")
        for index, patch in enumerate(self.bank, 1):
            self.bank_list.insert("end", f"{index:02d}  {patch['name']}")
        self.bank_list.selection_set(0)
        self._show_patch(self.bank[0])
        self.status_var.set(f"已载入 {Path(path).name}：{len(self.bank)} 个音色")

    def _select_bank_patch(self, _event=None) -> None:
        selection = self.bank_list.curselection()
        if selection and selection[0] < len(self.bank):
            self._show_patch(self.bank[selection[0]])

    def _open_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("FM JSON", "*.json"),))
        if path:
            try:
                self._show_patch(load_patch(path))
                self.status_var.set(f"已载入 {Path(path).name}")
            except Exception as error:
                messagebox.showerror("载入失败", str(error))

    def _save_json(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=(("FM JSON", "*.json"),))
        if path:
            try:
                save_patch(path, self._collect_patch())
                self.status_var.set(f"已保存 {Path(path).name}")
            except Exception as error:
                messagebox.showerror("保存失败", str(error))

    def _refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
            ports = [port for port in list_ports.comports()
                     if "Bluetooth" not in (port.description or "")]
            values = [f"{port.device}  {port.description}" for port in ports]
        except ImportError:
            values = []
        self.port_box["values"] = values
        if values and self.port_var.get() not in values:
            self.port_var.set(values[0])

    def _port_name(self) -> str:
        value = self.port_var.get().strip()
        if not value:
            raise RuntimeError("请先选择串口")
        return value.split()[0]

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")

    def _send_patch(self) -> None:
        if self.busy:
            return
        try:
            port = self._port_name()
            patch = self._collect_patch()
            packets = patch_packets(patch)
        except Exception as error:
            messagebox.showerror("参数错误", str(error))
            return
        self._set_busy(True)

        def progress(done, total):
            self.after(0, lambda: self.status_var.set(f"正在下载音色 {done}/{total}"))

        def worker():
            try:
                count = send_packets(port, packets, progress=progress)
                self.after(0, lambda: self.status_var.set(
                    f"音色已生效：{patch['name']}，共{count}包；请弹琴试听"))
            except Exception as error:
                self.after(0, lambda: messagebox.showerror("发送失败", str(error)))
                self.after(0, lambda: self.status_var.set("发送失败；检查COM口是否被串口助手占用"))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _audition(self, note: int) -> None:
        if self.busy:
            return
        try:
            port = self._port_name()
        except Exception as error:
            messagebox.showerror("试听失败", str(error))
            return

        def worker():
            try:
                send_packets(port, (midi_event_packet(0x90, note, 105),))
                time.sleep(0.7)
                send_packets(port, (midi_event_packet(0x80, note, 0),))
            except Exception as error:
                self.after(0, lambda: messagebox.showerror("试听失败", str(error)))

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    FmEditor().mainloop()
