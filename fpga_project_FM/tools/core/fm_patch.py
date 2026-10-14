"""DX7-compatible patch model and UART compiler for the FPGA FM engine.

The hardware deliberately consumes linear fixed-point values.  This module
owns the DX7 packing rules and the nonlinear conversions so importing a
public-domain 6-operator ``.syx`` bank does not cost FPGA logic.
"""

from __future__ import annotations

import json
import math
import struct
import time
from copy import deepcopy
from pathlib import Path
from typing import Iterable


SAMPLE_RATE = 48_828.125
CMD_MIDI_EVENT = 0x30
CMD_STRUM_CONFIG = 0x50
CMD_FM_GLOBAL = 0x60
CMD_FM_PITCH_SENSITIVITY = 0x61
CMD_FM_OPERATOR = 0x62
CMD_FM_ENVELOPE = 0x63
CMD_FM_SCALING = 0x64
CMD_FM_PITCH_ENVELOPE = 0x65
CMD_FM_COMMIT = 0x66
CMD_FM_ALGORITHM_FLAGS = 0x67
CMD_FM_NOTE_GAIN = 0x68

# Operator order is OP6, OP5, ... OP1, identical to a DX7 bulk dump and to
# Google's Apache-2.0 msfa FM engine.  The bit meanings are documented in RTL.
ALGORITHM_FLAGS = (
    (0xC1, 0x11, 0x11, 0x14, 0x01, 0x14),
    (0x01, 0x11, 0x11, 0x14, 0xC1, 0x14),
    (0xC1, 0x11, 0x14, 0x01, 0x11, 0x14),
    (0x41, 0x11, 0x94, 0x01, 0x11, 0x14),
    (0xC1, 0x14, 0x01, 0x14, 0x01, 0x14),
    (0x41, 0x94, 0x01, 0x14, 0x01, 0x14),
    (0xC1, 0x11, 0x05, 0x14, 0x01, 0x14),
    (0x01, 0x11, 0xC5, 0x14, 0x01, 0x14),
    (0x01, 0x11, 0x05, 0x14, 0xC1, 0x14),
    (0x01, 0x05, 0x14, 0xC1, 0x11, 0x14),
    (0xC1, 0x05, 0x14, 0x01, 0x11, 0x14),
    (0x01, 0x05, 0x05, 0x14, 0xC1, 0x14),
    (0xC1, 0x05, 0x05, 0x14, 0x01, 0x14),
    (0xC1, 0x05, 0x11, 0x14, 0x01, 0x14),
    (0x01, 0x05, 0x11, 0x14, 0xC1, 0x14),
    (0xC1, 0x11, 0x02, 0x25, 0x05, 0x14),
    (0x01, 0x11, 0x02, 0x25, 0xC5, 0x14),
    (0x01, 0x11, 0x11, 0xC5, 0x05, 0x14),
    (0xC1, 0x14, 0x14, 0x01, 0x11, 0x14),
    (0x01, 0x05, 0x14, 0xC1, 0x14, 0x14),
    (0x01, 0x14, 0x14, 0xC1, 0x14, 0x14),
    (0xC1, 0x14, 0x14, 0x14, 0x01, 0x14),
    (0xC1, 0x14, 0x14, 0x01, 0x14, 0x04),
    (0xC1, 0x14, 0x14, 0x14, 0x04, 0x04),
    (0xC1, 0x14, 0x14, 0x04, 0x04, 0x04),
    (0xC1, 0x05, 0x14, 0x01, 0x14, 0x04),
    (0x01, 0x05, 0x14, 0xC1, 0x14, 0x04),
    (0x04, 0xC1, 0x11, 0x14, 0x01, 0x14),
    (0xC1, 0x14, 0x01, 0x14, 0x04, 0x04),
    (0x04, 0xC1, 0x11, 0x14, 0x04, 0x04),
    (0xC1, 0x14, 0x04, 0x04, 0x04, 0x04),
    (0xC4, 0x04, 0x04, 0x04, 0x04, 0x04),
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def make_packet(command: int, payload: bytes = bytes(8)) -> bytes:
    if len(payload) != 8:
        raise ValueError("payload must contain exactly eight bytes")
    checksum = command
    for byte in payload:
        checksum ^= byte
    return bytes((0xA5, 0x5A, command)) + payload + bytes((checksum,))


def midi_event_packet(status: int, data1: int = 0, data2: int = 0) -> bytes:
    payload = bytes((status & 0xFF, data1 & 0x7F, data2 & 0x7F,
                     data2 & 0x7F, 0, 0, 0, 0))
    return make_packet(CMD_MIDI_EVENT, payload)


def all_notes_off_packets() -> list[bytes]:
    return [midi_event_packet(0xB0 | channel, cc, 0)
            for channel in range(16) for cc in (64, 123)]


def default_operator(number: int) -> dict:
    carrier = number in (1, 3, 5)
    return {
        "number": number,
        "enabled": True,
        "mode": "ratio",
        "coarse": 1 if carrier else (2 if number == 2 else 3),
        "fine": 0,
        "detune": 7,
        "output_level": 88 if carrier else 68,
        "eg_rates": [95, 45, 30, 65],
        "eg_levels": [99, 72, 55, 0],
        "rate_scaling": 2,
        "velocity_sensitivity": 3,
        "amp_mod_sensitivity": 0,
        "breakpoint": 39,
        "left_depth": 0,
        "right_depth": 18,
        "left_curve": 0,
        "right_curve": 0,
    }


def default_patch() -> dict:
    return {
        "format": "gowin-fm-patch-v1",
        "name": "FPGA E.PIANO",
        "algorithm": 5,
        "feedback": 2,
        "osc_sync": True,
        "transpose": 24,
        "lfo": {"speed": 35, "delay": 20, "pitch_depth": 0,
                "amp_depth": 0, "sync": True, "waveform": 4,
                "pitch_sensitivity": 3},
        "pitch_eg_rates": [50, 50, 50, 50],
        "pitch_eg_levels": [50, 50, 50, 50],
        "operators": [default_operator(number) for number in range(6, 0, -1)],
    }


def normalise_patch(raw: dict) -> dict:
    patch = deepcopy(default_patch())
    for key in ("name", "algorithm", "feedback", "osc_sync", "transpose",
                "pitch_eg_rates", "pitch_eg_levels"):
        if key in raw:
            patch[key] = deepcopy(raw[key])
    patch["lfo"].update(raw.get("lfo", {}))
    for index, op in enumerate(raw.get("operators", [])[:6]):
        patch["operators"][index].update(op)
    patch["name"] = str(patch["name"])[:10]
    patch["algorithm"] = int(_clamp(int(patch["algorithm"]), 1, 32))
    patch["feedback"] = int(_clamp(int(patch["feedback"]), 0, 7))
    patch["transpose"] = int(_clamp(int(patch["transpose"]), 0, 48))
    patch["osc_sync"] = bool(patch["osc_sync"])
    for name in ("speed", "delay", "pitch_depth", "amp_depth"):
        patch["lfo"][name] = int(_clamp(int(patch["lfo"][name]), 0, 99))
    patch["lfo"]["waveform"] = int(_clamp(int(patch["lfo"]["waveform"]), 0, 5))
    patch["lfo"]["pitch_sensitivity"] = int(_clamp(
        int(patch["lfo"]["pitch_sensitivity"]), 0, 7))
    patch["lfo"]["sync"] = bool(patch["lfo"].get("sync", True))
    patch["pitch_eg_rates"] = [int(_clamp(int(v), 0, 99))
                               for v in (patch["pitch_eg_rates"] + [50] * 4)[:4]]
    patch["pitch_eg_levels"] = [int(_clamp(int(v), 0, 99))
                                for v in (patch["pitch_eg_levels"] + [50] * 4)[:4]]
    for index, op in enumerate(patch["operators"]):
        op["number"] = 6 - index
        op["enabled"] = bool(op.get("enabled", True))
        op["mode"] = "fixed" if str(op.get("mode", "ratio")).lower() == "fixed" else "ratio"
        for name, maximum in (("coarse", 31), ("fine", 99), ("detune", 14),
                              ("output_level", 99), ("rate_scaling", 7),
                              ("velocity_sensitivity", 7),
                              ("amp_mod_sensitivity", 3), ("breakpoint", 99),
                              ("left_depth", 99), ("right_depth", 99),
                              ("left_curve", 3), ("right_curve", 3)):
            op[name] = int(_clamp(int(op.get(name, 0)), 0, maximum))
        op["eg_rates"] = [int(_clamp(int(v), 0, 99))
                          for v in (list(op.get("eg_rates", [])) + [50] * 4)[:4]]
        op["eg_levels"] = [int(_clamp(int(v), 0, 99))
                           for v in (list(op.get("eg_levels", [])) + [0] * 4)[:4]]
    return patch


def _unpack_bulk_voice(data: bytes) -> dict:
    if len(data) != 128:
        raise ValueError("a packed DX7 voice must contain 128 bytes")
    patch = default_patch()
    operators = []
    for index in range(6):
        source = data[index * 17:(index + 1) * 17]
        operators.append({
            "number": 6 - index, "enabled": True,
            "eg_rates": [min(99, value & 0x7F) for value in source[0:4]],
            "eg_levels": [min(99, value & 0x7F) for value in source[4:8]],
            "breakpoint": min(99, source[8] & 0x7F),
            "left_depth": min(99, source[9] & 0x7F),
            "right_depth": min(99, source[10] & 0x7F),
            "left_curve": source[11] & 3,
            "right_curve": (source[11] >> 2) & 3,
            "rate_scaling": source[12] & 7,
            "detune": (source[12] >> 3) & 15,
            "amp_mod_sensitivity": source[13] & 3,
            "velocity_sensitivity": (source[13] >> 2) & 7,
            "output_level": min(99, source[14] & 0x7F),
            "mode": "fixed" if (source[15] & 1) else "ratio",
            "coarse": (source[15] >> 1) & 31,
            "fine": min(99, source[16] & 0x7F),
        })
    patch.update({
        "operators": operators,
        "pitch_eg_rates": [min(99, value & 0x7F) for value in data[102:106]],
        "pitch_eg_levels": [min(99, value & 0x7F) for value in data[106:110]],
        "algorithm": (data[110] & 31) + 1,
        "feedback": data[111] & 7,
        "osc_sync": bool((data[111] >> 3) & 1),
        "lfo": {"speed": min(99, data[112] & 0x7F),
                "delay": min(99, data[113] & 0x7F),
                "pitch_depth": min(99, data[114] & 0x7F),
                "amp_depth": min(99, data[115] & 0x7F),
                "sync": bool(data[116] & 1),
                "waveform": (data[116] >> 1) & 7,
                "pitch_sensitivity": (data[116] >> 4) & 7},
        "transpose": min(48, data[117] & 0x7F),
        "name": bytes(value & 0x7F for value in data[118:128]).decode(
            "ascii", errors="replace").strip() or "UNTITLED",
    })
    return normalise_patch(patch)


def _unpack_single_voice(data: bytes) -> dict:
    """Convert the 155-byte unpacked format to the common patch dictionary."""
    if len(data) != 155:
        raise ValueError("a single DX7 voice must contain 155 bytes")
    packed = bytearray(128)
    for op in range(6):
        source = data[op * 21:(op + 1) * 21]
        target = op * 17
        packed[target:target + 11] = bytes(value & 0x7F for value in source[:11])
        packed[target + 11] = (source[11] & 3) | ((source[12] & 3) << 2)
        packed[target + 12] = (source[13] & 7) | ((source[20] & 15) << 3)
        packed[target + 13] = (source[14] & 3) | ((source[15] & 7) << 2)
        packed[target + 14] = source[16] & 0x7F
        packed[target + 15] = (source[17] & 1) | ((source[18] & 31) << 1)
        packed[target + 16] = source[19] & 0x7F
    packed[102:110] = bytes(value & 0x7F for value in data[126:134])
    packed[110] = data[134] & 31
    packed[111] = (data[135] & 7) | ((data[136] & 1) << 3)
    packed[112:116] = bytes(value & 0x7F for value in data[137:141])
    packed[116] = ((data[141] & 1) | ((data[142] & 7) << 1) |
                   ((data[143] & 7) << 4))
    packed[117] = data[144] & 0x7F
    packed[118:128] = bytes(value & 0x7F for value in data[145:155])
    return _unpack_bulk_voice(bytes(packed))


def load_syx(path: Path | str) -> list[dict]:
    """Load one or more Yamaha DX7 voices from a SysEx file."""
    raw = Path(path).read_bytes()
    patches: list[dict] = []
    cursor = 0
    while True:
        start = raw.find(b"\xF0\x43", cursor)
        if start < 0:
            break
        end = raw.find(b"\xF7", start + 2)
        if end < 0:
            break
        message = raw[start:end + 1]
        cursor = end + 1
        if len(message) >= 4104 and message[3] == 0x09:
            body = message[6:6 + 4096]
            if len(body) == 4096:
                patches.extend(_unpack_bulk_voice(body[i:i + 128])
                               for i in range(0, 4096, 128))
        elif len(message) >= 163 and message[3] == 0x00:
            body = message[6:6 + 155]
            if len(body) == 155:
                patches.append(_unpack_single_voice(body))
    if not patches:
        # Some archives distribute the raw packed bank without the SysEx shell.
        if len(raw) == 4096:
            patches = [_unpack_bulk_voice(raw[i:i + 128])
                       for i in range(0, 4096, 128)]
        elif len(raw) == 128:
            patches = [_unpack_bulk_voice(raw)]
    if not patches:
        raise ValueError("no DX7 single voice or 32-voice bank was found")
    return patches


def save_patch(path: Path | str, patch: dict) -> None:
    Path(path).write_text(json.dumps(normalise_patch(patch), ensure_ascii=False,
                                     indent=2), encoding="utf-8")


def load_patch(path: Path | str) -> dict:
    return normalise_patch(json.loads(Path(path).read_text(encoding="utf-8")))


def operator_frequency_word(op: dict) -> int:
    coarse, fine = op["coarse"], op["fine"]
    if op["mode"] == "fixed":
        # DX7 fixed-mode coarse selects a power-of-ten decade; fine moves
        # continuously inside it.  Direct phase increment is Q0.32.
        frequency = 10.0 ** ((coarse & 3) + fine / 100.0)
        frequency *= 2.0 ** ((op["detune"] - 7) / 1200.0)
        return int(_clamp(round(frequency / SAMPLE_RATE * 2**32), 0, 0xFFFFFFFF))
    ratio = (0.5 if coarse == 0 else float(coarse)) * (1.0 + fine / 100.0)
    ratio *= 2.0 ** (((op["detune"] - 7) * 2.0) / 1200.0)
    return int(_clamp(round(ratio * 65536.0), 0, 0xFFFFFFFF))


def output_level_gain_q14(level: int) -> int:
    # DX7 levels below 20 are deliberately nonlinear.  This is the same
    # scale used by the public-domain measurements and the msfa/Dexed engine.
    scaled = dx7_scale_level(level)
    gain = 2.0 ** ((scaled - 127) / 8.0)
    return int(_clamp(round(gain * 16384.0), 0, 0xFFFF))


def envelope_level_q16(level: int) -> int:
    scaled = dx7_scale_level(level)
    gain = 2.0 ** ((scaled - 127) / 8.0)
    return int(_clamp(round(gain * 65535.0), 0, 0xFFFF))


_DX7_LOW_LEVELS = (0, 5, 9, 13, 17, 20, 23, 25, 27, 29,
                   31, 33, 35, 37, 39, 41, 42, 43, 45, 46)


def dx7_scale_level(level: int) -> int:
    """Map a 0..99 DX7 level to the measured nonlinear 0..127 scale."""
    level = int(_clamp(level, 0, 99))
    return 28 + level if level >= 20 else _DX7_LOW_LEVELS[level]


def dx7_envelope_target(level: int) -> int:
    """Return the operator-EG target before output/key/velocity offsets.

    Units are the DX7's approximately 0.0235 dB internal level steps.  The
    FPGA keeps twelve fractional bits while moving between these targets.
    """
    return (dx7_scale_level(level) >> 1) << 6


def dx7_base_qrate(rate: int) -> int:
    """Compile a patch rate to the 0..63 DX7 rate clock domain."""
    return int(_clamp((int(_clamp(rate, 0, 99)) * 41) >> 6, 0, 63))


def _dx7_scale_curve(group: int, depth: int, curve: int) -> int:
    exp_scale = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 16, 19, 23,
                 27, 33, 39, 47, 56, 66, 80, 94, 110, 126, 142, 158,
                 174, 190, 206, 222, 238, 250)
    group = max(0, int(group))
    if curve in (0, 3):
        scale = (group * depth * 329) >> 12
    else:
        scale = (exp_scale[min(group, len(exp_scale) - 1)] * depth * 329) >> 15
    return -scale if curve < 2 else scale


