"""Generate MIDI-note DDS, pitch-ratio and loudness-compensation tables."""

import math
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
DX7_LFO_PHASE_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "dx7_lfo_phase_inc.mem"
)
DX7_LFO_SINE_OUTPUT = (
    PROJECT_ROOT
    / "src"
    / "generated"
    / "dx7_lfo_sine_q8.mem"
)

# Measured DX7 LFO frequencies used by the msfa/Dexed engine.  The old FPGA
# approximation started near 2 Hz and topped out near 13 Hz; the instrument
# actually spans about 0.063..49.26 Hz, so patches at both ends were audibly
# wrong even when their operator algorithm was correct.
DX7_LFO_FREQUENCIES = (
    0.062541, 0.125031, 0.312393, 0.437120, 0.624610,
    0.750694, 0.936330, 1.125302, 1.249609, 1.436782,
    1.560915, 1.752081, 1.875117, 2.062494, 2.247191,
    2.374451, 2.560492, 2.686728, 2.873976, 2.998950,
    3.188013, 3.369840, 3.500175, 3.682224, 3.812065,
    4.000800, 4.186202, 4.310716, 4.501260, 4.623209,
    4.814636, 4.930480, 5.121901, 5.315191, 5.434783,
    5.617346, 5.750431, 5.946717, 6.062811, 6.248438,
    6.431695, 6.564264, 6.749460, 6.868132, 7.052186,
    7.250580, 7.375719, 7.556294, 7.687577, 7.877738,
    7.993605, 8.181967, 8.372405, 8.504848, 8.685079,
    8.810573, 8.986341, 9.122423, 9.300595, 9.500285,
    9.607994, 9.798158, 9.950249, 10.117361, 11.251125,
    11.384335, 12.562814, 13.676149, 13.904338, 15.092062,
    16.366612, 16.638935, 17.869907, 19.193858, 19.425019,
    20.833333, 21.034918, 22.502250, 24.003841, 24.260068,
    25.746653, 27.173913, 27.578599, 29.052876, 30.693677,
    31.191516, 32.658393, 34.317090, 34.674064, 36.416606,
    38.197097, 38.550501, 40.387722, 40.749796, 42.625746,
    44.326241, 44.883303, 46.772685, 48.590865, 49.261084,
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
    # 3840 is the nominal DX7 output-level-99 peak.  A 17-bit Q1.16 table is
    # intentional: velocity can lift that peak by almost 6 dB.  Saturating it
    # at 16 bits removed the strongest modulation sidebands and made hard
    # attacks sound darker than Dexed.
    dx7_gains = []
    for address in range(1024):
        level = address * 4
        gain = round((2.0 ** ((level - 3840) / 256.0)) * 65536.0)
        dx7_gains.append(f"{max(0, min(0x1FFFF, gain)):017b}")
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

    lfo_phase_increments = [
        round(frequency * (2**32) / SAMPLE_RATE_HZ)
        for frequency in DX7_LFO_FREQUENCIES
    ]
    # Addresses 100..127 are defensive copies of setting 99.
    lfo_phase_increments.extend([lfo_phase_increments[-1]] * 28)
    DX7_LFO_PHASE_OUTPUT.write_text(
        "\n".join(f"{value:08X}" for value in lfo_phase_increments) + "\n",
        encoding="ascii",
    )
    print(f"generated {DX7_LFO_PHASE_OUTPUT} ({len(lfo_phase_increments)} rates)")

    lfo_sine = [
        round(math.sin(2.0 * math.pi * index / 256.0) * 255.0) & 0x1FF
        for index in range(256)
    ]
    DX7_LFO_SINE_OUTPUT.write_text(
        "\n".join(f"{value:09b}" for value in lfo_sine) + "\n",
        encoding="ascii",
    )
    print(f"generated {DX7_LFO_SINE_OUTPUT} ({len(lfo_sine)} samples)")


if __name__ == "__main__":
    main()
