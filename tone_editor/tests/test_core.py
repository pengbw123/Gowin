import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from audio_engine import build_envelope, filter_cutoff_curve, render_tone
from fpga_export import export_tone
from presets import build_presets
from tone_model import Envelope, SAMPLE_RATE, Tone, WAVETABLE_POINTS, filter_alpha_lut, normalize_wavetable


class ToneEditorTests(unittest.TestCase):
    def test_presets_are_valid_q15_tables(self):
        presets = build_presets()
        self.assertGreaterEqual(len(presets), 5)
        for tone in presets.values():
            self.assertEqual(len(tone.wavetable), WAVETABLE_POINTS)
            self.assertGreater(max(tone.wavetable), 1000)
            self.assertLess(min(tone.wavetable), -1000)
            self.assertLessEqual(max(tone.wavetable), 32767)
            self.assertGreaterEqual(min(tone.wavetable), -32768)

    def test_filter_lut_is_monotonic(self):
        frequencies, values = filter_alpha_lut()
        self.assertEqual(len(values), 256)
        self.assertTrue(np.all(np.diff(frequencies) > 0))
        self.assertTrue(np.all(np.diff(values.astype(np.int64)) >= 0))
        self.assertGreater(int(values[-1]), int(values[0]))

    def test_filter_chart_uses_fpga_cutoff_bins(self):
        control = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        cutoff = filter_cutoff_curve(control, 700.0, 12000.0)
        self.assertTrue(np.all(np.diff(cutoff) >= 0))
        self.assertGreaterEqual(float(cutoff[0]), 600.0)
        self.assertLessEqual(float(cutoff[0]), 800.0)
        self.assertGreaterEqual(float(cutoff[-1]), 10000.0)
        self.assertLessEqual(float(cutoff[-1]), 13000.0)

    def test_normalize_preserves_q15_scale(self):
        source = np.linspace(-1000, 500, WAVETABLE_POINTS)
        normalized = normalize_wavetable(source)
        self.assertGreater(int(np.max(normalized)), 30000)
        self.assertLess(int(np.min(normalized)), -30000)
        self.assertLess(abs(float(np.mean(normalized))), 2.0)

    def test_fixed_point_preview_is_audible(self):
        tone = build_presets()["钢琴（合成）"]
        samples = render_tone(tone, frequency=261.6256, hold_seconds=0.08, fixed_point=True)
        self.assertEqual(samples.dtype, np.int16)
        self.assertGreater(len(samples), 1000)
        self.assertGreater(int(np.max(np.abs(samples.astype(np.int32)))), 100)

    def test_vital_style_gate_and_adsr_stages(self):
        sample_ms = 1000.0 / SAMPLE_RATE
        envelope = Envelope(
            attack_ms=4 * sample_ms,
            decay_ms=4 * sample_ms,
            sustain=0.4,
            release_ms=5 * sample_ms,
            attack_curve=0.0,
            decay_curve=0.0,
            release_curve=0.0,
        )
        gate_off = 12
        values = build_envelope(envelope, gate_off, 20)
        self.assertAlmostEqual(float(values[0]), 0.0, places=6)
        self.assertAlmostEqual(float(values[3]), 1.0, places=6)
        self.assertAlmostEqual(float(values[7]), 0.4, places=6)
        self.assertTrue(np.allclose(values[8:gate_off], 0.4))
        self.assertAlmostEqual(float(values[gate_off]), 0.4, places=6)
        self.assertAlmostEqual(float(values[gate_off + 4]), 0.0, places=6)

    def test_gate_release_interrupts_attack_from_current_level(self):
        sample_ms = 1000.0 / SAMPLE_RATE
        envelope = Envelope(
            attack_ms=10 * sample_ms,
            decay_ms=10 * sample_ms,
            sustain=0.5,
            release_ms=5 * sample_ms,
        )
        gate_off = 5
        values = build_envelope(envelope, gate_off, 14)
        current = float(values[gate_off - 1])
        self.assertGreater(current, 0.0)
        self.assertLess(current, 1.0)
        self.assertAlmostEqual(float(values[gate_off]), current, places=6)
        self.assertTrue(np.all(np.diff(values[gate_off:gate_off + 5]) <= 1e-12))
        self.assertAlmostEqual(float(values[gate_off + 4]), 0.0, places=6)

    def test_json_roundtrip_and_fpga_export(self):
        tone = build_presets()["小提琴（合成）"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "violin.tone.json"
            tone.save(config)
            loaded = Tone.load(config)
            self.assertEqual(loaded.name, tone.name)
            self.assertEqual(loaded.wavetable, tone.wavetable)
            outputs = export_tone(loaded, root / "fpga")
            for output in outputs:
                self.assertTrue(output.exists())
            wavetable_mem = outputs[0].read_text(encoding="ascii").splitlines()
            alpha_mem = outputs[1].read_text(encoding="ascii").splitlines()
            self.assertEqual(len(wavetable_mem), WAVETABLE_POINTS)
            self.assertEqual(len(alpha_mem), 256)
            self.assertTrue(all(len(line) == 4 for line in wavetable_mem))
            manifest = json.loads(outputs[3].read_text(encoding="utf-8"))
            self.assertEqual(manifest["wavetable_format"], "signed-q1.15-two-complement-hex")
            self.assertEqual(manifest["envelope_logic"], "gate-driven-attack-decay-sustain-release")
            parameters = outputs[2].read_text(encoding="utf-8")
            self.assertNotIn("DELAY_SAMPLES", parameters)
            self.assertNotIn("HOLD_SAMPLES", parameters)
            self.assertIn("TONE_FILTER_RELEASE_PHASE_INC", parameters)
            for curve_file in outputs[4:]:
                self.assertEqual(len(curve_file.read_text(encoding="ascii").splitlines()), 256)


if __name__ == "__main__":
    unittest.main()
