"""Shared UART protocol helpers for the Primer 25K additive synthesizer."""

from __future__ import annotations

import json
import math
import struct
import time
from pathlib import Path
from typing import Iterable

from envelope_tools import (
    CURVE_POINTS,
    normalise_envelopes,
    pitch_ratio_q12,
    q16_curve,
    stage_timing,
)


SAMPLE_RATE = 48_828.125
ANCHOR_NAMES = ("C2", "C3", "C4", "C6")
ANCHOR_MIDI_NOTES = (36, 48, 60, 84)

CMD_ATTACK = 0x10
CMD_DECAY = 0x11
CMD_SUSTAIN = 0x12
CMD_RELEASE = 0x13
CMD_PITCH_ENVELOPE_TIME = 0x14
CMD_ENVELOPE_TABLE_POINT = 0x20
CMD_MIDI_EVENT = 0x30
CMD_HARMONIC_AMPLITUDE = 0x40
CMD_HARMONIC_DECAY = 0x41
CMD_COMMIT = 0x42
CMD_STRUM_CONFIG = 0x50
CMD_FILTER_POINT = 0x51
CMD_FILTER_TIME = 0x52
CMD_FILTER_CONFIG = 0x53

FILTER_POINTS = 64
FILTER_MIN_HZ = 80.0
FILTER_MAX_HZ = 12_000.0

TABLE_AMP_ATTACK = 0
TABLE_AMP_DECAY = 1
TABLE_AMP_RELEASE = 2
TABLE_PITCH_RATIO = 3

DEFAULT_PERFORMANCE = {
    "mode": "normal",
    "strum_interval_ms": 18,
    "strum_gate_ms": 120,
    "velocity_speed_depth": 80,
    "velocity_brightness_depth": 96,
}


def make_packet(command: int, payload: bytes = bytes(8)) -> bytes:
    if len(payload) != 8:
        raise ValueError("payload must contain exactly eight bytes")
    checksum = command
    for value in payload:
        checksum ^= value
    return bytes((0xA5, 0x5A, command)) + payload + bytes((checksum,))


def timing_packet(command: int, milliseconds: float) -> bytes:
    samples, phase_increment = stage_timing(milliseconds)
    return make_packet(command, struct.pack("<II", samples, min(0xFFFFFFFF, phase_increment)))


def sustain_packet(value: float) -> bytes:
    if not 0.0 <= value <= 1.0:
        raise ValueError("sustain must be between 0 and 1")
    return make_packet(CMD_SUSTAIN, struct.pack("<Q", round(value * 65535.0)))


def amplitude_packet(anchor: int, harmonic: int, amplitude: float) -> bytes:
    if not 0 <= anchor < 4 or not 0 <= harmonic < 16:
        raise ValueError("anchor must be 0..3 and harmonic must be 0..15")
    if not 0.0 <= amplitude <= 1.0:
        raise ValueError("amplitude must be between 0 and 1")
    payload = bytearray(8)
    payload[0] = anchor
    payload[1] = harmonic
    struct.pack_into("<H", payload, 2, round(amplitude * 32767.0))
    return make_packet(CMD_HARMONIC_AMPLITUDE, bytes(payload))


def decay_ms_to_q24(decay_ms: float) -> int:
    if decay_ms < 0 or decay_ms > 120_000:
        raise ValueError("harmonic decay must be between 0 and 120000 ms")
    if decay_ms == 0:
        return 0
    tau_samples = decay_ms * SAMPLE_RATE / 1000.0
    alpha = 1.0 - math.exp(-1.0 / tau_samples)
    return max(1, min((1 << 24) - 1, round(alpha * ((1 << 24) - 1))))


def harmonic_decay_packet(anchor: int, harmonic: int, decay_ms: float) -> bytes:
    """Legacy packet retained for loading older presets.

    The current FPGA architecture deliberately ignores per-partial decay and
    uses the dynamic filter envelope instead, which saves one multiply for
    every rendered harmonic.
    """
    if not 0 <= anchor < 4 or not 0 <= harmonic < 16:
        raise ValueError("anchor must be 0..3 and harmonic must be 0..15")
    payload = bytearray(8)
    payload[0] = anchor
    payload[1] = harmonic
    rate = decay_ms_to_q24(decay_ms)
    payload[2:5] = rate.to_bytes(3, "little")
    return make_packet(CMD_HARMONIC_DECAY, bytes(payload))


def cutoff_hz_to_code(frequency_hz: float) -> int:
    frequency_hz = max(FILTER_MIN_HZ, min(FILTER_MAX_HZ, float(frequency_hz)))
    position = math.log(frequency_hz / FILTER_MIN_HZ) / math.log(FILTER_MAX_HZ / FILTER_MIN_HZ)
    return max(0, min(255, round(position * 255.0)))


def filter_point_packet(anchor: int, address: int, frequency_hz: float) -> bytes:
    if not 0 <= anchor < 4 or not 0 <= address < FILTER_POINTS:
        raise ValueError("filter anchor must be 0..3 and address must be 0..63")
    payload = bytearray(8)
    payload[0] = anchor
    payload[1] = address
    payload[2] = cutoff_hz_to_code(frequency_hz)
    return make_packet(CMD_FILTER_POINT, bytes(payload))


