"""Shared amplitude/pitch-envelope helpers for the FPGA instrument tools.

The RTL stores three normalised amplitude progress tables (Attack, Decay and
Release) plus one pitch-ratio table.  Keeping the conversion here means the
editor preview, UART download and build-time MEM export all use exactly the
same 256-point data.
"""

from __future__ import annotations

import math
from copy import deepcopy

import numpy as np


SAMPLE_RATE = 48_828.125
CURVE_POINTS = 256
PITCH_MIN_SEMITONES = -24.0
PITCH_MAX_SEMITONES = 24.0
PITCH_RATIO_FRAC_BITS = 12
PITCH_RATIO_ONE = 1 << PITCH_RATIO_FRAC_BITS

DEFAULT_ADSR_CURVES = {
    "attack_curve": 0.9,
    "decay_curve": 0.6,
    "release_curve": 0.3,
}
DEFAULT_PITCH_ENVELOPE = {
    "time_ms": 250.0,
    "points_semitones": [0.0] * CURVE_POINTS,
}


def curve_lut(curve: float) -> list[float]:
    """Return the same power-curve LUT used by the original tone editor."""
    value = max(-1.0, min(1.0, float(curve)))
    exponent = 2.0 ** (-2.0 * value)
    return [
        (index / float(CURVE_POINTS - 1)) ** exponent
        for index in range(CURVE_POINTS)
    ]


def _normalise_curve(values: object, fallback_curve: float) -> list[float]:
    if isinstance(values, list) and len(values) == CURVE_POINTS:
        result = [max(0.0, min(1.0, float(value))) for value in values]
        result[0] = 0.0
        result[-1] = 1.0
        return result
    return curve_lut(fallback_curve)


def normalise_envelopes(preset: dict) -> dict:
    """Return a backward-compatible preset with complete envelope fields."""
    result = deepcopy(preset)
    adsr = result.setdefault("adsr", {})
    adsr.setdefault("attack_ms", 30.0)
    adsr.setdefault("decay_ms", 500.0)
    adsr.setdefault("sustain", 0.18)
    adsr.setdefault("release_ms", 800.0)
    for key, default in DEFAULT_ADSR_CURVES.items():
        adsr.setdefault(key, default)
    adsr["attack_table"] = _normalise_curve(
        adsr.get("attack_table"), adsr["attack_curve"]
    )
    adsr["decay_table"] = _normalise_curve(
        adsr.get("decay_table"), adsr["decay_curve"]
    )
    adsr["release_table"] = _normalise_curve(
        adsr.get("release_table"), adsr["release_curve"]
    )

    pitch = result.setdefault("pitch_envelope", {})
    pitch.setdefault("time_ms", DEFAULT_PITCH_ENVELOPE["time_ms"])
    points = pitch.get("points_semitones")
    if not isinstance(points, list) or len(points) != CURVE_POINTS:
        points = list(DEFAULT_PITCH_ENVELOPE["points_semitones"])
    pitch["points_semitones"] = [
        max(PITCH_MIN_SEMITONES, min(PITCH_MAX_SEMITONES, float(value)))
        for value in points
    ]
    return result


def q16_curve(values: list[float]) -> list[int]:
    if len(values) != CURVE_POINTS:
        raise ValueError("envelope curve must contain 256 points")
    return [round(max(0.0, min(1.0, float(value))) * 65535.0) for value in values]


def pitch_ratio_q12(points_semitones: list[float]) -> list[int]:
    """Convert signed semitone offsets to unsigned Q4.12 frequency ratios."""
    if len(points_semitones) != CURVE_POINTS:
        raise ValueError("pitch envelope must contain 256 points")
    result = []
    for semitones in points_semitones:
        value = max(PITCH_MIN_SEMITONES, min(PITCH_MAX_SEMITONES, float(semitones)))
        ratio = 2.0 ** (value / 12.0)
        result.append(max(1, min(0xFFFF, round(ratio * PITCH_RATIO_ONE))))
    return result


def stage_timing(milliseconds: float) -> tuple[int, int]:
    if not 0.1 <= float(milliseconds) <= 60_000.0:
        raise ValueError("envelope time must be between 0.1 and 60000 ms")
    samples = max(1, round(float(milliseconds) * SAMPLE_RATE / 1000.0))
    phase_increment = (
        0xFFFFFFFF
        if samples <= 1
        else min(0xFFFFFFFF, round(0xFFFFFFFF / float(samples - 1)))
    )
    return samples, phase_increment


