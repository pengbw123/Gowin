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

FILTER_POINTS = 64
FILTER_MIN_HZ = 80.0
FILTER_MAX_HZ = 12_000.0


def _default_filter_points(anchor_index: int) -> np.ndarray:
    start_hz = (7200.0, 8200.0, 9500.0, 11_000.0)[anchor_index]
    end_hz = (550.0, 700.0, 900.0, 1400.0)[anchor_index]
    progress = np.arange(FILTER_POINTS, dtype=np.float64) / (FILTER_POINTS - 1)
    return start_hz * np.power(end_hz / start_hz, np.power(progress, 0.62))


def _preview_filter(preset: dict, anchor_index: int, total_samples: int) -> tuple[np.ndarray, int]:
    """Return the selected anchor's cutoff envelope and pole count.

    This deliberately mirrors the FPGA representation: a 64-point cutoff
    curve advances once per audio sample, then holds its last point.  Missing
    filter data means bypass, which keeps old preset files playable.
    """
    raw = preset.get("dynamic_filter", {})
    if not bool(raw.get("enabled", True)):
        return np.full(total_samples, FILTER_MAX_HZ), 0
    anchors = raw.get("anchors", [])
    selected = max(0, min(3, int(anchor_index)))
    source = anchors[selected] if selected < len(anchors) else {}
    points = np.asarray(source.get("cutoff_hz", []), dtype=np.float64)
    if points.size != FILTER_POINTS:
        points = _default_filter_points(selected)
    time_ms = max(0.1, float(source.get("time_ms", 1400.0)))
    duration_samples = max(1, round(time_ms * SAMPLE_RATE / 1000.0))
    addresses = np.minimum(
        FILTER_POINTS - 1,
        (np.minimum(np.arange(total_samples), duration_samples - 1) * FILTER_POINTS)
        // duration_samples,
    )
    cutoff = np.clip(points[addresses], FILTER_MIN_HZ, FILTER_MAX_HZ)
    poles = 2 if int(raw.get("slope_db_oct", 12)) >= 12 else 1
    return cutoff, poles


def _apply_dynamic_lowpass(signal: np.ndarray, cutoff: np.ndarray, poles: int) -> np.ndarray:
    """Apply the same time-varying one/two-pole filter used by the RTL."""
    if poles <= 0 or signal.size == 0:
        return signal
    alpha = 1.0 - np.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)
    output = np.empty_like(signal)
    y1 = 0.0
    y2 = 0.0
    if poles == 1:
        for index, sample in enumerate(signal):
            y1 += alpha[index] * (sample - y1)
            output[index] = y1
    else:
        for index, sample in enumerate(signal):
            y1 += alpha[index] * (sample - y1)
            y2 += alpha[index] * (y1 - y2)
            output[index] = y2
    return output


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
    """Render one note using the FPGA's ADSR, pitch and dynamic-filter path."""
    tone = normalise_envelopes(preset)
    anchor = tone["anchors"][max(0, min(3, int(anchor_index)))]
    note = int(anchor["midi_note"])
    base_frequency = 440.0 * 2.0 ** ((note - 69) / 12.0)
    hold_samples = max(1, round(float(hold_ms) * SAMPLE_RATE / 1000.0))
    release_samples, _ = stage_timing(float(tone["adsr"]["release_ms"]))
    total_samples = hold_samples + release_samples + round(0.08 * SAMPLE_RATE)
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
    for harmonic, amplitude in enumerate(anchor["amplitudes"], start=1):
        signal += float(amplitude) * np.sin(harmonic * phase)

    cutoff, poles = _preview_filter(tone, anchor_index, total_samples)
    signal = _apply_dynamic_lowpass(signal, cutoff, poles)

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
    """Return the sixteen estimated audible partial levels during preview.

    The bars use the exact magnitude response of the FPGA one/two-pole filter
    at each harmonic frequency, multiplied by the common ADSR.
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
    cutoff, poles = _preview_filter(tone, anchor_index, total_samples)
    if poles <= 0:
        response = np.ones((frame_count, len(amplitudes)), dtype=np.float64)
    else:
        base_frequency = 440.0 * 2.0 ** ((int(anchor["midi_note"]) - 69) / 12.0)
        frequencies = base_frequency * np.arange(1, len(amplitudes) + 1, dtype=np.float64)
        frame_cutoff = cutoff[indices]
        alpha = 1.0 - np.exp(-2.0 * math.pi * frame_cutoff[:, None] / SAMPLE_RATE)
        omega = 2.0 * math.pi * frequencies[None, :] / SAMPLE_RATE
        denominator = np.sqrt(
            1.0 + (1.0 - alpha) ** 2
            - 2.0 * (1.0 - alpha) * np.cos(omega)
        )
        response = alpha / np.maximum(denominator, 1.0e-12)
        if poles >= 2:
            response *= response
    levels = amplitudes[None, :] * response * envelope[:, None]
    return times, levels

