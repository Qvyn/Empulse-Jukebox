import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_analysis import STINGER_SLOTS, suggest_segments


class SegmentSuggestionTests(unittest.TestCase):
    def test_assigns_every_context_and_stinger_with_finite_ranges(self):
        envelope = [0.1] * 100 + [0.4] * 100 + [1.0] * 100
        result = suggest_segments(envelope, 180_000)
        expected = {"menu", "pre_match", "in_match", "practice", "post_match", *STINGER_SLOTS}
        self.assertEqual(set(result), expected)
        for slot, (start, end) in result.items():
            self.assertGreaterEqual(start, 0, slot)
            self.assertGreater(end, start, slot)
            self.assertLessEqual(end, 180_000, slot)
        for slot in STINGER_SLOTS:
            start, end = result[slot]
            self.assertLessEqual(end - start, 7_000)

    def test_short_audio_is_clamped(self):
        result = suggest_segments([0.2, 0.8, 0.4], 2_000)
        self.assertTrue(result)
        self.assertTrue(all(0 <= start < end <= 2_000 for start, end in result.values()))


if __name__ == "__main__":
    unittest.main()
