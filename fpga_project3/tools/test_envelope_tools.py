import unittest
from pathlib import Path

import numpy as np

from additive_uart_protocol import load_preset
from envelope_tools import (
    CURVE_POINTS, amplitude_envelope_samples, normalise_envelopes,
    pitch_ratio_q12, preview_harmonic_frames, render_additive_preview,
)


class EnvelopeToolsTests(unittest.TestCase):
    def setUp(self):
        self.preset = load_preset(Path(__file__).with_name("pianotone2.json"))

    def test_tables_are_complete(self):
        tone = normalise_envelopes(self.preset)
        for name in ("attack_table", "decay_table", "release_table"):
            self.assertEqual(len(tone["adsr"][name]), CURVE_POINTS)
        self.assertEqual(len(tone["pitch_envelope"]["points_semitones"]), CURVE_POINTS)

    def test_pitch_q12_ratios(self):
        points = [-12.0, 0.0, 12.0] + [0.0] * (CURVE_POINTS - 3)
        ratios = pitch_ratio_q12(points)
        self.assertEqual(ratios[0], 0x0800)
        self.assertEqual(ratios[1], 0x1000)
        self.assertEqual(ratios[2], 0x2000)

    def test_early_note_off_enters_release(self):
        tone = normalise_envelopes(self.preset)
        envelope = amplitude_envelope_samples(tone["adsr"], 100, 50000)
        self.assertGreater(float(envelope[99]), 0.0)
        self.assertLess(float(envelope[-1]), float(envelope[100]))

    def test_preview_contains_audio(self):
        samples = render_additive_preview(self.preset, anchor_index=2, hold_ms=50)
        self.assertEqual(samples.dtype, np.int16)
        self.assertGreater(int(np.max(np.abs(samples.astype(np.int32)))), 1000)

    def test_preview_harmonic_bars_follow_envelope(self):
        times, levels = preview_harmonic_frames(
            self.preset, anchor_index=2, hold_ms=100, frame_rate=30
        )
        self.assertEqual(levels.shape[1], 16)
        self.assertEqual(len(times), levels.shape[0])
        self.assertAlmostEqual(float(np.max(levels[0])), 0.0, places=7)
        self.assertGreater(float(np.max(levels)), 0.0)
        self.assertLess(float(np.max(levels[-1])), float(np.max(levels)))


if __name__ == "__main__":
    unittest.main()

