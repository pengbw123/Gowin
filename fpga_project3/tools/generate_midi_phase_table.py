"""Generate 128 Q0.32 DDS phase increments for the FPGA sample rate."""

from pathlib import Path


SAMPLE_RATE_HZ = 48_828.125
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "generated" / "midi_phase_inc.mem"
PITCH_RATIO_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "generated"
    / "pitch_ratio_q16.mem"
)


def main():
    values = []
    for note in range(128):
        frequency = 440.0 * (2.0 ** ((note - 69) / 12.0))
        increment = round(frequency * (2**32) / SAMPLE_RATE_HZ)
        values.append(f"{max(1, min(0xFFFFFFFF, increment)):08X}")
    OUTPUT.write_text("\n".join(values) + "\n", encoding="ascii")
    print(f"generated {OUTPUT} ({len(values)} notes)")

    # 512 entries span -2 to almost +2 semitones.  Index 256 is exactly
    # unshifted pitch.  Q1.16 retains enough precision for low MIDI notes.
    ratios = []
    for index in range(512):
        semitones = (index - 256) / 128.0
        ratio_q16 = round((2.0 ** (semitones / 12.0)) * (2**16))
        ratios.append(f"{max(1, min(0x1FFFF, ratio_q16)):017b}")
    PITCH_RATIO_OUTPUT.write_text("\n".join(ratios) + "\n", encoding="ascii")
    print(f"generated {PITCH_RATIO_OUTPUT} ({len(ratios)} pitch ratios)")


if __name__ == "__main__":
    main()
