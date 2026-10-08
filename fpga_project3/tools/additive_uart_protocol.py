"""Shared UART protocol helpers for the Primer 25K additive synthesizer."""

from __future__ import annotations

import json
import math
import struct
import time
from pathlib import Path
from typing import Iterable


SAMPLE_RATE = 48_828.125
ANCHOR_NAMES = ("C2", "C3", "C4", "C6")
ANCHOR_MIDI_NOTES = (36, 48, 60, 84)

CMD_ATTACK = 0x10
CMD_DECAY = 0x11
CMD_SUSTAIN = 0x12
CMD_RELEASE = 0x13
CMD_HARMONIC_AMPLITUDE = 0x40
CMD_HARMONIC_DECAY = 0x41
CMD_COMMIT = 0x42


def make_packet(command: int, payload: bytes = bytes(8)) -> bytes:
    if len(payload) != 8:
        raise ValueError("payload must contain exactly eight bytes")
    checksum = command
    for value in payload:
        checksum ^= value
    return bytes((0xA5, 0x5A, command)) + payload + bytes((checksum,))


def timing_packet(command: int, milliseconds: float) -> bytes:
    if not 0.1 <= milliseconds <= 60_000:
        raise ValueError("ADSR time must be between 0.1 and 60000 ms")
    samples = max(1, round(milliseconds * SAMPLE_RATE / 1000.0))
    phase_increment = 0xFFFFFFFF if samples <= 1 else round(0xFFFFFFFF / (samples - 1))
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
    if not 0 <= anchor < 4 or not 0 <= harmonic < 16:
        raise ValueError("anchor must be 0..3 and harmonic must be 0..15")
    payload = bytearray(8)
    payload[0] = anchor
    payload[1] = harmonic
    rate = decay_ms_to_q24(decay_ms)
    payload[2:5] = rate.to_bytes(3, "little")
    return make_packet(CMD_HARMONIC_DECAY, bytes(payload))


def commit_packet() -> bytes:
    return make_packet(CMD_COMMIT)


def validate_preset(preset: dict) -> None:
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


def preset_packets(preset: dict) -> list[bytes]:
    validate_preset(preset)
    adsr = preset["adsr"]
    packets = [
        timing_packet(CMD_ATTACK, float(adsr["attack_ms"])),
        timing_packet(CMD_DECAY, float(adsr["decay_ms"])),
        sustain_packet(float(adsr["sustain"])),
        timing_packet(CMD_RELEASE, float(adsr["release_ms"])),
    ]
    for anchor_index, anchor in enumerate(preset["anchors"]):
        for harmonic_index, value in enumerate(anchor["amplitudes"]):
            packets.append(amplitude_packet(anchor_index, harmonic_index, float(value)))
        for harmonic_index, value in enumerate(anchor["decay_ms"]):
            packets.append(harmonic_decay_packet(anchor_index, harmonic_index, float(value)))
    packets.append(commit_packet())
    return packets


def load_preset(path: Path | str) -> dict:
    preset = json.loads(Path(path).read_text(encoding="utf-8"))
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
