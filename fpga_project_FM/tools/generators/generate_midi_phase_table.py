"""Generate MIDI-note DDS, pitch-ratio and loudness-compensation tables."""

from pathlib import Path

from core.additive_uart_protocol import DEFAULT_NOTE_LOUDNESS, note_gain_table_q14


SAMPLE_RATE_HZ = 48_828.125
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "src" / "generated" / "midi_phase_inc.mem"
PITCH_RATIO_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "pitch_ratio_q16.mem"
)
NOTE_GAIN_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "midi_note_gain_q14.mem"
)
DX7_LEVEL_GAIN_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "dx7_level_gain_q16.mem"
)
DX7_VELOCITY_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "dx7_velocity_curve.mem"
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

    # Unsigned Q2.14 supports gains up to almost 4.0.  Compensation is folded
    # into velocity once at Note On, so it adds no per-sample render cycle.
    note_gains = [f"{value:04X}" for value in note_gain_table_q14({
        "note_loudness": DEFAULT_NOTE_LOUDNESS,
    })]
    NOTE_GAIN_OUTPUT.write_text("\n".join(note_gains) + "\n", encoding="ascii")
    print(f"generated {NOTE_GAIN_OUTPUT} ({len(note_gains)} note gains)")

    # The FM envelope runs in the DX7 logarithmic level domain.  Ten address
    # bits cover 0..4095 internal level steps (four steps per entry).  Level
    # 3840 is normalized to full-scale; higher values saturate so the FPGA
    # keeps deterministic headroom while preserving the measured curve.
    dx7_gains = []
    for address in range(1024):
        level = address * 4
        gain = round((2.0 ** ((level - 3840) / 256.0)) * 65535.0)
        dx7_gains.append(f"{max(0, min(65535, gain)):04X}")
    DX7_LEVEL_GAIN_OUTPUT.write_text(
        "\n".join(dx7_gains) + "\n", encoding="ascii")
    print(f"generated {DX7_LEVEL_GAIN_OUTPUT} ({len(dx7_gains)} levels)")

    velocity_curve = (
        0, 70, 86, 97, 106, 114, 121, 126, 132, 138, 142, 148, 152,
        156, 160, 163, 166, 170, 173, 174, 178, 181, 184, 186, 189, 190,
        194, 196, 198, 200, 202, 205, 206, 209, 211, 214, 216, 218, 220,
        222, 224, 225, 227, 229, 230, 232, 233, 235, 237, 238, 240, 241,
        242, 243, 244, 246, 246, 248, 249, 250, 251, 252, 253, 254,
    )
    DX7_VELOCITY_OUTPUT.write_text(
        "\n".join(f"{value:02X}" for value in velocity_curve) + "\n",
        encoding="ascii")
    print(f"generated {DX7_VELOCITY_OUTPUT} ({len(velocity_curve)} velocities)")


if __name__ == "__main__":
    main()
