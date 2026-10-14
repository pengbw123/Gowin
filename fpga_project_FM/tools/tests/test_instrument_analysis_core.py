"""Small deterministic regression tests for the instrument-analysis DSP."""

from __future__ import annotations

import math
import unittest

import numpy as np

from core.instrument_analysis_core import analyse_audio, estimate_fundamental


class InstrumentAnalysisCoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_rate = 48000.0
        self.time = np.arange(int(self.sample_rate * 1.2)) / self.sample_rate
        envelope = np.exp(-self.time / 0.55)
        self.audio = envelope * (
            0.70 * np.sin(2.0 * math.pi * 440.0 * self.time)
            + 0.22 * np.sin(2.0 * math.pi * 880.0 * self.time + 0.3)
            + 0.08 * np.sin(2.0 * math.pi * 1320.0 * self.time - 0.2)
        )

    def test_fundamental_with_expected_pitch(self) -> None:
        measured = estimate_fundamental(self.audio, self.sample_rate, expected_hz=440.0)
        self.assertAlmostEqual(measured, 440.0, delta=1.0)

    def test_harmonic_frequency_and_decay(self) -> None:
        result = analyse_audio(
            self.audio,
            self.sample_rate,
            expected_midi=69.0,
            n_fft=4096,
            hop_length=512,
            max_harmonics=8,
        )
        middle = len(result.times_s) // 3
        self.assertAlmostEqual(result.median_fundamental_hz, 440.0, delta=2.0)
        self.assertAlmostEqual(result.harmonic_frequency_hz[0, middle], 440.0, delta=3.0)
        self.assertAlmostEqual(result.harmonic_frequency_hz[1, middle], 880.0, delta=3.0)
        self.assertIsNotNone(result.harmonic_fits[0].tau_ms)
        self.assertAlmostEqual(result.harmonic_fits[0].tau_ms, 550.0, delta=140.0)

    def test_stft_frames_cover_selection_edges(self) -> None:
        start_s = 0.137
        end_s = 1.071
        hop = 1024
        result = analyse_audio(
            self.audio,
            self.sample_rate,
            selection_start_s=start_s,
            selection_end_s=end_s,
            expected_midi=69.0,
            n_fft=16384,
            hop_length=hop,
            max_harmonics=4,
        )
        # The selectable time should begin exactly at the selection boundary
        # and finish no farther than one hop before its end, independently of
        # the much larger FFT window length.
        self.assertAlmostEqual(result.times_s[0], start_s, places=9)
        self.assertGreaterEqual(result.times_s[-1], end_s - hop / self.sample_rate)
        self.assertLessEqual(result.times_s[-1], end_s + 1.0e-9)


if __name__ == "__main__":
    unittest.main()
