import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import mido

from core.midi_player_core import TimedMidiEvent, load_midi, note_name, transpose_event
from apps.uart_midi_player import is_usb_serial_candidate, port_display_name


class MidiPlayerCoreTests(unittest.TestCase):
    def test_note_names_cover_full_midi_range(self):
        self.assertEqual(note_name(0), "C-1")
        self.assertEqual(note_name(60), "C4")
        self.assertEqual(note_name(127), "G9")

    def test_transpose_note_but_not_sustain(self):
        note = TimedMidiEvent(1.0, 0x91, 60, 80)
        shifted = transpose_event(note, 12, 125)
        self.assertEqual((shifted.status, shifted.data1, shifted.data2), (0x91, 72, 100))
        sustain = TimedMidiEvent(1.0, 0xB1, 64, 127)
        self.assertEqual(transpose_event(sustain, 12), sustain)

    def test_out_of_range_transpose_drops_note(self):
        self.assertIsNone(transpose_event(TimedMidiEvent(0, 0x90, 120, 100), 12))

    def test_generated_midi_is_full_range_and_timed(self):
        midi = mido.MidiFile(ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        track.append(mido.Message("note_on", note=36, velocity=80, time=0))
        track.append(mido.Message("note_on", note=96, velocity=90, time=240))
        track.append(mido.Message("note_off", note=36, velocity=0, time=240))
        track.append(mido.Message("note_off", note=96, velocity=0, time=240))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.mid"
            midi.save(path)
            song = load_midi(path)
        self.assertEqual(song.min_note, 36)
        self.assertEqual(song.max_note, 96)
        self.assertEqual(song.peak_polyphony, 2)
        self.assertGreater(song.duration_s, 0.5)

    def test_serial_filter_rejects_bluetooth_and_keeps_usb(self):
        bluetooth = SimpleNamespace(
            device="COM6", description="Bluetooth Serial", hwid="BTHENUM\\ABC",
            vid=None, pid=None,
        )
        usb = SimpleNamespace(
            device="COM11", description="USB Serial Port", hwid="USB\\VID_1A86&PID_7523",
            vid=0x1A86, pid=0x7523,
        )
        self.assertFalse(is_usb_serial_candidate(bluetooth))
        self.assertTrue(is_usb_serial_candidate(usb))
        self.assertIn("COM11", port_display_name(usb))


if __name__ == "__main__":
    unittest.main()
