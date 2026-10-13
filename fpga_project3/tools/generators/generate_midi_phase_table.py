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


if __name__ == "__main__":
    main()