def filter_time_packet(anchor: int, milliseconds: float) -> bytes:
    if not 0 <= anchor < 4:
        raise ValueError("filter anchor must be 0..3")
    _samples, phase_increment = stage_timing(milliseconds)
    payload = bytearray(8)
    payload[0] = anchor
    struct.pack_into("<I", payload, 1, min(0xFFFFFFFF, phase_increment))
    return make_packet(CMD_FILTER_TIME, bytes(payload))


def filter_config_packet(enabled: bool, slope_db_oct: int) -> bytes:
    payload = bytearray(8)
    payload[0] = 1 if enabled else 0
    payload[1] = 1 if int(slope_db_oct) >= 12 else 0
    return make_packet(CMD_FILTER_CONFIG, bytes(payload))


def _default_filter_anchor(index: int) -> dict:
    start_hz = (7200.0, 8200.0, 9500.0, 11_000.0)[index]
    end_hz = (550.0, 700.0, 900.0, 1400.0)[index]
    values = []
    for point in range(FILTER_POINTS):
        x = point / (FILTER_POINTS - 1)
        values.append(start_hz * ((end_hz / start_hz) ** (x ** 0.62)))
    return {"time_ms": 1400.0, "cutoff_hz": values}


def normalise_dynamic_filter(preset: dict) -> dict:
    raw = preset.get("dynamic_filter", {})
    source_anchors = raw.get("anchors", [])
    anchors = []
    for index, (name, note) in enumerate(zip(ANCHOR_NAMES, ANCHOR_MIDI_NOTES)):
        source = source_anchors[index] if index < len(source_anchors) else _default_filter_anchor(index)
        points = [float(value) for value in source.get("cutoff_hz", [])]
        if len(points) != FILTER_POINTS:
            points = _default_filter_anchor(index)["cutoff_hz"]
        anchors.append({
            "name": name,
            "midi_note": note,
            "time_ms": max(0.1, float(source.get("time_ms", 1400.0))),
            "cutoff_hz": [max(FILTER_MIN_HZ, min(FILTER_MAX_HZ, value)) for value in points],
        })
    return {
        "enabled": bool(raw.get("enabled", True)),
        "slope_db_oct": 12 if int(raw.get("slope_db_oct", 12)) >= 12 else 6,
        "anchors": anchors,
    }


def filter_packets(preset: dict) -> list[bytes]:
    config = normalise_dynamic_filter(preset)
    packets = [filter_config_packet(config["enabled"], config["slope_db_oct"])]
    for anchor_index, anchor in enumerate(config["anchors"]):
        packets.append(filter_time_packet(anchor_index, float(anchor["time_ms"])))
        packets.extend(
            filter_point_packet(anchor_index, address, frequency)
            for address, frequency in enumerate(anchor["cutoff_hz"])
        )
    return packets


def commit_packet() -> bytes:
    return make_packet(CMD_COMMIT)


def midi_event_packet(
    status: int,
    data1: int = 0,
    data2: int = 0,
    brightness: int | None = None,
) -> bytes:
    """Build one real-time MIDI event for the FPGA synthesizer.

    ``status`` is the complete MIDI status byte, so the caller may preserve
    the original channel.  The current synthesizer accepts all channels and
    uses Note On, Note Off, CC20 master volume, CC64 sustain and CC123 all
    notes off.  Brightness defaults to velocity for ordinary note events.
    """
    if not 0x80 <= status <= 0xFF:
        raise ValueError("MIDI status must be between 0x80 and 0xFF")
    if not 0 <= data1 <= 127 or not 0 <= data2 <= 127:
        raise ValueError("MIDI data bytes must be between 0 and 127")
    if brightness is None:
        brightness = data2
    if not 0 <= brightness <= 127:
        raise ValueError("brightness must be between 0 and 127")
    payload = bytes((status, data1, data2, brightness, 0, 0, 0, 0))
    return make_packet(CMD_MIDI_EVENT, payload)


def all_notes_off_packets() -> list[bytes]:
    """Release sustain and all voices on all sixteen MIDI channels."""
    packets: list[bytes] = []
    for channel in range(16):
        packets.append(midi_event_packet(0xB0 | channel, 64, 0))
        packets.append(midi_event_packet(0xB0 | channel, 123, 0))
    return packets


def envelope_table_packet(table: int, address: int, value: int) -> bytes:
    if not 0 <= table <= TABLE_PITCH_RATIO:
        raise ValueError("envelope table id must be 0..3")
    if not 0 <= address < CURVE_POINTS:
        raise ValueError("envelope table address must be 0..255")
    if not 0 <= value <= 0xFFFF:
        raise ValueError("envelope table value must be 0..65535")
    payload = bytearray(8)
    payload[0] = table
    payload[1] = address
    struct.pack_into("<H", payload, 2, value)
    return make_packet(CMD_ENVELOPE_TABLE_POINT, bytes(payload))


