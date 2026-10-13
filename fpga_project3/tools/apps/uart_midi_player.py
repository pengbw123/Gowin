"""Graphical Standard-MIDI-file player for the Primer 25K UART synth."""

from __future__ import annotations

import bisect
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.additive_uart_protocol import DEFAULT_PERFORMANCE, midi_event_packet, strum_packet
from core.midi_player_core import MidiSong, TimedMidiEvent, load_midi, note_name, transpose_event


BAUD = 115200
LIBRARY_DIR = Path(__file__).resolve().parents[1] / "midi_library"
UART_FRAME_SECONDS = 12 * 10 / BAUD


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def is_usb_serial_candidate(port_info) -> bool:
    """Reject Bluetooth/legacy virtual COM ports before they reach the UI."""
    hwid = str(getattr(port_info, "hwid", "") or "").upper()
    if "BTHENUM" in hwid or "BLUETOOTH" in hwid:
        return False
    return getattr(port_info, "vid", None) is not None or hwid.startswith("USB")


def port_display_name(port_info) -> str:
    description = str(getattr(port_info, "description", "") or "USB串口")
    vid = getattr(port_info, "vid", None)
    pid = getattr(port_info, "pid", None)
    identity = f"VID:PID={vid:04X}:{pid:04X}" if vid is not None and pid is not None else "USB"
    return f"{port_info.device}  |  {description}  |  {identity}"


