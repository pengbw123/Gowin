import math

from tone_model import Envelope, FilterSettings, Tone, generate_wavetable


def _tone(name, harmonics, amp, filt, phases=None, gain=0.72, description=""):
    phases = phases or [0.0] * 16
    tone = Tone(
        name=name,
        harmonics=(harmonics + [0.0] * 16)[:16],
        harmonic_phases=(phases + [0.0] * 16)[:16],
        amplitude_envelope=amp,
        filter=filt,
        master_gain=gain,
        metadata={"description": description, "source": "built-in synthetic preset"},
    )
    tone.wavetable = generate_wavetable(tone.harmonics, tone.harmonic_phases).tolist()
    return tone


def build_presets():
    piano = _tone(
        "钢琴（合成）",
        [1.0, 0.72, 0.48, 0.31, 0.25, 0.18, 0.13, 0.10, 0.075, 0.055, 0.04, 0.03],
        Envelope(8, 1250, 0.18, 1650, 0.35, -0.55, -0.55),
        FilterSettings(True, True, 650, 14500, 1.0, 0.2),
        phases=[0.0, 0.08, -0.12, 0.15, -0.18, 0.20, -0.15, 0.10],
        gain=0.72,
        description="Bright attack with a long, darkening decay. Synthetic rather than sampled piano.",
    )

    violin = _tone(
        "小提琴（合成）",
        [1.0, 0.62, 0.47, 0.34, 0.28, 0.22, 0.18, 0.15, 0.12, 0.10, 0.085, 0.07, 0.055],
        Envelope(170, 420, 0.78, 520, -0.1, -0.15, -0.25),
        FilterSettings(True, True, 1700, 11000, 0.65, 0.35),
        phases=[0.0, 0.18, -0.12, 0.24, -0.2, 0.12],
        gain=0.60,
        description="Saw-like harmonic spectrum with a bowed, slower attack.",
    )

    strings = _tone(
        "管弦弦乐",
        [1.0, 0.42, 0.36, 0.22, 0.19, 0.13, 0.11, 0.075, 0.06, 0.045],
        Envelope(650, 900, 0.82, 1800, -0.45, -0.25, -0.55),
        FilterSettings(True, True, 900, 8500, 0.55, 0.25),
        phases=[0.0, math.pi / 3, -math.pi / 5, math.pi / 7, -math.pi / 4],
        gain=0.58,
        description="Slow orchestral string pad suitable for chords.",
    )

    brass = _tone(
        "铜管",
        [1.0, 0.80, 0.58, 0.44, 0.33, 0.25, 0.18, 0.13, 0.095, 0.07],
        Envelope(90, 380, 0.72, 480, 0.05, -0.20, -0.3),
        FilterSettings(True, True, 750, 12500, 0.9, 0.4),
        gain=0.55,
        description="Bright brass spectrum with filter-envelope emphasis.",
    )

    organ = _tone(
        "管风琴",
        [1.0, 0.10, 0.50, 0.06, 0.25, 0.04, 0.14, 0.03],
        Envelope(35, 120, 0.92, 260, 0.0, 0.0, -0.15),
        FilterSettings(True, True, 2500, 13000, 0.35, 0.0),
        gain=0.62,
        description="Stable drawbar-style tone with strong octave harmonics.",
    )

    flute = _tone(
        "长笛",
        [1.0, 0.12, 0.055, 0.025, 0.012],
        Envelope(110, 350, 0.85, 440, -0.15, -0.15, -0.25),
        FilterSettings(True, True, 1900, 9000, 0.45, 0.15),
        gain=0.68,
        description="Near-sine breathy synthetic flute foundation.",
    )

    return {tone.name: tone for tone in (piano, violin, strings, brass, organ, flute)}