def dx7_keyboard_level_offset(op: dict, note: int) -> int:
    """Output/key-scaling contribution used by the logarithmic FPGA EG."""
    offset = int(note) - int(op["breakpoint"]) - 17
    if offset >= 0:
        scale = _dx7_scale_curve((offset + 1) // 3,
                                 op["right_depth"], op["right_curve"])
    else:
        scale = _dx7_scale_curve(-(offset - 1) // 3,
                                 op["left_depth"], op["left_curve"])
    outlevel = min(127, dx7_scale_level(op["output_level"]) + scale)
    # Velocity is note-event dependent and is added in RTL.  4256 is the
    # measured DX7 zero reference used by msfa/Dexed.
    return (outlevel << 5) - 4256


def envelope_coefficient(rate: int) -> int:
    # One-pole time mapping: rate 99 is percussive, rate 0 takes tens of
    # seconds.  FPGA rate scaling can only accelerate this base coefficient.
    seconds = 0.005 * 2.0 ** ((99 - rate) / 8.0)
    alpha = 1.0 - math.exp(-1.0 / (SAMPLE_RATE * seconds))
    return int(_clamp(round(alpha * 65535.0), 1, 0xFFFF))


def pitch_ratio_q16(level: int) -> int:
    # DX pitch-EG level 50 is the centre.  This musical approximation limits
    # the imported range to roughly +/-12 semitones for safe live audition.
    semitones = (level - 50) * (12.0 / 49.0)
    return int(_clamp(round((2.0 ** (semitones / 12.0)) * 65536.0), 0, 0x1FFFF))