class SerialMidiLink:
    def __init__(self, log_queue: queue.Queue[str]) -> None:
        self.port = None
        self.lock = threading.Lock()
        self.log_queue = log_queue
        self.next_write_time = 0.0

    @property
    def connected(self) -> bool:
        return self.port is not None and self.port.is_open

    def open(self, name: str) -> None:
        try:
            import serial
        except ImportError as error:
            raise RuntimeError("缺少 pyserial，请先运行依赖安装脚本") from error
        with self.lock:
            if self.port is not None and self.port.is_open:
                raise RuntimeError("串口已经连接，禁止重复打开")
            if self.port is not None:
                try:
                    self.port.close()
                except Exception:
                    pass
                self.port = None
            # Configure the port while it is still closed.  In particular,
            # never pulse DTR/RTS on open: some BL616/USB bridge firmware uses
            # those lines for reset/boot control, and fragile virtual-port
            # drivers may also fail when handles are opened/closed rapidly.
            port = serial.Serial(
                port=None,
                baudrate=BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            port.dtr = False
            port.rts = False
            port.port = name
            port.open()
            self.port = port
            self.next_write_time = time.perf_counter()

    def close(self) -> None:
        with self.lock:
            port, self.port = self.port, None
            self.next_write_time = 0.0
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass

    def send_packet(self, packet: bytes) -> None:
        """Write exactly one paced packet while owning the only port handle."""
        if len(packet) != 12:
            raise ValueError("FPGA UART packet must contain 12 bytes")
        with self.lock:
            port = self.port
            if port is None or not port.is_open:
                raise RuntimeError("串口尚未连接")
            now = time.perf_counter()
            if now < self.next_write_time:
                time.sleep(self.next_write_time - now)
            written = port.write(packet)
            if written != len(packet):
                raise RuntimeError(f"串口只发送了 {written}/{len(packet)} 字节")
            self.next_write_time = max(self.next_write_time, time.perf_counter()) + UART_FRAME_SECONDS

    def send(self, status: int, data1: int, data2: int) -> None:
        self.send_packet(midi_event_packet(status, data1, data2))

    def all_notes_off(self, channels: tuple[int, ...] = tuple(range(16))) -> None:
        if not self.connected:
            return
        # Sustain-off first, then All Notes Off.  Limiting this to channels
        # used by the song avoids sending 32 packets after every pause.
        targets = channels or (0,)
        for channel in targets:
            self.send(0xB0 | channel, 64, 0)
            self.send(0xB0 | channel, 123, 0)


class MidiPlayback:
    def __init__(self, link: SerialMidiLink, ui_queue: queue.Queue[tuple]) -> None:
        self.link = link
        self.ui_queue = ui_queue
        self.song: MidiSong | None = None
        self.lock = threading.Lock()
        self.running = False
        self.paused = False
        self.playhead = 0.0
        self.event_index = 0
        self.started_at = 0.0
        self.tempo_scale = 1.0
        self.transpose = 0
        self.velocity_percent = 100
        self.ignore_drums = True
        self.thread: threading.Thread | None = None
        self.generation = 0
        self.thread_generation = -1

    def set_song(self, song: MidiSong) -> None:
        self.stop(send_silence=True)
        with self.lock:
            self.song = song
            self.playhead = 0.0
            self.event_index = 0
        self.ui_queue.put(("position", 0.0))
        self.ui_queue.put(("clear_notes",))

    def _current_position_locked(self) -> float:
        if self.running and not self.paused:
            return self.playhead + (time.perf_counter() - self.started_at) * self.tempo_scale
        return self.playhead

    def _silence_safely(self, channels: tuple[int, ...]) -> bool:
        try:
            self.link.all_notes_off(channels)
            return True
        except Exception as error:
            self.link.close()
            self.ui_queue.put(("error", f"串口停止发声失败，连接已关闭：{error}"))
            return False

    def play(self) -> None:
        if not self.song:
            raise RuntimeError("请先选择 MIDI 文件")
        if not self.link.connected:
            raise RuntimeError("请先连接串口")
        with self.lock:
            if self.running and not self.paused:
                return
            if self.playhead >= self.song.duration_s:
                self.playhead = 0.0
                self.event_index = 0
            self.paused = False
            self.running = True
            self.started_at = time.perf_counter()
            if (self.thread is None or not self.thread.is_alive()
                    or self.thread_generation != self.generation):
                self.thread_generation = self.generation
                self.thread = threading.Thread(
                    target=self._worker, args=(self.generation,), daemon=True
                )
                self.thread.start()
        self.ui_queue.put(("state", "播放中"))

    def pause(self) -> None:
        with self.lock:
            if not self.running or self.paused:
                return
            self.playhead = self._current_position_locked()
            self.paused = True
            channels = self.song.channels if self.song else (0,)
        self._silence_safely(channels)
        self.ui_queue.put(("clear_notes",))
        self.ui_queue.put(("state", "已暂停"))

    def stop(self, send_silence: bool = True) -> None:
        with self.lock:
            song = self.song
            self.running = False
            self.paused = False
            self.generation += 1
            self.playhead = 0.0
            self.event_index = 0
        if send_silence and song:
            self._silence_safely(song.channels)
        self.ui_queue.put(("position", 0.0))
        self.ui_queue.put(("clear_notes",))
        self.ui_queue.put(("state", "已停止"))

    def seek(self, seconds: float) -> None:
        with self.lock:
            if not self.song:
                return
            was_playing = self.running and not self.paused
            channels = self.song.channels
            times = [event.time_s for event in self.song.events]
            self.playhead = max(0.0, min(float(seconds), self.song.duration_s))
            self.event_index = bisect.bisect_left(times, self.playhead)
            if was_playing:
                self.started_at = time.perf_counter()
        self._silence_safely(channels)
        self.ui_queue.put(("clear_notes",))
        self.ui_queue.put(("position", self.playhead))

    def update_tempo(self, value: float) -> None:
        with self.lock:
            current = self._current_position_locked()
            self.playhead = current
            self.started_at = time.perf_counter()
            self.tempo_scale = float(value)

    def _worker(self, generation: int) -> None:
        last_ui = 0.0
        while True:
            with self.lock:
                if not self.running or generation != self.generation:
                    return
                paused = self.paused
                if paused:
                    position = self.playhead
                    event = None
                else:
                    position = self._current_position_locked()
                    event = None
                    if self.song and self.event_index < len(self.song.events):
                        candidate = self.song.events[self.event_index]
                        if candidate.time_s <= position:
                            event = candidate
                            self.event_index += 1
                    song_finished = bool(
                        self.song
                        and position >= self.song.duration_s
                        and self.event_index >= len(self.song.events)
                    )
                    transpose = self.transpose
                    velocity_percent = self.velocity_percent
                    ignore_drums = self.ignore_drums
                    channels = self.song.channels if self.song else (0,)

            if paused:
                time.sleep(0.01)
                continue
            if song_finished:
                self._silence_safely(channels)
                with self.lock:
                    self.running = False
                    self.playhead = self.song.duration_s if self.song else 0.0
                self.ui_queue.put(("clear_notes",))
                self.ui_queue.put(("position", self.playhead))
                self.ui_queue.put(("state", "播放完成"))
                return
            if event is not None:
                if not (ignore_drums and event.channel == 9):
                    adjusted = transpose_event(event, transpose, velocity_percent)
                    if adjusted is not None:
                        try:
                            self.link.send(adjusted.status, adjusted.data1, adjusted.data2)
                            if adjusted.is_note_on:
                                self.ui_queue.put(("note_on", adjusted.data1))
                            elif adjusted.is_note_off:
                                self.ui_queue.put(("note_off", adjusted.data1))
                        except Exception as error:
                            self.link.close()
                            self.ui_queue.put(("error", f"串口发送失败：{error}"))
                            with self.lock:
                                self.running = False
                            return
                continue
            now = time.perf_counter()
            if now - last_ui >= 0.03:
                self.ui_queue.put(("position", position))
                last_ui = now
            time.sleep(0.001)


class PianoCanvas(tk.Canvas):
    BLACK_PCS = {1, 3, 6, 8, 10}

    def __init__(self, master, on_press, on_release, **kwargs) -> None:
        super().__init__(master, height=180, background="#10141c", highlightthickness=0, **kwargs)
        self.on_press = on_press
        self.on_release = on_release
        self.low = 21
        self.high = 108
        self.active: set[int] = set()
        self.mouse_note: int | None = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.tag_bind("piano_key", "<ButtonPress-1>", self._mouse_down)
        self.bind("<ButtonRelease-1>", self._mouse_up)

    def set_range(self, low: int, high: int) -> None:
        self.low = max(0, min(127, low))
        self.high = max(self.low, min(127, high))
        self.redraw()

    def set_active(self, notes: set[int]) -> None:
        self.active = set(notes)
        self.redraw()

    def _mouse_down(self, _event) -> None:
        current = self.find_withtag("current")
        if not current:
            return
        tags = self.gettags(current[0])
        note_tag = next((tag for tag in tags if tag.startswith("note_")), None)
        if note_tag:
            self.mouse_note = int(note_tag.split("_", 1)[1])
            self.on_press(self.mouse_note)

    def _mouse_up(self, _event) -> None:
        if self.mouse_note is not None:
            self.on_release(self.mouse_note)
            self.mouse_note = None

    def redraw(self) -> None:
        self.delete("all")
        width = max(200, self.winfo_width())
        height = max(120, self.winfo_height())
        whites = [n for n in range(self.low, self.high + 1) if n % 12 not in self.BLACK_PCS]
        if not whites:
            return
        white_w = width / len(whites)
        white_x: dict[int, float] = {}
        for index, note in enumerate(whites):
            x0 = index * white_w
            x1 = (index + 1) * white_w
            white_x[note] = x0
            fill = "#57d3ff" if note in self.active else "#f4f6f8"
            self.create_rectangle(x0, 0, x1, height, fill=fill, outline="#202631",
                                  tags=("piano_key", f"note_{note}"))
            if note % 12 == 0 and white_w >= 10:
                self.create_text((x0 + x1) / 2, height - 13, text=note_name(note),
                                 fill="#303640", font=("Segoe UI", 8))
        for note in range(self.low, self.high + 1):
            if note % 12 not in self.BLACK_PCS:
                continue
            previous_white = next((n for n in range(note - 1, self.low - 1, -1)
                                   if n % 12 not in self.BLACK_PCS), None)
            if previous_white is None or previous_white not in white_x:
                continue
            center = white_x[previous_white] + white_w
            black_w = white_w * 0.62
            fill = "#ffb44a" if note in self.active else "#161b24"
            self.create_rectangle(center - black_w / 2, 0, center + black_w / 2,
                                  height * 0.62, fill=fill, outline="#090b0f",
                                  tags=("piano_key", f"note_{note}"))


class MidiPlayerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FPGA 全音域 MIDI 演奏器")
        self.geometry("1120x760")
        self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.link = SerialMidiLink(self.log_queue)
        self.playback = MidiPlayback(self.link, self.ui_queue)
        self.song: MidiSong | None = None
        self.active_counts: dict[int, int] = {}
        self.dragging_progress = False
        self.library_paths: list[Path] = []
        self.port_choices: dict[str, str] = {}
        self._build_ui()
        self._refresh_ports()
        self._refresh_library()
        self.after(30, self._poll_queues)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        connection = ttk.LabelFrame(root, text="1. 连接 Primer 25K（115200 8-N-1）", padding=8)
        connection.pack(fill="x")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(connection, textvariable=self.port_var, width=52, state="readonly")
        self.port_combo.pack(side="left")
        ttk.Button(connection, text="刷新", command=self._refresh_ports).pack(side="left", padx=5)
        self.connect_button = ttk.Button(connection, text="连接", command=self._toggle_connection)
        self.connect_button.pack(side="left")
        self.connection_var = tk.StringVar(value="未连接")
        ttk.Label(connection, textvariable=self.connection_var).pack(side="left", padx=12)
        ttk.Label(connection, text="实体键盘 MODE 旋钮仍实时控制板端总音量",
                  foreground="#146c43").pack(side="right")

        middle = ttk.Frame(root)
        middle.pack(fill="both", expand=True, pady=(10, 0))
        library_frame = ttk.LabelFrame(middle, text="2. 曲库 / MIDI 文件", padding=8)
        library_frame.pack(side="left", fill="y")
        self.library_list = tk.Listbox(library_frame, width=30, height=13, exportselection=False)
        self.library_list.pack(fill="both", expand=True)
        self.library_list.bind("<Double-Button-1>", lambda _e: self._load_selected_library())
        ttk.Button(library_frame, text="载入所选曲目", command=self._load_selected_library).pack(fill="x", pady=(6, 2))
        ttk.Button(library_frame, text="打开任意 .mid/.midi", command=self._open_midi).pack(fill="x")
        ttk.Button(library_frame, text="刷新曲库", command=self._refresh_library).pack(fill="x", pady=(2, 0))
        ttk.Button(library_frame, text="打开曲库目录", command=self._open_library_folder).pack(fill="x", pady=(2, 0))

        controls = ttk.LabelFrame(middle, text="3. 播放控制", padding=10)
        controls.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.song_title_var = tk.StringVar(value="尚未载入曲目")
        ttk.Label(controls, textvariable=self.song_title_var, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self.song_info_var = tk.StringVar(value="支持 MIDI 0–127 全音域；当前合成器最多同时保留 8 个声部")
        ttk.Label(controls, textvariable=self.song_info_var).pack(anchor="w", pady=(2, 10))

        buttons = ttk.Frame(controls)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="▶ 播放/继续", command=self._play).pack(side="left")
        ttk.Button(buttons, text="Ⅱ 暂停", command=self.playback.pause).pack(side="left", padx=5)
        ttk.Button(buttons, text="■ 停止", command=self.playback.stop).pack(side="left")
        self.state_var = tk.StringVar(value="已停止")
        ttk.Label(buttons, textvariable=self.state_var).pack(side="right")

        options = ttk.Frame(controls)
        options.pack(fill="x", pady=12)
        ttk.Label(options, text="速度").grid(row=0, column=0, sticky="w")
        self.tempo_var = tk.DoubleVar(value=1.0)
        tempo = ttk.Scale(options, from_=0.5, to=1.5, variable=self.tempo_var,
                          command=lambda value: self._tempo_changed(float(value)))
        tempo.grid(row=0, column=1, sticky="ew", padx=5)
        self.tempo_label = ttk.Label(options, text="1.00×", width=7)
        self.tempo_label.grid(row=0, column=2)
        ttk.Label(options, text="移调").grid(row=1, column=0, sticky="w")
        self.transpose_var = tk.IntVar(value=0)
        transpose = ttk.Spinbox(options, from_=-24, to=24, textvariable=self.transpose_var,
                                width=7, command=self._options_changed)
        transpose.grid(row=1, column=1, sticky="w", padx=5, pady=4)
        ttk.Label(options, text="半音（不限制在实体25键音区）").grid(row=1, column=2, sticky="w")
        ttk.Label(options, text="力度").grid(row=2, column=0, sticky="w")
        self.velocity_var = tk.IntVar(value=100)
        velocity = ttk.Spinbox(options, from_=20, to=150, increment=5,
                               textvariable=self.velocity_var, width=7,
                               command=self._options_changed)
        velocity.grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(options, text="%（与 MODE 总音量相互独立）").grid(row=2, column=2, sticky="w")
        self.ignore_drums_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="忽略 MIDI 第10通道鼓组", variable=self.ignore_drums_var,
                        command=self._options_changed).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        options.columnconfigure(1, weight=1)

        progress_line = ttk.Frame(controls)
        progress_line.pack(fill="x")
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(progress_line, textvariable=self.time_var, width=15).pack(side="left")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress = ttk.Scale(progress_line, from_=0, to=1, variable=self.progress_var)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress.bind("<ButtonPress-1>", lambda _e: setattr(self, "dragging_progress", True))
        self.progress.bind("<ButtonRelease-1>", self._seek_from_scale)

        piano_frame = ttk.LabelFrame(root, text="全音域可视琴键（点击也可试音）", padding=6)
        piano_frame.pack(fill="x", pady=(10, 0))
        self.piano = PianoCanvas(piano_frame, self._manual_note_on, self._manual_note_off)
        self.piano.pack(fill="x")

        log_frame = ttk.LabelFrame(root, text="软件状态（为稳定起见不在后台持续读取串口）", padding=5)
        log_frame.pack(fill="both", expand=False, pady=(8, 0))
        self.log = tk.Text(log_frame, height=5, wrap="word", state="disabled",
                           background="#111820", foreground="#bce3c5", insertbackground="white")
        self.log.pack(fill="both", expand=True)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 120:
            self.log.delete("1.0", f"{lines - 100}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
            ports = [port for port in list_ports.comports() if is_usb_serial_candidate(port)]
        except ImportError:
            ports = []
        previous_device = self.port_choices.get(self.port_var.get(), "")
        self.port_choices = {port_display_name(port): port.device for port in ports}
        self.port_combo["values"] = list(self.port_choices)
        matching = next(
            (display for display, device in self.port_choices.items()
             if device == previous_device), ""
        )
        # Never silently select the first system COM port.  The user must
        # explicitly choose one of the USB ports belonging to the board.
        self.port_var.set(matching)
        if not ports:
            self.connection_var.set("未发现USB串口（蓝牙COM口已安全过滤）")

    def _toggle_connection(self) -> None:
        self.connect_button.configure(state="disabled")
        if self.link.connected:
            try:
                self.playback.stop()
                self.link.close()
                self.connection_var.set("未连接")
                self.connect_button.configure(text="连接")
            finally:
                self.connect_button.configure(state="normal")
            return
        display_name = self.port_var.get()
        port_name = self.port_choices.get(display_name, "")
        if not port_name:
            messagebox.showwarning("串口", "没有选择串口")
            self.connect_button.configure(state="normal")
            return
        try:
            self.link.open(port_name)
            normal_performance = dict(DEFAULT_PERFORMANCE)
            normal_performance["mode"] = "normal"
            self.link.send_packet(strum_packet(normal_performance))
            self.link.all_notes_off((0,))
        except Exception as error:
            self.link.close()
            messagebox.showerror("连接失败", str(error))
            self.connect_button.configure(state="normal")
            return
        self.connection_var.set(f"已连接 {port_name}；普通演奏模式")
        self.connect_button.configure(text="断开")
        self.connect_button.configure(state="normal")
        self._append_log(
            f"安全打开 {port_name} @ {BAUD}：DTR/RTS关闭，后台读取关闭，已恢复普通演奏模式"
        )

    def _refresh_library(self) -> None:
        LIBRARY_DIR.mkdir(exist_ok=True)
        self.library_paths = sorted(
            [*LIBRARY_DIR.rglob("*.mid"), *LIBRARY_DIR.rglob("*.midi")],
            key=lambda path: path.name.lower(),
        )
        self.library_list.delete(0, "end")
        for path in self.library_paths:
            self.library_list.insert("end", path.stem)
        if self.library_paths:
            self.library_list.selection_set(0)

    def _load_selected_library(self) -> None:
        selected = self.library_list.curselection()
        if selected:
            self._load_path(self.library_paths[selected[0]])

    def _open_midi(self) -> None:
        path = filedialog.askopenfilename(
            title="选择标准 MIDI 文件",
            filetypes=(("MIDI 文件", "*.mid *.midi"), ("所有文件", "*.*")),
        )
        if path:
            self._load_path(Path(path))

    def _open_library_folder(self) -> None:
        import os
        os.startfile(LIBRARY_DIR)

    def _load_path(self, path: Path) -> None:
        try:
            song = load_midi(path)
        except Exception as error:
            messagebox.showerror("MIDI 载入失败", str(error))
            return
        self.song = song
        self.playback.set_song(song)
        self.song_title_var.set(song.title)
        range_text = f"{note_name(song.min_note)}–{note_name(song.max_note)} / MIDI {song.min_note}–{song.max_note}"
        warning = "；超过板端8复音时会按现有策略抢占声部" if song.peak_polyphony > 8 else ""
        self.song_info_var.set(
            f"时长 {format_time(song.duration_s)}　音域 {range_text}　峰值复音 {song.peak_polyphony}{warning}"
        )
        self.progress.configure(to=max(0.001, song.duration_s))
        low = max(0, song.min_note - 2)
        high = min(127, song.max_note + 2)
        # Keep at least roughly two octaves visible for tiny test files.
        if high - low < 24:
            center = (high + low) // 2
            low, high = max(0, center - 12), min(127, center + 12)
        self.piano.set_range(low, high)
        self.time_var.set(f"00:00 / {format_time(song.duration_s)}")
        self._append_log(f"载入：{path.name}，{len(song.events)} 个事件，音域 {range_text}")

    def _play(self) -> None:
        self._options_changed()
        try:
            self.playback.play()
        except Exception as error:
            messagebox.showwarning("无法播放", str(error))

    def _tempo_changed(self, value: float) -> None:
        self.tempo_label.configure(text=f"{value:.2f}×")
        self.playback.update_tempo(value)

    def _options_changed(self) -> None:
        try:
            self.playback.transpose = max(-24, min(24, int(self.transpose_var.get())))
            self.playback.velocity_percent = max(20, min(150, int(self.velocity_var.get())))
        except (ValueError, tk.TclError):
            return
        self.playback.ignore_drums = bool(self.ignore_drums_var.get())

    def _seek_from_scale(self, _event) -> None:
        self.dragging_progress = False
        self.playback.seek(self.progress_var.get())

    def _manual_note_on(self, note: int) -> None:
        if not self.link.connected:
            return
        try:
            self.link.send(0x90, note, 100)
            self.active_counts[note] = self.active_counts.get(note, 0) + 1
            self.piano.set_active(set(self.active_counts))
        except Exception as error:
            self._append_log(str(error))

    def _manual_note_off(self, note: int) -> None:
        if self.link.connected:
            try:
                self.link.send(0x80, note, 0)
            except Exception as error:
                self._append_log(str(error))
        self.active_counts.pop(note, None)
        self.piano.set_active(set(self.active_counts))

    def _poll_queues(self) -> None:
        while True:
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "position":
                position = item[1]
                if not self.dragging_progress:
                    self.progress_var.set(position)
                duration = self.song.duration_s if self.song else 0
                self.time_var.set(f"{format_time(position)} / {format_time(duration)}")
            elif kind == "note_on":
                note = item[1]
                self.active_counts[note] = self.active_counts.get(note, 0) + 1
                self.piano.set_active(set(self.active_counts))
            elif kind == "note_off":
                note = item[1]
                count = self.active_counts.get(note, 0) - 1
                if count > 0:
                    self.active_counts[note] = count
                else:
                    self.active_counts.pop(note, None)
                self.piano.set_active(set(self.active_counts))
            elif kind == "clear_notes":
                self.active_counts.clear()
                self.piano.set_active(set())
            elif kind == "state":
                self.state_var.set(item[1])
            elif kind == "error":
                self._append_log(item[1])
                self.connection_var.set("串口已安全关闭，请检查连接后手动重连")
                self.connect_button.configure(text="连接", state="normal")
                messagebox.showerror("播放错误", item[1])
        while True:
            try:
                self._append_log(self.log_queue.get_nowait())
            except queue.Empty:
                break
        self.after(30, self._poll_queues)

    def _on_close(self) -> None:
        self.playback.stop()
        self.link.close()
        self.destroy()


if __name__ == "__main__":
    MidiPlayerApp().mainloop()

