"""Send live ADSR and unison settings to the Tang Primer 25K synth.

Examples:
  python uart_synth_control.py COM6 amp-attack 25
  python uart_synth_control.py COM6 amp-sustain 0.35
  python uart_synth_control.py COM6 filter-follow 0
  python uart_synth_control.py COM6 unison 8
  python uart_synth_control.py COM6 detune-cents 4.0
"""

import argparse
import math
import struct


SAMPLE_RATE = 48_828.125
BASE_PHASE_INCREMENT = 23_012_828

COMMANDS = {
    "amp-attack": 0x10,
    "amp-decay": 0x11,
    "amp-sustain": 0x12,
    "amp-release": 0x13,
    "filter-attack": 0x20,
    "filter-decay": 0x21,
    "filter-sustain": 0x22,
    "filter-release": 0x23,
    "filter-follow": 0x24,
    "unison": 0x30,
    "detune-cents": 0x31,
}


def timing_payload(milliseconds: float) -> bytes:
    samples = max(1, round(milliseconds * SAMPLE_RATE / 1000.0))
    phase_increment = 0xFFFFFFFF if samples <= 1 else round(0xFFFFFFFF / (samples - 1))
    return struct.pack("<II", samples, min(0xFFFFFFFF, phase_increment))


def value_payload(command: str, value: float) -> bytes:
    if command in {"amp-attack", "amp-decay", "amp-release",
                   "filter-attack", "filter-decay", "filter-release"}:
        if value < 0.1 or value > 60_000:
            raise ValueError("ADSR time must be between 0.1 and 60000 ms")
        return timing_payload(value)

    if command in {"amp-sustain", "filter-sustain"}:
        if not 0.0 <= value <= 1.0:
            raise ValueError("sustain must be between 0.0 and 1.0")
        return struct.pack("<Q", round(value * 65535.0))

    if command == "filter-follow":
        return struct.pack("<Q", 1 if value else 0)

    if command == "unison":
        count = int(value)
        if count < 1 or count > 8:
            raise ValueError("unison count must be 1..8")
        return struct.pack("<Q", count)

    if command == "detune-cents":
        if value < 0.0 or value > 50.0:
            raise ValueError("detune step must be between 0 and 50 cents")
        ratio = math.pow(2.0, value / 1200.0) - 1.0
        phase_step = round(BASE_PHASE_INCREMENT * ratio)
        return struct.pack("<Q", phase_step)

    raise ValueError("unsupported command")


def make_packet(command: int, payload: bytes) -> bytes:
    if len(payload) != 8:
        raise ValueError("payload must contain exactly 8 bytes")
    checksum = command
    for byte in payload:
        checksum ^= byte
    return bytes((0xA5, 0x5A, command)) + payload + bytes((checksum,))


def main() -> None:
    parser = argparse.ArgumentParser(description="Tang Primer 25K live synth control")
    parser.add_argument("port", help="serial port, for example COM6")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("value", type=float)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dry-run", action="store_true", help="print packet without opening serial port")
    args = parser.parse_args()

    packet = make_packet(COMMANDS[args.command], value_payload(args.command, args.value))
    packet_text = " ".join("{:02X}".format(byte) for byte in packet)
    if args.dry_run:
        print(packet_text)
        return

    try:
        import serial
    except ImportError as error:
        raise SystemExit("pyserial is required: python -m pip install pyserial") from error

    with serial.Serial(args.port, args.baud, timeout=0.2) as port:
        port.write(packet)
        port.flush()
    print("sent:", packet_text)


if __name__ == "__main__":
    main()
