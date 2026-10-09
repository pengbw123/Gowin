"""Phase-2 extraction of FPGA-ready relative harmonic decay parameters.

The phase-1 analyser measures absolute partial magnitudes H_k(t).  The FPGA,
however, already multiplies every partial by a common ADSR envelope.  Phase 2
therefore factors the recording into a common energy envelope G(t) and a
relative timbre trajectory R_k(t) = H_k(t) / G(t).  Only R_k is fitted to the
per-partial exponential decay used by ``midi_poly_synth.v``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional

import numpy as np

from instrument_analysis_core import AnalysisResult, analyse_audio


FPGA_SAMPLE_RATE = 48_828.125
Q24_MAX = (1 << 24) - 1


@dataclass
class RelativeDecayFit:
    tau_ms: Optional[float]
    alpha_q24: int
    slope_per_s: float
    r_squared: Optional[float]
    clamped_growth: bool


@dataclass
class AnchorTimbre:
    name: str
    midi_note: int
    result: AnalysisResult
    reference_frame: int
    amplitudes: np.ndarray
    global_envelope: np.ndarray
    relative_envelopes: np.ndarray
    relative_fits: List[RelativeDecayFit]


def tau_ms_to_alpha_q24(tau_ms: Optional[float],
                        sample_rate_hz: float = FPGA_SAMPLE_RATE) -> int:
    """Convert a relative exponential time constant to the RTL Q0.24 alpha."""
    if tau_ms is None or tau_ms <= 0.0:
        return 0
    tau_samples = tau_ms * sample_rate_hz / 1000.0
    alpha = 1.0 - math.exp(-1.0 / tau_samples)
    return max(1, min(Q24_MAX, int(round(alpha * Q24_MAX))))


def _fit_relative_decay(times_s: np.ndarray, relative: np.ndarray,
                        global_envelope: np.ndarray,
                        reference_frame: int) -> RelativeDecayFit:
    peak_global = float(np.max(global_envelope))
    if peak_global <= 1.0e-15:
        return RelativeDecayFit(None, 0, 0.0, None, False)

    indices = np.arange(reference_frame, len(times_s))
    valid = (
        np.isfinite(relative[indices]) &
        (relative[indices] > 1.0e-8) &
        (global_envelope[indices] >= peak_global * 0.01)
    )
    indices = indices[valid]
    if indices.size < 6:
        return RelativeDecayFit(None, 0, 0.0, None, False)

    # Stop at the first long gap; late noise must not masquerade as a decay.
    gaps = np.where(np.diff(indices) > 1)[0]
    if gaps.size:
        indices = indices[: gaps[0] + 1]
    if indices.size < 6:
        return RelativeDecayFit(None, 0, 0.0, None, False)

    x = times_s[indices] - times_s[indices[0]]
    y = np.log(np.maximum(relative[indices], 1.0e-10))
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1.0e-15 else 1.0

    # The current RTL can hold or decay a partial, but cannot make it grow
    # relative to the common ADSR.  Report this explicitly instead of turning
    # a positive slope into a misleading decay coefficient.
    if slope >= -1.0e-6:
        return RelativeDecayFit(None, 0, float(slope), r_squared,
                                bool(slope > 1.0e-6))
    tau_ms = -1000.0 / float(slope)
    return RelativeDecayFit(
        tau_ms,
        tau_ms_to_alpha_q24(tau_ms),
        float(slope),
        r_squared,
        False,
    )


def extract_anchor_timbre(audio: np.ndarray, sample_rate_hz: float,
                          name: str, midi_note: int,
                          selection_start_s: float = 0.0,
                          selection_end_s: Optional[float] = None,
                          n_fft: int = 8192,
                          max_harmonics: int = 32) -> AnchorTimbre:
    result = analyse_audio(
        audio,
        sample_rate_hz,
        selection_start_s=selection_start_s,
        selection_end_s=selection_end_s,
        expected_midi=float(midi_note),
        n_fft=n_fft,
        hop_length=n_fft // 8,
        max_harmonics=max_harmonics,
    )
    harmonic_amplitude = np.maximum(result.harmonic_amplitude, 0.0)
    global_envelope = np.sqrt(np.sum(harmonic_amplitude ** 2, axis=0))
    reference_frame = int(np.argmax(global_envelope))
    safe_global = np.maximum(global_envelope, np.max(global_envelope) * 1.0e-9)
    relative = harmonic_amplitude / safe_global[np.newaxis, :]

    # Median a few onset frames so one FFT-bin fluctuation does not define the
    # complete FPGA timbre.  The L1 normalisation leaves headroom for mixing.
    left = reference_frame
    right = min(relative.shape[1], reference_frame + 3)
    amplitudes = np.median(relative[:, left:right], axis=1)
    amplitudes = np.maximum(amplitudes, 0.0)
    amplitude_sum = float(np.sum(amplitudes))
    if amplitude_sum > 1.0e-12:
        amplitudes = amplitudes * (0.92 / amplitude_sum)

    fits = [
        _fit_relative_decay(result.times_s, relative[index], global_envelope,
                            reference_frame)
        for index in range(max_harmonics)
    ]
    return AnchorTimbre(
        name=name,
        midi_note=midi_note,
        result=result,
        reference_frame=reference_frame,
        amplitudes=amplitudes,
        global_envelope=global_envelope,
        relative_envelopes=relative,
        relative_fits=fits,
    )


def anchor_to_json(anchor: AnchorTimbre, harmonic_count: int = 16) -> dict:
    count = min(harmonic_count, len(anchor.amplitudes))
    # The existing UART command intentionally accepts at most 120 seconds.
    # Slower fitted changes are indistinguishable from an almost-held partial
    # at Q0.24 resolution, so clamp only the FPGA-facing value and retain the
    # raw slope/R² below for diagnosis.
    export_tau_ms = [
        0.0 if fit.tau_ms is None else min(120_000.0, float(fit.tau_ms))
        for fit in anchor.relative_fits[:count]
    ]
    return {
        "name": anchor.name,
        "midi_note": anchor.midi_note,
        "reference_time_s": float(anchor.result.times_s[anchor.reference_frame]),
        "amplitudes": [round(float(value), 8) for value in anchor.amplitudes[:count]],
        "decay_ms": [round(value, 3) for value in export_tau_ms],
        "decay_alpha_q24": [
            tau_ms_to_alpha_q24(value) for value in export_tau_ms
        ],
        "relative_decay_fit": [
            {
                "slope_per_s": round(float(fit.slope_per_s), 8),
                "r_squared": (None if fit.r_squared is None
                              else round(float(fit.r_squared), 6)),
                "clamped_growth": bool(fit.clamped_growth),
            }
            for fit in anchor.relative_fits[:count]
        ],
    }
