import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jukebox_tracks import format_time, normalize_entry, parse_time


class TrackSettingsTests(unittest.TestCase):
    def test_v01_entry_migration(self):
        self.assertEqual(
            normalize_entry(r"C:\\Music\\album.mp3"),
            {"path": r"C:\\Music\\album.mp3", "start_ms": 0, "end_ms": 0},
        )

    def test_timestamp_formats(self):
        self.assertEqual(parse_time("1:23.456"), 83_456)
        self.assertEqual(parse_time("1:02:03"), 3_723_000)
        self.assertEqual(parse_time("90"), 90_000)
        self.assertEqual(format_time(83_456), "1:23.456")

    def test_blank_end(self):
        self.assertEqual(parse_time("", allow_blank=True), 0)
        self.assertEqual(format_time(0, blank_zero=True), "")

    def test_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            parse_time("one minute")


if __name__ == "__main__":
    unittest.main()
