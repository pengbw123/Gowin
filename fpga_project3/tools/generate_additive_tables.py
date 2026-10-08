"""Generate the FPGA sine ROM and C2/C3/C4/C6 additive-piano defaults."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


POINTS = 2048
HARMONICS = 16
ANCHORS = 4
Q15_MAX = 32767
Q24_MAX = (1 << 24) - 1


def decay_ms_to_q24(decay_ms: float, sample_rate: float) -> int:
    """Return alpha for level[n+1] = level[n] * (1-alpha)."""
    if decay_ms <= 0:
        return 0
    tau_samples = decay_ms * sample_rate / 1000.0
    alpha = 1.0 - math.exp(-1.0 / tau_samples)
    return max(1, min(Q24_MAX, round(alpha * Q24_MAX)))


def write_hex(path: Path, values: list[int], digits: int) -> None:
    mask = (1 << (digits * 4)) - 1
    path.write_text(
        "".join(f"{value & mask:0{digits}X}\n" for value in values),
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preset",
        type=Path,
        default=Path(__file__).with_name("additive_piano_4anchor.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "generated",
    )
    args = parser.parse_args()

    preset = json.loads(args.preset.read_text(encoding="utf-8"))
    anchors = preset["anchors"]
    if len(anchors) != ANCHORS:
        raise ValueError(f"expected {ANCHORS} anchors")

    sine = [round(math.sin(2.0 * math.pi * i / POINTS) * Q15_MAX) for i in range(POINTS)]
    amplitudes: list[int] = []
    decays: list[int] = []
    sample_rate = float(preset.get("sample_rate_hz", 48_828.125))

    for anchor in anchors:
        if len(anchor["amplitudes"]) != HARMONICS or len(anchor["decay_ms"]) != HARMONICS:
            raise ValueError("every anchor must contain 16 amplitudes and 16 decay times")
        if sum(anchor["amplitudes"]) > 1.0:
            raise ValueError(f"{anchor['name']} amplitude sum exceeds 1.0")
        amplitudes.extend(round(max(0.0, min(1.0, float(v))) * Q15_MAX) for v in anchor["amplitudes"])
        decays.extend(decay_ms_to_q24(float(v), sample_rate) for v in anchor["decay_ms"])

    args.output.mkdir(parents=True, exist_ok=True)
    write_hex(args.output / "additive_sine_2048.mem", sine, 4)
    write_hex(args.output / "additive_harmonic_amp.mem", amplitudes, 4)
    write_hex(args.output / "additive_harmonic_decay.mem", decays, 6)
    print(f"generated {POINTS}-point sine ROM and {len(amplitudes)} four-anchor coefficients")


if __name__ == "__main__":
    main()
