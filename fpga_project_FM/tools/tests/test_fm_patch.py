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
    load_syx,
    normalise_patch,
    patch_packets,
)


class FmPatchTests(unittest.TestCase):
    def test_all_algorithms_have_six_operator_rows(self):
        self.assertEqual(len(ALGORITHM_FLAGS), 32)
        self.assertTrue(all(len(row) == 6 for row in ALGORITHM_FLAGS))

    def test_packets_have_valid_frame_and_checksum(self):
        packets = patch_packets(default_patch())
        self.assertEqual(packets[-1][2], CMD_FM_COMMIT)
        self.assertEqual(sum(packet[2] == CMD_FM_NOTE_GAIN for packet in packets), 6 * 128)
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

    def test_note_table_uses_signed_log_level_offsets(self):
        patch = default_patch()
        op = patch["operators"][0]
        offset = dx7_keyboard_level_offset(op, 60)
        packets = patch_packets(patch)
        note_packets = [packet for packet in packets
                        if packet[2] == CMD_FM_NOTE_GAIN]
        packet = note_packets[60]
        encoded = int.from_bytes(packet[5:7], "little", signed=True)
        self.assertEqual(encoded, offset)


if __name__ == "__main__":
    unittest.main()
