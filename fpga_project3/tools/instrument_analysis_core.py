"""Numerical core for the isolated-instrument spectrum analyser.

The GUI lives in :mod:`instrument_spectrum_analyzer`.  Keeping the DSP in this
module makes it possible to regression-test the analysis without importing a
GUI or an audio-file backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy import signal


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def midi_frequency(note: float) -> float:
    return 440.0 * (2.0 ** ((float(note) - 69.0) / 12.0))


def midi_note_name(note: int) -> str:
    note = int(note)
    return "%s%d" % (NOTE_NAMES[note % 12], note // 12 - 1)


def frequency_to_midi(frequency_hz: float) -> float:
    if frequency_hz <= 0:
        return 0.0
    return 69.0 + 12.0 * math.log(frequency_hz / 440.0, 2.0)


@dataclass
class HarmonicFit:
    tau_ms: Optional[float]
    t60_ms: Optional[float]
    r_squared: Optional[float]
    peak_time_s: float
    peak_amplitude: float


@dataclass
class AnalysisResult:
    sample_rate_hz: float
    selection_start_s: float
    selection_end_s: float
    audio: np.ndarray
    times_s: np.ndarray
    frequencies_hz: np.ndarray
    spectrum_complex: np.ndarray
    spectrum_dbfs: np.ndarray
    fundamental_hz: np.ndarray
    harmonic_frequency_hz: np.ndarray
    harmonic_amplitude: np.ndarray
    harmonic_amplitude_relative: np.ndarray
    harmonic_db: np.ndarray
    harmonic_phase_rad: np.ndarray
    harmonic_fits: List[HarmonicFit]
    n_fft: int
    hop_length: int

    @property
    def median_fundamental_hz(self) -> float:
        valid = self.fundamental_hz[np.isfinite(self.fundamental_hz)]
        return float(np.median(valid)) if valid.size else 0.0

    @property
    def estimated_midi(self) -> float:
        return frequency_to_midi(self.median_fundamental_hz)


def _next_power_of_two(value: int) -> int:
    return 1 << max(1, int(value - 1).bit_length())


def _parabolic_peak(log_magnitude: np.ndarray, index: int) -> Tuple[float, float]:
    """Return fractional-bin offset and interpolated log magnitude."""
    if index <= 0 or index >= len(log_magnitude) - 1:
        return 0.0, float(log_magnitude[index])
    left = float(log_magnitude[index - 1])
    center = float(log_magnitude[index])
    right = float(log_magnitude[index + 1])
    denominator = left - 2.0 * center + right
    if abs(denominator) < 1.0e-15:
        return 0.0, center
    offset = 0.5 * (left - right) / denominator
    offset = max(-0.5, min(0.5, offset))
    height = center - 0.25 * (left - right) * offset
    return offset, height


def estimate_fundamental(
    audio: np.ndarray,
    sample_rate_hz: float,
    expected_hz: Optional[float] = None,
    minimum_hz: float = 20.0,
    maximum_hz: float = 5000.0,
) -> float:
    """Estimate one fundamental frequency using a weighted harmonic score.

    A supplied expected frequency narrows the search to +/- one semitone.  This
    is deliberately supported because a plucked string can have a weak
    fundamental and a stronger second or third partial.
    """
    values = np.asarray(audio, dtype=np.float64)
    if values.size < 32:
        raise ValueError("audio selection is too short")
    values = values - float(np.mean(values))

    # Ignore the first few milliseconds of a percussive attack when possible.
    skip = min(values.size // 8, int(round(0.015 * sample_rate_hz)))
    usable = values[skip:]
    wanted = min(usable.size, int(round(1.5 * sample_rate_hz)))
    usable = usable[:wanted]
    if usable.size < 32:
        usable = values

    n_fft = _next_power_of_two(max(4096, usable.size * 2))
    n_fft = min(n_fft, 262144)
    windowed = usable[: min(usable.size, n_fft)] * signal.windows.hann(
        min(usable.size, n_fft), sym=False
    )
    spectrum = np.abs(np.fft.rfft(windowed, n=n_fft)) + 1.0e-15
    frequencies = np.fft.rfftfreq(n_fft, 1.0 / sample_rate_hz)

    if expected_hz and expected_hz > 0:
        lower = max(minimum_hz, expected_hz / (2.0 ** (1.0 / 12.0)))
        upper = min(maximum_hz, expected_hz * (2.0 ** (1.0 / 12.0)))
    else:
        lower, upper = minimum_hz, maximum_hz

    # A fine continuous grid avoids binding the result to one FFT bin.
    step_hz = max(0.08, sample_rate_hz / n_fft / 4.0)
    candidates = np.arange(lower, upper + step_hz, step_hz)
    scores = np.zeros(candidates.shape, dtype=np.float64)
    nyquist = sample_rate_hz * 0.48
    normalized_spectrum = spectrum / max(float(np.max(spectrum)), 1.0e-15)
    for harmonic in range(1, 13):
        target = candidates * harmonic
        valid = target < nyquist
        if not np.any(valid):
            break
        weight = 1.0 / math.sqrt(float(harmonic))
        scores[valid] += weight * np.interp(
            target[valid], frequencies, normalized_spectrum
        )
    best = float(candidates[int(np.argmax(scores))])
    return best


def _track_fundamental(
    magnitude: np.ndarray,
    frequencies_hz: np.ndarray,
    nominal_hz: float,
    sample_rate_hz: float,
) -> np.ndarray:
    """Track small pitch motion while resisting octave errors."""
    frames = magnitude.shape[1]
    tracked = np.full(frames, nominal_hz, dtype=np.float64)
    nyquist = sample_rate_hz * 0.48
    candidates = nominal_hz * np.linspace(0.97, 1.03, 41)
    bin_width = float(frequencies_hz[1] - frequencies_hz[0])
    for frame in range(frames):
        column = magnitude[:, frame]
        column = column / max(float(np.max(column)), 1.0e-15)
        scores = np.zeros(candidates.shape, dtype=np.float64)
        for harmonic in range(1, 9):
            target = candidates * harmonic
            valid = target < nyquist
            if not np.any(valid):
                break
            scores[valid] += (1.0 / math.sqrt(float(harmonic))) * np.interp(
                target[valid], frequencies_hz, column
            )
        coarse = float(candidates[int(np.argmax(scores))])
        # Refine with the actual peaks of several partials.  This removes the
        # several-Hz quantisation that a 4096-point FFT would otherwise impose
        # on a low guitar fundamental.
        estimates = []
        weights = []
        log_column = np.log(np.maximum(column, 1.0e-15))
        for harmonic in range(1, 9):
            predicted = coarse * harmonic
            if predicted >= nyquist:
                break
            radius = max(bin_width, min(nominal_hz * 0.20, 120.0))
            center_bin = int(round(predicted / bin_width))
            radius_bins = max(1, int(round(radius / bin_width)))
            left = max(1, center_bin - radius_bins)
            right = min(len(column) - 2, center_bin + radius_bins)
            if right < left:
                continue
            peak_bin = left + int(np.argmax(column[left : right + 1]))
            peak_value = float(column[peak_bin])
            if peak_value < 0.002:
                continue
            offset, _height = _parabolic_peak(log_column, peak_bin)
            estimates.append(((peak_bin + offset) * bin_width) / harmonic)
            weights.append(peak_value / math.sqrt(float(harmonic)))
        if estimates:
            refined = float(np.average(np.asarray(estimates), weights=np.asarray(weights)))
            tracked[frame] = min(nominal_hz * 1.03, max(nominal_hz * 0.97, refined))
        else:
            tracked[frame] = coarse

    # Remove one-frame jumps without suppressing a slow guitar pitch droop.
    if frames >= 5:
        tracked = signal.medfilt(tracked, kernel_size=5)
    return tracked


def _fit_decay(times_s: np.ndarray, amplitudes: np.ndarray) -> HarmonicFit:
    amplitudes = np.asarray(amplitudes, dtype=np.float64)
    if not amplitudes.size or not np.any(np.isfinite(amplitudes)):
        return HarmonicFit(None, None, None, 0.0, 0.0)
    finite_amp = np.where(np.isfinite(amplitudes), amplitudes, 0.0)
    peak_index = int(np.argmax(finite_amp))
    peak = float(finite_amp[peak_index])
    peak_time = float(times_s[peak_index]) if times_s.size else 0.0
    if peak <= 1.0e-12:
        return HarmonicFit(None, None, None, peak_time, peak)

    threshold = peak * 0.01
    indices = np.arange(peak_index, finite_amp.size)
    indices = indices[finite_amp[indices] >= threshold]
    if indices.size < 6:
        return HarmonicFit(None, None, None, peak_time, peak)
    # Keep one continuous section. Noise later in the file must not extend the
    # fit after the partial has already disappeared.
    breaks = np.where(np.diff(indices) > 1)[0]
    if breaks.size:
        indices = indices[: breaks[0] + 1]
    if indices.size < 6:
        return HarmonicFit(None, None, None, peak_time, peak)

    x = times_s[indices] - times_s[indices[0]]
    y = np.log(np.maximum(finite_amp[indices], peak * 1.0e-6))
    slope, intercept = np.polyfit(x, y, 1)
    predicted = slope * x + intercept
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - float(np.mean(y))) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1.0e-15 else 1.0
    if slope >= -1.0e-6:
        return HarmonicFit(None, None, r_squared, peak_time, peak)
    tau_s = -1.0 / float(slope)
    tau_ms = tau_s * 1000.0
    return HarmonicFit(tau_ms, tau_ms * math.log(1000.0), r_squared, peak_time, peak)


def analyse_audio(
    audio: np.ndarray,
    sample_rate_hz: float,
    selection_start_s: float = 0.0,
    selection_end_s: Optional[float] = None,
    expected_midi: Optional[float] = None,
    n_fft: int = 4096,
    hop_length: Optional[int] = None,
    max_harmonics: int = 32,
) -> AnalysisResult:
    """Analyse one isolated musical note.

    ``audio`` must already be mono.  Harmonic magnitudes are tracked around
    integer multiples of the frame-wise fundamental, with local peak and
    parabolic-bin refinement rather than a nearest-bin lookup.
    """
    if sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive")
    if n_fft < 256 or n_fft & (n_fft - 1):
        raise ValueError("FFT length must be a power of two and at least 256")
    if max_harmonics < 1 or max_harmonics > 64:
        raise ValueError("harmonic count must be between 1 and 64")
    hop = int(hop_length or n_fft // 8)
    if hop < 1 or hop > n_fft:
        raise ValueError("invalid hop length")

    source = np.asarray(audio, dtype=np.float64).reshape(-1)
    duration = source.size / float(sample_rate_hz)
    end_s = duration if selection_end_s is None else min(duration, float(selection_end_s))
    start_s = max(0.0, float(selection_start_s))
    if end_s <= start_s:
        raise ValueError("selection end must be after selection start")
    first = int(round(start_s * sample_rate_hz))
    last = int(round(end_s * sample_rate_hz))
    selected = source[first:last].copy()
    if selected.size < 32:
        raise ValueError("selected audio is too short")
    selected -= float(np.mean(selected))
    if np.max(np.abs(selected)) < 1.0e-9:
        raise ValueError("selected audio is silent")
    if selected.size < n_fft:
        selected = np.pad(selected, (0, n_fft - selected.size), mode="constant")

    expected_hz = midi_frequency(expected_midi) if expected_midi is not None else None
    nominal_hz = estimate_fundamental(selected, sample_rate_hz, expected_hz=expected_hz)

    frequencies, relative_times, zxx = signal.stft(
        selected,
        fs=sample_rate_hz,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop,
        nfft=n_fft,
        boundary=None,
        padded=False,
    )
    magnitude = np.abs(zxx)
    # scipy.signal.stft with a Hann window reports a sinusoid close to half its
    # peak amplitude.  Doubling gives a useful approximately dBFS scale.
    magnitude_for_dbfs = magnitude * 2.0
    spectrum_dbfs = 20.0 * np.log10(np.maximum(magnitude_for_dbfs, 1.0e-12))
    fundamental = _track_fundamental(magnitude, frequencies, nominal_hz, sample_rate_hz)
    times = relative_times + start_s

    frame_count = magnitude.shape[1]
    harmonic_frequency = np.full((max_harmonics, frame_count), np.nan, dtype=np.float64)
    harmonic_amplitude = np.zeros((max_harmonics, frame_count), dtype=np.float64)
    harmonic_phase = np.full((max_harmonics, frame_count), np.nan, dtype=np.float64)
    bin_width = sample_rate_hz / float(n_fft)
    nyquist_limit = sample_rate_hz * 0.48

    for frame in range(frame_count):
        column = magnitude[:, frame]
        log_column = np.log(np.maximum(column, 1.0e-15))
        f0 = float(fundamental[frame])
        for harmonic in range(1, max_harmonics + 1):
            predicted_hz = harmonic * f0
            if predicted_hz >= nyquist_limit:
                continue
            radius_hz = max(bin_width, min(f0 * 0.22, 120.0))
            center_bin = int(round(predicted_hz / bin_width))
            radius_bins = max(1, int(round(radius_hz / bin_width)))
            left = max(1, center_bin - radius_bins)
            right = min(len(column) - 2, center_bin + radius_bins)
            if right < left:
                continue
            peak_bin = left + int(np.argmax(column[left : right + 1]))
            offset, log_height = _parabolic_peak(log_column, peak_bin)
            harmonic_frequency[harmonic - 1, frame] = (peak_bin + offset) * bin_width
            harmonic_amplitude[harmonic - 1, frame] = math.exp(log_height)
            harmonic_phase[harmonic - 1, frame] = float(np.angle(zxx[peak_bin, frame]))

    reference = float(np.max(harmonic_amplitude))
    if reference <= 1.0e-15:
        reference = 1.0
    relative = harmonic_amplitude / reference
    harmonic_db = 20.0 * np.log10(np.maximum(relative, 1.0e-9))
    fits = [_fit_decay(times, harmonic_amplitude[index]) for index in range(max_harmonics)]

    return AnalysisResult(
        sample_rate_hz=float(sample_rate_hz),
        selection_start_s=start_s,
        selection_end_s=end_s,
        audio=selected,
        times_s=times,
        frequencies_hz=frequencies,
        spectrum_complex=zxx,
        spectrum_dbfs=spectrum_dbfs,
        fundamental_hz=fundamental,
        harmonic_frequency_hz=harmonic_frequency,
        harmonic_amplitude=harmonic_amplitude,
        harmonic_amplitude_relative=relative,
        harmonic_db=harmonic_db,
        harmonic_phase_rad=harmonic_phase,
        harmonic_fits=fits,
        n_fft=n_fft,
        hop_length=hop,
    )
