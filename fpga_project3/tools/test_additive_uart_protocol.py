import unittest
from pathlib import Path

from additive_uart_protocol import (
    CMD_ENVELOPE_TABLE_POINT, CMD_PITCH_ENVELOPE_TIME, CMD_STRUM_CONFIG,
    CMD_MIDI_EVENT, TABLE_PITCH_RATIO, envelope_packets, envelope_table_packet,
    load_preset, make_packet, midi_event_packet, strum_packet,
)


class StrumPacketTests(unittest.TestCase):
    def test_strum_packet_layout_and_checksum(self):
        packet = strum_packet({
            "mode": "strum",
            "strum_interval_ms": 18,
            "strum_gate_ms": 120,
            "velocity_speed_depth": 80,
            "velocity_brightness_depth": 96,
        })
        self.assertEqual(len(packet), 12)
        self.assertEqual(packet[:3], bytes((0xA5, 0x5A, CMD_STRUM_CONFIG)))
        self.assertEqual(packet[3:11], bytes((1, 18, 0, 120, 0, 80, 96, 0)))
        self.assertEqual(packet, make_packet(CMD_STRUM_CONFIG, packet[3:11]))

    def test_parameter_limits_are_checked(self):
        with self.assertRaises(ValueError):
            strum_packet({"mode": "strum", "strum_interval_ms": 0})
        with self.assertRaises(ValueError):
            strum_packet({"mode": "strum", "velocity_speed_depth": 128})

    def test_pitch_table_packet_layout(self):
        packet = envelope_table_packet(TABLE_PITCH_RATIO, 255, 0x1000)
        self.assertEqual(packet[:3], bytes((0xA5, 0x5A, CMD_ENVELOPE_TABLE_POINT)))
        self.assertEqual(packet[3:7], bytes((TABLE_PITCH_RATIO, 255, 0x00, 0x10)))
        self.assertEqual(packet, make_packet(CMD_ENVELOPE_TABLE_POINT, packet[3:11]))

    def test_complete_envelope_download(self):
        preset = load_preset(Path(__file__).with_name("pianotone2.json"))
        packets = envelope_packets(preset)
        # Five scalar/timing packets plus four independently writable 256-point RAMs.
        self.assertEqual(len(packets), 5 + 4 * 256)
        self.assertEqual(packets[4][2], CMD_PITCH_ENVELOPE_TIME)
        # The default pitch envelope is neutral: Q4.12 ratio 1.0 = 0x1000.
        self.assertEqual(packets[-1][3:7], bytes((TABLE_PITCH_RATIO, 255, 0x00, 0x10)))

    def test_midi_event_packet_uses_full_note_number(self):
        packet = midi_event_packet(0x92, 108, 117)
        self.assertEqual(len(packet), 12)
        self.assertEqual(packet[:3], bytes((0xA5, 0x5A, CMD_MIDI_EVENT)))
        self.assertEqual(packet[3:7], bytes((0x92, 108, 117, 117)))
        self.assertEqual(packet, make_packet(CMD_MIDI_EVENT, packet[3:11]))

    def test_midi_event_rejects_invalid_data_byte(self):
        with self.assertRaises(ValueError):
            midi_event_packet(0x90, 128, 100)



if __name__ == "__main__":
    unittest.main()