def keyboard_gain_q14(op: dict, note: int) -> int:
    # Kept for callers that need a linear preview.  UART command 0x68 now
    # carries the signed logarithmic offset returned by
    # dx7_keyboard_level_offset instead.
    offset = dx7_keyboard_level_offset(op, note)
    gain = 2.0 ** ((offset + 192) / 256.0)
    return int(_clamp(round(gain * 16384.0), 0, 0xFFFF))


def patch_packets(raw_patch: dict) -> list[bytes]:
    patch = normalise_patch(raw_patch)
    packets = all_notes_off_packets()
    lfo = patch["lfo"]
    global_payload = bytearray(8)
    global_payload[0] = patch["algorithm"] - 1
    global_payload[1] = patch["feedback"] | (int(patch["osc_sync"]) << 3)
    global_payload[2] = patch["transpose"]
    global_payload[3] = lfo["waveform"]
    global_payload[4] = lfo["speed"]
    global_payload[5] = lfo["delay"]
    global_payload[6] = lfo["pitch_depth"]
    global_payload[7] = lfo["amp_depth"]
    packets.append(make_packet(CMD_FM_GLOBAL, bytes(global_payload)))
    packets.append(make_packet(CMD_FM_PITCH_SENSITIVITY,
                               bytes((lfo["pitch_sensitivity"], 0, 0, 0, 0, 0, 0, 0))))
    packets.append(make_packet(CMD_FM_ALGORITHM_FLAGS,
                               bytes(ALGORITHM_FLAGS[patch["algorithm"] - 1]) + bytes(2)))
    for op_index, op in enumerate(patch["operators"]):
        payload = bytearray(8)
        payload[0] = op_index
        payload[1] = int(op["enabled"]) | (int(op["mode"] == "fixed") << 1)
        struct.pack_into("<I", payload, 2, operator_frequency_word(op))
        struct.pack_into("<H", payload, 6, output_level_gain_q14(op["output_level"]))
        packets.append(make_packet(CMD_FM_OPERATOR, bytes(payload)))
        for stage in range(4):
            env_payload = bytearray(8)
            env_payload[0] = op_index
            env_payload[1] = stage
            struct.pack_into("<H", env_payload, 2,
                             dx7_base_qrate(op["eg_rates"][stage]))
            struct.pack_into("<H", env_payload, 4,
                             dx7_envelope_target(op["eg_levels"][stage]))
            env_payload[6] = op["rate_scaling"]
            env_payload[7] = (op["velocity_sensitivity"] |
                              (op["amp_mod_sensitivity"] << 4))
            packets.append(make_packet(CMD_FM_ENVELOPE, bytes(env_payload)))
        scale_payload = bytes((op_index, op["breakpoint"], op["left_depth"],
                               op["right_depth"], op["left_curve"],
                               op["right_curve"], 0, 0))
        packets.append(make_packet(CMD_FM_SCALING, scale_payload))
        for note in range(128):
            gain_payload = bytearray(8)
            gain_payload[0] = op_index
            gain_payload[1] = note
            struct.pack_into("<h", gain_payload, 2,
                             dx7_keyboard_level_offset(op, note))
            packets.append(make_packet(CMD_FM_NOTE_GAIN, bytes(gain_payload)))
    for stage in range(4):
        payload = bytearray(8)
        payload[0] = stage
        struct.pack_into("<H", payload, 1,
                         envelope_coefficient(patch["pitch_eg_rates"][stage]))
        ratio = pitch_ratio_q16(patch["pitch_eg_levels"][stage])
        payload[3:6] = ratio.to_bytes(3, "little")
        packets.append(make_packet(CMD_FM_PITCH_ENVELOPE, bytes(payload)))
    packets.append(make_packet(CMD_FM_COMMIT))
    return packets


def send_packets(port_name: str, packets: Iterable[bytes], baud: int = 115200,
                 progress=None) -> int:
    try:
        import serial
    except ImportError as error:
        raise RuntimeError("pyserial is required") from error
    packet_list = list(packets)
    with serial.Serial(port_name, baudrate=baud, timeout=0.1,
                       write_timeout=2.0, rtscts=False, dsrdtr=False) as port:
        port.dtr = False
        port.rts = False
        for index, packet in enumerate(packet_list):
            port.write(packet)
            if progress and (index & 31) == 0:
                progress(index + 1, len(packet_list))
            # The 115200-baud wire needs about 1.05 ms per 12-byte packet.
            # Small pacing prevents the FPGA UART parser from being flooded.
            time.sleep(0.00115)
        port.flush()
        time.sleep(0.03)
    if progress:
        progress(len(packet_list), len(packet_list))
    return len(packet_list)
