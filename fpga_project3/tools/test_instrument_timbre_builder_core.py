from __future__ import annotations

import math
import unittest

import numpy as np

from instrument_timbre_builder_core import (
    FPGA_SAMPLE_RATE,
    _fit_relative_decay,
    tau_ms_to_alpha_q24,
)


class TimbreBuilderCoreTest(unittest.TestCase):
    def test_relative_decay_removes_common_envelope(self) -> None:
        times = np.arange(0.0, 2.0, 0.01)
        common = np.exp(-times / 0.8)
        partial = np.exp(-times / 0.35)
        absolute = common * partial
        relative = absolute / common
        fit = _fit_relative_decay(times, relative, common, 0)
        self.assertIsNotNone(fit.tau_ms)
        self.assertAlmostEqual(fit.tau_ms, 350.0, delta=1.0)
        self.assertFalse(fit.clamped_growth)

    def test_alpha_matches_one_sample_exponential(self) -> None:
        tau_ms = 500.0
        q24 = tau_ms_to_alpha_q24(tau_ms)
        alpha = q24 / float((1 << 24) - 1)
        expected = 1.0 - math.exp(-1.0 / (tau_ms * FPGA_SAMPLE_RATE / 1000.0))
        self.assertAlmostEqual(alpha, expected, delta=1.0 / ((1 << 24) - 1))

    def test_relative_growth_is_not_exported_as_decay(self) -> None:
        times = np.arange(0.0, 1.0, 0.01)
        common = np.exp(-times)
        relative = np.exp(times * 0.2)
        fit = _fit_relative_decay(times, relative, common, 0)
        self.assertIsNone(fit.tau_ms)
        self.assertEqual(fit.alpha_q24, 0)
        self.assertTrue(fit.clamped_growth)
        self.assertIs(type(fit.clamped_growth), bool)


if __name__ == "__main__":
    unittest.main()
