import tempfile
import unittest
from pathlib import Path

from core.fm_patch import (
    ALGORITHM_FLAGS,
    CMD_FM_COMMIT,
    CMD_FM_NOTE_GAIN,
    default_patch,
    dx7_base_qrate,
    dx7_envelope_target,
    dx7_keyboard_level_offset,
    dx7_scale_level,
    pitch_envelope_coefficient,
    pitch_ratio_q16,
    load_syx,
    normalise_patch,
    patch_packets,
)
from generators.generate_fm_preset_bank import compile_bank


class FmPatchTests(unittest.TestCase):
    def test_all_algorithms_have_six_operator_rows(self):
        self.assertEqual(len(ALGORITHM_FLAGS), 32)
        self.assertTrue(all(len(row) == 6 for row in ALGORITHM_FLAGS))

    def test_packets_have_valid_frame_and_checksum(self):
        packets = patch_packets(default_patch())
        self.assertEqual(packets[-1][2], CMD_FM_COMMIT)
        self.assertEqual(sum(packet[2] == CMD_FM_NOTE_GAIN for packet in packets), 0)
        for packet in packets:
            self.assertEqual(len(packet), 12)
            self.assertEqual(packet[:2], b"\xA5\x5A")
            checksum = 0
            for value in packet[2:11]:
                checksum ^= value
            self.assertEqual(packet[11], checksum)

    def test_raw_32_voice_bank_unpack(self):
        voice = bytearray(128)
        for op in range(6):
            base = op * 17
            voice[base:base + 4] = bytes((99, 70, 50, 60))
            voice[base + 4:base + 8] = bytes((99, 80, 60, 0))
            voice[base + 14] = 90
            voice[base + 15] = 2  # ratio, coarse 1
        voice[110] = 31
        voice[111] = 7
        voice[118:128] = b"TEST PATCH"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bank.syx"
            path.write_bytes(bytes(voice) * 32)
            patches = load_syx(path)
        self.assertEqual(len(patches), 32)
        self.assertEqual(patches[0]["algorithm"], 32)
        self.assertEqual(patches[0]["name"], "TEST PATCH")
        self.assertEqual(patches[0]["operators"][0]["number"], 6)

    def test_normalise_clamps_human_input(self):
        patch = normalise_patch({"algorithm": 99, "feedback": -1,
                                 "operators": [{"output_level": 140}]})
        self.assertEqual(patch["algorithm"], 32)
        self.assertEqual(patch["feedback"], 0)
        self.assertEqual(patch["operators"][0]["output_level"], 99)

    def test_dx7_measured_level_and_rate_mapping(self):
        self.assertEqual(dx7_scale_level(0), 0)
        self.assertEqual(dx7_scale_level(19), 46)
        self.assertEqual(dx7_scale_level(20), 48)
        self.assertEqual(dx7_scale_level(99), 127)
        self.assertEqual(dx7_envelope_target(99), 4032)
        self.assertEqual(dx7_base_qrate(0), 0)
        self.assertEqual(dx7_base_qrate(99), 63)

    def test_keyboard_scaling_is_now_compact_runtime_metadata(self):
        patch = default_patch()
        op = patch["operators"][0]
        self.assertIsInstance(dx7_keyboard_level_offset(op, 60), int)
        self.assertFalse(any(packet[2] == CMD_FM_NOTE_GAIN
                             for packet in patch_packets(patch)))

    def test_pitch_envelope_uses_full_dx7_range(self):
        self.assertLess(pitch_ratio_q16(0), 5000)
        self.assertEqual(pitch_ratio_q16(50), 65536)
        self.assertGreater(pitch_ratio_q16(99), 900000)
        self.assertLess(pitch_envelope_coefficient(0),
                        pitch_envelope_coefficient(99))

    def test_compact_bank_has_all_32_power_on_presets(self):
        voice = bytearray(128)
        for op in range(6):
            base = op * 17
            voice[base:base + 4] = bytes((99, 70, 50, 60))
            voice[base + 4:base + 8] = bytes((99, 80, 60, 0))
            voice[base + 14] = 90
            voice[base + 15] = 2
        voice[118:128] = b"BANK TEST "
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bank.syx"
            output = Path(folder) / "generated"
            source.write_bytes(bytes(voice) * 32)
            compile_bank(source, output)
            self.assertEqual(len((output / "fm_rom1a_global.mem").read_text().splitlines()), 32)
            self.assertEqual(len((output / "fm_rom1a_operator.mem").read_text().splitlines()), 192)
            self.assertEqual(len((output / "fm_rom1a_envelope.mem").read_text().splitlines()), 768)


if __name__ == "__main__":
    unittest.main()
