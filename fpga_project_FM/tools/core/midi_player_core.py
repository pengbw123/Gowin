"""MIDI-file parsing and scheduling data for the UART piano player."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TimedMidiEvent:
    time_s: float
    status: int
    data1: int
    data2: int

    @property
    def channel(self) -> int:
        return self.status & 0x0F

    @property
    def kind(self) -> int:
        return self.status & 0xF0

    @property
    def is_note_on(self) -> bool:
        return self.kind == 0x90 and self.data2 > 0

    @property
    def is_note_off(self) -> bool:
        return self.kind == 0x80 or (self.kind == 0x90 and self.data2 == 0)


@dataclass(frozen=True)
class MidiSong:
    path: Path
    title: str
    events: tuple[TimedMidiEvent, ...]
    duration_s: float
    min_note: int
    max_note: int
    peak_polyphony: int
    channels: tuple[int, ...]


def note_name(note: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    note = max(0, min(127, int(note)))
    return f"{names[note % 12]}{note // 12 - 1}"


def load_midi(path: str | Path) -> MidiSong:
    """Load a Standard MIDI File and convert tick deltas to absolute seconds.

    Only messages understood by the FPGA are retained: notes and sustain.
    Tempo changes are applied while building the absolute timeline.  All MIDI
    channels and all 128 note numbers remain available; the 25-key hardware
    keyboard's current octave never limits file playback.
    """
    try:
        import mido
    except ImportError as error:
        raise RuntimeError("缺少 mido：请先运行 install_python_requirements.bat") from error

    midi_path = Path(path)
    midi_file = mido.MidiFile(midi_path)
    tempo = 500_000
    elapsed = 0.0
    events: list[TimedMidiEvent] = []
    # File names are more reliable than the first track name.  Many exported
    # files call their tempo/meta track simply "control track".
    title = midi_path.stem.replace("_", " ")
    channels: set[int] = set()
    notes: list[int] = []
    active: dict[tuple[int, int], int] = {}
    active_total = 0
    peak_polyphony = 0

    for message in mido.merge_tracks(midi_file.tracks):
        elapsed += mido.tick2second(message.time, midi_file.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
            continue
        if message.type == "track_name":
            continue
        if message.is_meta:
            continue

        if message.type in ("note_on", "note_off"):
            status = (0x90 if message.type == "note_on" else 0x80) | message.channel
            velocity = int(message.velocity)
            note = int(message.note)
            events.append(TimedMidiEvent(elapsed, status, note, velocity))
            channels.add(message.channel)
            notes.append(note)
            key = (message.channel, note)
            if message.type == "note_on" and velocity > 0:
                active[key] = active.get(key, 0) + 1
                active_total += 1
                peak_polyphony = max(peak_polyphony, active_total)
            elif active.get(key, 0):
                active[key] -= 1
                active_total -= 1
        elif message.type == "control_change" and message.control == 64:
            events.append(
                TimedMidiEvent(elapsed, 0xB0 | message.channel, 64, int(message.value))
            )
            channels.add(message.channel)

    if not events:
        raise ValueError("这个 MIDI 文件中没有可演奏的音符")
    return MidiSong(
        path=midi_path,
        title=title,
        events=tuple(events),
        duration_s=max(elapsed, events[-1].time_s),
        min_note=min(notes),
        max_note=max(notes),
        peak_polyphony=peak_polyphony,
        channels=tuple(sorted(channels)),
    )


def transpose_event(
    event: TimedMidiEvent,
    semitones: int,
    velocity_percent: int = 100,
) -> TimedMidiEvent | None:
    """Apply playback transpose/velocity without changing CC messages."""
    if event.kind not in (0x80, 0x90):
        return event
    note = event.data1 + int(semitones)
    if not 0 <= note <= 127:
        return None
    velocity = event.data2
    if velocity:
        velocity = max(1, min(127, round(velocity * velocity_percent / 100.0)))
    return TimedMidiEvent(event.time_s, event.status, note, velocity)

