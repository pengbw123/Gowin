import unittest

from additive_uart_protocol import CMD_STRUM_CONFIG, make_packet, strum_packet


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


if __name__ == "__main__":
    unittest.main()