def envelope_packets(preset: dict) -> list[bytes]:
    tone = normalise_envelopes(preset)
    adsr = tone["adsr"]
    pitch = tone["pitch_envelope"]
    packets = [
        timing_packet(CMD_ATTACK, float(adsr["attack_ms"])),
        timing_packet(CMD_DECAY, float(adsr["decay_ms"])),
        sustain_packet(float(adsr["sustain"])),
        timing_packet(CMD_RELEASE, float(adsr["release_ms"])),
        timing_packet(CMD_PITCH_ENVELOPE_TIME, float(pitch["time_ms"])),
    ]
    tables = (
        q16_curve(adsr["attack_table"]),
        q16_curve(adsr["decay_table"]),
        q16_curve(adsr["release_table"]),
        pitch_ratio_q12(pitch["points_semitones"]),
    )
    for table_id, values in enumerate(tables):
        for address, value in enumerate(values):
            packets.append(envelope_table_packet(table_id, address, value))
    return packets


def strum_packet(performance: dict) -> bytes:
    mode = str(performance.get("mode", "normal")).lower()
    if mode not in ("normal", "strum"):
        raise ValueError("performance mode must be normal or strum")
    interval = int(performance.get("strum_interval_ms", 18))
    gate = int(performance.get("strum_gate_ms", 120))
    speed_depth = int(performance.get("velocity_speed_depth", 80))
    brightness_depth = int(performance.get("velocity_brightness_depth", 96))
    if not 1 <= interval <= 500:
        raise ValueError("strum interval must be between 1 and 500 ms")
    if not 1 <= gate <= 5000:
        raise ValueError("strum gate must be between 1 and 5000 ms")
    if not 0 <= speed_depth <= 127 or not 0 <= brightness_depth <= 127:
        raise ValueError("velocity depths must be between 0 and 127")
    payload = bytearray(8)
    payload[0] = 1 if mode == "strum" else 0
    struct.pack_into("<H", payload, 1, interval)
    struct.pack_into("<H", payload, 3, gate)
    payload[5] = speed_depth
    payload[6] = brightness_depth
    return make_packet(CMD_STRUM_CONFIG, bytes(payload))


def validate_preset(preset: dict) -> None:
    preset = normalise_envelopes(preset)
    anchors = preset.get("anchors", [])
    if len(anchors) != 4:
        raise ValueError("preset must contain four anchors")
    for expected_name, expected_note, anchor in zip(ANCHOR_NAMES, ANCHOR_MIDI_NOTES, anchors):
        if str(anchor.get("name", "")).upper() != expected_name:
            raise ValueError(f"anchor {expected_name} has the wrong name")
        if int(anchor.get("midi_note", -1)) != expected_note:
            raise ValueError(f"anchor {expected_name} must use MIDI note {expected_note}")
        if len(anchor.get("amplitudes", [])) != 16:
            raise ValueError(f"{expected_name} must contain 16 amplitudes")
        if len(anchor.get("decay_ms", [])) != 16:
            raise ValueError(f"{expected_name} must contain 16 decay values")
        for value in anchor["amplitudes"]:
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{expected_name} amplitude is outside 0..1")
    adsr = preset.get("adsr", {})
    for name in ("attack_ms", "decay_ms", "sustain", "release_ms"):
        if name not in adsr:
            raise ValueError(f"preset ADSR is missing {name}")
    for name in ("attack_table", "decay_table", "release_table"):
        if len(adsr.get(name, [])) != CURVE_POINTS:
            raise ValueError(f"preset ADSR {name} must contain 256 points")
    pitch = preset.get("pitch_envelope", {})
    stage_timing(float(pitch.get("time_ms", 0.0)))
    pitch_ratio_q12(pitch.get("points_semitones", []))
    strum_packet(preset.get("performance", DEFAULT_PERFORMANCE))
    normalise_dynamic_filter(preset)


def preset_packets(preset: dict) -> list[bytes]:
    tone = normalise_envelopes(preset)
    validate_preset(tone)
    packets = envelope_packets(tone)
    for anchor_index, anchor in enumerate(tone["anchors"]):
        for harmonic_index, value in enumerate(anchor["amplitudes"]):
            packets.append(amplitude_packet(anchor_index, harmonic_index, float(value)))
    packets.append(commit_packet())
    packets.extend(filter_packets(tone))
    packets.append(strum_packet(tone.get("performance", DEFAULT_PERFORMANCE)))
    return packets


def load_preset(path: Path | str) -> dict:
    preset = normalise_envelopes(json.loads(Path(path).read_text(encoding="utf-8")))
    validate_preset(preset)
    return preset


def send_packets(port_name: str, packets: Iterable[bytes], baud: int = 115200) -> int:
    try:
        import serial
    except ImportError as error:
        raise RuntimeError("pyserial is required: python -m pip install pyserial") from error

    count = 0
    with serial.Serial(port_name, baud, timeout=0.1, write_timeout=2.0) as port:
        for packet in packets:
            port.write(packet)
            count += 1
        port.flush()
        # Keep the port alive until the final stop bit has left the adapter.
        time.sleep(0.03)
    return count