def _table_value(table: list[float], count: int, samples: int) -> float:
    if samples <= 1 or count >= samples - 1:
        return float(table[-1])
    address = min(CURVE_POINTS - 1, (count * CURVE_POINTS) // samples)
    return float(table[address])


def amplitude_envelope_samples(
    adsr: dict, note_on_samples: int, total_samples: int
) -> np.ndarray:
    """Render the gate-driven FPGA ADSR, including early Note Off."""
    attack_samples, _ = stage_timing(float(adsr["attack_ms"]))
    decay_samples, _ = stage_timing(float(adsr["decay_ms"]))
    release_samples, _ = stage_timing(float(adsr["release_ms"]))
    sustain = max(0.0, min(1.0, float(adsr["sustain"])))
    tables = {
        "attack": adsr["attack_table"],
        "decay": adsr["decay_table"],
        "release": adsr["release_table"],
    }
    output = np.zeros(total_samples, dtype=np.float64)
    state, count, level, release_start = "attack", 0, 0.0, 0.0
    for sample in range(total_samples):
        if sample == note_on_samples and state != "idle":
            state, count, release_start = "release", 0, level

        if state == "attack":
            level = _table_value(tables["attack"], count, attack_samples)
            if count >= attack_samples - 1:
                state, count, level = "decay", 0, 1.0
            else:
                count += 1
        elif state == "decay":
            progress = _table_value(tables["decay"], count, decay_samples)
            level = 1.0 - (1.0 - sustain) * progress
            if count >= decay_samples - 1:
                state, count, level = "sustain", 0, sustain
            else:
                count += 1
        elif state == "sustain":
            level = sustain
        elif state == "release":
            progress = _table_value(tables["release"], count, release_samples)
            level = release_start * (1.0 - progress)
            if count >= release_samples - 1:
                state, level = "idle", 0.0
            else:
                count += 1
        else:
            level = 0.0
        output[sample] = max(0.0, min(1.0, level))
    return output


def render_additive_preview(
    preset: dict,
    anchor_index: int = 2,
    hold_ms: float = 900.0,
) -> np.ndarray:
    """Render one note using harmonic decay, ADSR and the pitch envelope."""
    tone = normalise_envelopes(preset)
    anchor = tone["anchors"][max(0, min(3, int(anchor_index)))]
    note = int(anchor["midi_note"])
    base_frequency = 440.0 * 2.0 ** ((note - 69) / 12.0)
    hold_samples = max(1, round(float(hold_ms) * SAMPLE_RATE / 1000.0))
    release_samples, _ = stage_timing(float(tone["adsr"]["release_ms"]))
    total_samples = hold_samples + release_samples + round(0.08 * SAMPLE_RATE)
    time = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE

    pitch = tone["pitch_envelope"]
    pitch_samples, _ = stage_timing(float(pitch["time_ms"]))
    addresses = np.minimum(
        CURVE_POINTS - 1,
        (np.minimum(np.arange(total_samples), pitch_samples - 1) * CURVE_POINTS)
        // max(1, pitch_samples),
    )
    pitch_values = np.asarray(pitch["points_semitones"], dtype=np.float64)[addresses]
    ratios = np.power(2.0, pitch_values / 12.0)
    phase = np.cumsum((2.0 * math.pi * base_frequency / SAMPLE_RATE) * ratios)

    signal = np.zeros(total_samples, dtype=np.float64)
    for harmonic, (amplitude, decay_ms) in enumerate(
        zip(anchor["amplitudes"], anchor["decay_ms"]), start=1
    ):
        amplitude = float(amplitude)
        decay_ms = float(decay_ms)
        relative = (
            np.ones(total_samples, dtype=np.float64)
            if decay_ms <= 0.0
            else np.exp(-time / (decay_ms / 1000.0))
        )
        signal += amplitude * relative * np.sin(harmonic * phase)

    envelope = amplitude_envelope_samples(tone["adsr"], hold_samples, total_samples)
    signal *= envelope
    peak = float(np.max(np.abs(signal))) if signal.size else 0.0
    if peak > 1.0e-9:
        signal *= 0.88 / peak
    return np.rint(np.clip(signal, -1.0, 1.0) * 32767.0).astype(np.int16)


def preview_harmonic_frames(
    preset: dict,
    anchor_index: int = 2,
    hold_ms: float = 900.0,
    frame_rate: float = 30.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return time stamps and the sixteen audible partial levels for preview.

    Each column is ``A_k * PartialDecay_k(t) * ADSR(t)``.  Pitch modulation
    changes the partial frequencies but not these magnitudes, so the returned
    bars stay directly comparable with the editor's initial spectrum.
    """
    tone = normalise_envelopes(preset)
    anchor = tone["anchors"][max(0, min(3, int(anchor_index)))]
    hold_samples = max(1, round(float(hold_ms) * SAMPLE_RATE / 1000.0))
    release_samples, _ = stage_timing(float(tone["adsr"]["release_ms"]))
    total_samples = hold_samples + release_samples + round(0.08 * SAMPLE_RATE)
    frame_count = max(2, int(math.ceil(total_samples / SAMPLE_RATE * frame_rate)) + 1)
    indices = np.minimum(
        total_samples - 1,
        np.rint(np.arange(frame_count, dtype=np.float64) * SAMPLE_RATE / frame_rate).astype(np.int64),
    )
    times = indices.astype(np.float64) / SAMPLE_RATE
    envelope = amplitude_envelope_samples(
        tone["adsr"], hold_samples, total_samples
    )[indices]
    amplitudes = np.asarray(anchor["amplitudes"], dtype=np.float64)
    decay_ms = np.asarray(anchor["decay_ms"], dtype=np.float64)
    relative = np.ones((frame_count, len(amplitudes)), dtype=np.float64)
    decaying = decay_ms > 0.0
    if np.any(decaying):
        relative[:, decaying] = np.exp(
            -times[:, None] / (decay_ms[decaying][None, :] / 1000.0)
        )
    levels = amplitudes[None, :] * relative * envelope[:, None]
    return times, levels

