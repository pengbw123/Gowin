"""Compile one 32-voice DX7 bank into compact FPGA initialization files.

The source ``.syx`` remains outside Git.  Only the small fixed-point memories
and a human-readable name manifest are committed, so cloning the project is
fast and the FPGA can boot without a PC.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from core.fm_patch import (  # noqa: E402
    ALGORITHM_FLAGS,
    dx7_base_qrate,
    dx7_envelope_target,
    load_syx,
    normalise_patch,
    operator_frequency_word,
    pitch_envelope_coefficient,
    pitch_ratio_q16,
)


def write_hex(path: Path, values: list[int], digits: int) -> None:
    path.write_text("".join(f"{value:0{digits}X}\n" for value in values),
                    encoding="ascii")


def compile_bank(source: Path, output: Path) -> list[dict]:
    patches = [normalise_patch(p) for p in load_syx(source)[:32]]
    if len(patches) != 32:
        raise ValueError(f"expected 32 voices, found {len(patches)}")

    globals_mem: list[int] = []
    op_main_mem: list[int] = []
    op_scale_mem: list[int] = []
    eg_mem: list[int] = []
    pitch_mem: list[int] = []
    algorithm_mem: list[int] = []

    for patch in patches:
        lfo = patch["lfo"]
        global_word = (
            ((patch["algorithm"] - 1) & 0x1F)
            | ((patch["feedback"] & 0x7) << 5)
            | (int(patch["osc_sync"]) << 8)
            | (int(lfo.get("sync", True)) << 9)
            | ((patch["transpose"] & 0x7F) << 10)
            | ((lfo["waveform"] & 0x7) << 17)
            | ((lfo["speed"] & 0x7F) << 20)
            | ((lfo["delay"] & 0x7F) << 27)
            | ((lfo["pitch_depth"] & 0x7F) << 34)
            | ((lfo["amp_depth"] & 0x7F) << 41)
            | ((lfo["pitch_sensitivity"] & 0x7) << 48)
        )
        globals_mem.append(global_word)
        algorithm_mem.append(sum(
            (flag & 0xFF) << (8 * index)
            for index, flag in enumerate(ALGORITHM_FLAGS[patch["algorithm"] - 1])
        ))

        for op in patch["operators"]:
            op_main_mem.append(
                int(op["enabled"])
                | (int(op["mode"] == "fixed") << 1)
                | ((operator_frequency_word(op) & 0xFFFFFFFF) << 2)
                | ((op["output_level"] & 0x7F) << 34)
            )
            op_scale_mem.append(
                (op["breakpoint"] & 0x7F)
                | ((op["left_depth"] & 0x7F) << 7)
                | ((op["right_depth"] & 0x7F) << 14)
                | ((op["left_curve"] & 0x3) << 21)
                | ((op["right_curve"] & 0x3) << 23)
                | ((op["rate_scaling"] & 0x7) << 25)
                | ((op["velocity_sensitivity"] & 0x7) << 28)
                | ((op["amp_mod_sensitivity"] & 0x3) << 31)
            )
            for rate, level in zip(op["eg_rates"], op["eg_levels"]):
                eg_mem.append((dx7_base_qrate(rate) & 0x3F)
                              | ((dx7_envelope_target(level) & 0xFFFF) << 6))

        for rate, level in zip(patch["pitch_eg_rates"],
                               patch["pitch_eg_levels"]):
            pitch_mem.append(
                (pitch_envelope_coefficient(rate) & 0xFFFF)
                | ((pitch_ratio_q16(level) & 0x1FFFFF) << 16)
            )

    output.mkdir(parents=True, exist_ok=True)
    write_hex(output / "fm_rom1a_global.mem", globals_mem, 16)
    write_hex(output / "fm_rom1a_operator.mem", op_main_mem, 16)
    write_hex(output / "fm_rom1a_scaling.mem", op_scale_mem, 10)
    write_hex(output / "fm_rom1a_envelope.mem", eg_mem, 6)
    write_hex(output / "fm_rom1a_pitch.mem", pitch_mem, 10)
    write_hex(output / "fm_rom1a_algorithm.mem", algorithm_mem, 12)
    manifest = [{"index": i, "name": patch["name"]}
                for i, patch in enumerate(patches)]
    (output / "fm_rom1a_names.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return patches


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path,
                        help="ROM1A 32-voice .syx bank")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "src" / "generated")
    args = parser.parse_args()
    patches = compile_bank(args.source, args.output)
    print(f"compiled {len(patches)} presets: " +
          ", ".join(p["name"] for p in patches))


if __name__ == "__main__":
    main()
