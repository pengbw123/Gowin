"""Command-line controller for the Primer 25K additive synthesizer.

Examples:
  python uart_synth_control.py COM11 send-preset additive_piano_4anchor.json
  python uart_synth_control.py COM11 harmonic-amp C4 2 0.25 --commit
  python uart_synth_control.py COM11 harmonic-decay C4 2 1800 --commit
  python uart_synth_control.py COM11 adsr 30 500 0.18 800
"""

from __future__ import annotations

import argparse
from pathlib import Path

from additive_uart_protocol import (
    ANCHOR_NAMES,
    CMD_ATTACK,
    CMD_DECAY,
    CMD_RELEASE,
    amplitude_packet,
    commit_packet,
    harmonic_decay_packet,
    load_preset,
    preset_packets,
    send_packets,
    sustain_packet,
    timing_packet,
)


def anchor_index(text: str) -> int:
    normalized = text.upper()
    if normalized not in ANCHOR_NAMES:
        raise argparse.ArgumentTypeError("anchor must be C2, C3, C4 or C6")
    return ANCHOR_NAMES.index(normalized)


def main() -> None:
    parser = argparse.ArgumentParser(description="Primer 25K additive synth UART control")
    parser.add_argument("port", help="COM port, for example COM11")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_preset = subparsers.add_parser("send-preset")
    send_preset.add_argument("path", type=Path)

    amp = subparsers.add_parser("harmonic-amp")
    amp.add_argument("anchor", type=anchor_index)
    amp.add_argument("harmonic", type=int, help="1..16")
    amp.add_argument("amplitude", type=float, help="0..1")
    amp.add_argument("--commit", action="store_true")

    decay = subparsers.add_parser("harmonic-decay")
    decay.add_argument("anchor", type=anchor_index)
    decay.add_argument("harmonic", type=int, help="1..16")
    decay.add_argument("milliseconds", type=float, help="0 disables extra decay")
    decay.add_argument("--commit", action="store_true")

    adsr = subparsers.add_parser("adsr")
    adsr.add_argument("attack_ms", type=float)
    adsr.add_argument("decay_ms", type=float)
    adsr.add_argument("sustain", type=float)
    adsr.add_argument("release_ms", type=float)

    subparsers.add_parser("commit")
    args = parser.parse_args()

    if args.command == "send-preset":
        packets = preset_packets(load_preset(args.path))
    elif args.command == "harmonic-amp":
        if not 1 <= args.harmonic <= 16:
            parser.error("harmonic must be 1..16")
        packets = [amplitude_packet(args.anchor, args.harmonic - 1, args.amplitude)]
        if args.commit:
            packets.append(commit_packet())
    elif args.command == "harmonic-decay":
        if not 1 <= args.harmonic <= 16:
            parser.error("harmonic must be 1..16")
        packets = [harmonic_decay_packet(args.anchor, args.harmonic - 1, args.milliseconds)]
        if args.commit:
            packets.append(commit_packet())
    elif args.command == "adsr":
        packets = [
            timing_packet(CMD_ATTACK, args.attack_ms),
            timing_packet(CMD_DECAY, args.decay_ms),
            sustain_packet(args.sustain),
            timing_packet(CMD_RELEASE, args.release_ms),
        ]
    else:
        packets = [commit_packet()]

    if args.dry_run:
        for packet in packets:
            print(" ".join(f"{value:02X}" for value in packet))
        return

    count = send_packets(args.port, packets, args.baud)
    print(f"sent {count} packet(s) to {args.port}; re-trigger a note to hear harmonic changes")


if __name__ == "__main__":
    main()
