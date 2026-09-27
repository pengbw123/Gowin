import math
import tempfile
import wave
from pathlib import Path

import numpy as np

from tone_model import (
    FILTER_LUT_POINTS,
    SAMPLE_RATE,
    WAVETABLE_POINTS,
    cutoff_to_lut_index,
    filter_alpha_lut,
)


def _curve_segment(progress, curve):
    progress = np.clip(progress, 0.0, 1.0)
    exponent = 2.0 ** (-2.0 * float(curve))
    return np.power(progress, exponent)


def build_envelope(settings, note_on_samples, total_samples):
    result = np.zeros(total_samples, dtype=np.float64)
    attack = max(1, int(round(settings.attack_ms * SAMPLE_RATE / 1000.0)))
    decay = max(1, int(round(settings.decay_ms * SAMPLE_RATE / 1000.0)))
    release = max(1, int(round(settings.release_ms * SAMPLE_RATE / 1000.0)))
    sustain = max(0.0, min(1.0, float(settings.sustain)))

    attack_start = 0
    attack_end = min(note_on_samples, attack)
    if attack_end > attack_start:
        p = np.arange(attack_end - attack_start, dtype=np.float64) / max(1, attack - 1)
        result[attack_start:attack_end] = _curve_segment(p, settings.attack_curve)

    decay_start = attack
    decay_end = min(note_on_samples, decay_start + decay)
    if decay_end > decay_start:
        p = np.arange(decay_end - decay_start, dtype=np.float64) / max(1, decay - 1)
        result[decay_start:decay_end] = 1.0 + (sustain - 1.0) * _curve_segment(p, settings.decay_curve)

    sustain_start = attack + decay
    if note_on_samples > sustain_start:
        result[sustain_start:note_on_samples] = sustain

    release_start_value = result[note_on_samples - 1] if note_on_samples > 0 else 0.0
    release_count = min(release, max(0, total_samples - note_on_samples))
    if release_count:
        p = np.arange(release_count, dtype=np.float64) / max(1, release - 1)
        release_shape = 1.0 - _curve_segment(p, settings.release_curve)
        result[note_on_samples:note_on_samples + release_count] = release_start_value * release_shape
    return result


def filter_cutoff_curve(control, minimum_hz, maximum_hz):
    """Map a 0..1 filter envelope to the exact cutoff bins used by FPGA v1."""
    frequencies, _ = filter_alpha_lut()
    min_index = cutoff_to_lut_index(minimum_hz)
    max_index = cutoff_to_lut_index(maximum_hz)
    control_q16 = np.rint(np.clip(control, 0.0, 1.0) * 65535.0).astype(np.int64)
    indices = min_index + ((control_q16 * (max_index - min_index)) >> 16)
    indices = np.clip(indices, 0, FILTER_LUT_POINTS - 1)
    return frequencies[indices]


def _oscillator_fixed(wavetable, frequency, samples):
    table = np.asarray(wavetable, dtype=np.int16)
    phase = 0
    phase_inc = int(round(float(frequency) * (2 ** 32) / SAMPLE_RATE))
    shift = 32 - int(round(math.log(WAVETABLE_POINTS, 2)))
    output = np.empty(samples, dtype=np.int32)
    for i in range(samples):
        output[i] = int(table[(phase >> shift) & (WAVETABLE_POINTS - 1)])
        phase = (phase + phase_inc) & 0xFFFFFFFF
    return output


def _filter_fixed(samples, control, minimum_hz, maximum_hz):
    _, alpha_lut = filter_alpha_lut()
    min_index = cutoff_to_lut_index(minimum_hz)
    max_index = cutoff_to_lut_index(maximum_hz)
    y = 0
    output = np.empty(len(samples), dtype=np.int32)
    control_q16 = np.rint(np.clip(control, 0.0, 1.0) * 65535.0).astype(np.int64)
    for i, x in enumerate(samples):
        index = min_index + ((int(control_q16[i]) * (max_index - min_index)) >> 16)
        index = max(0, min(FILTER_LUT_POINTS - 1, index))
        alpha = int(alpha_lut[index])
        y += ((int(x) - y) * alpha) >> 16
        y = max(-32768, min(32767, y))
        output[i] = y
    return output


def render_tone(tone, frequency=261.625565, hold_seconds=1.8, fixed_point=True):
    tone.ensure_valid()
    release_seconds = max(0.08, tone.amplitude_envelope.release_ms / 1000.0)
    total_seconds = hold_seconds + release_seconds + 0.08
    total_samples = int(round(total_seconds * SAMPLE_RATE))
    note_on_samples = int(round(hold_seconds * SAMPLE_RATE))
    amp_env = build_envelope(tone.amplitude_envelope, note_on_samples, total_samples)

    if fixed_point:
        signal = _oscillator_fixed(tone.wavetable, frequency, total_samples)
        if tone.filter.enabled:
            if tone.filter.follow_amplitude_envelope:
                filter_control = amp_env * tone.filter.envelope_amount
            else:
                filter_control = build_envelope(tone.filter.envelope, note_on_samples, total_samples)
                filter_control *= tone.filter.envelope_amount
            signal = _filter_fixed(signal, filter_control, tone.filter.min_cutoff_hz, tone.filter.max_cutoff_hz)
        envelope_q16 = np.rint(amp_env * 65535.0).astype(np.int64)
        output = (signal.astype(np.int64) * envelope_q16) >> 16
        output = np.rint(output * tone.master_gain).clip(-32768, 32767).astype(np.int16)
        return output

    phase = np.mod(np.arange(total_samples, dtype=np.float64) * frequency / SAMPLE_RATE, 1.0)
    table = np.asarray(tone.wavetable, dtype=np.float64) / 32768.0
    positions = phase * WAVETABLE_POINTS
    left = np.floor(positions).astype(np.int64) & (WAVETABLE_POINTS - 1)
    right = (left + 1) & (WAVETABLE_POINTS - 1)
    fraction = positions - np.floor(positions)
    signal = table[left] * (1.0 - fraction) + table[right] * fraction
    if tone.filter.enabled:
        control = amp_env if tone.filter.follow_amplitude_envelope else build_envelope(
            tone.filter.envelope, note_on_samples, total_samples
        )
        min_idx = cutoff_to_lut_index(tone.filter.min_cutoff_hz)
        max_idx = cutoff_to_lut_index(tone.filter.max_cutoff_hz)
        frequencies, _ = filter_alpha_lut()
        state = 0.0
        filtered = np.empty(total_samples, dtype=np.float64)
        for i, x in enumerate(signal):
            idx = min_idx + int(max(0.0, min(1.0, control[i] * tone.filter.envelope_amount)) * (max_idx - min_idx))
            alpha = 1.0 - math.exp(-2.0 * math.pi * frequencies[idx] / SAMPLE_RATE)
            state += alpha * (x - state)
            filtered[i] = state
        signal = filtered
    return np.rint(signal * amp_env * tone.master_gain * 32767.0).clip(-32768, 32767).astype(np.int16)


def write_wav(samples, path):
    path = Path(path)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(round(SAMPLE_RATE)))
        wav_file.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def write_preview_temp(samples):
    handle = tempfile.NamedTemporaryFile(prefix="gowin_tone_", suffix=".wav", delete=False)
    handle.close()
    write_wav(samples, handle.name)
    return handle.name
