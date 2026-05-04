from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gpures.tui import cursor_to_time, time_to_cursor


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


class CursorTimeTests(unittest.TestCase):
    def test_cursor_to_time_linear(self):
        start = _now() + timedelta(minutes=5)
        duration = timedelta(hours=4)
        pos = cursor_to_time(10, start, duration, 40)
        expected = start + timedelta(hours=1)
        self.assertAlmostEqual(
            (pos - expected).total_seconds(), 0, delta=2,
        )

    def test_cursor_to_time_at_edges(self):
        start = _now()
        duration = timedelta(hours=2)
        left = cursor_to_time(0, start, duration, 40)
        right = cursor_to_time(39, start, duration, 40)
        self.assertAlmostEqual(
            (left - start).total_seconds(), 0, delta=2,
        )
        expected_right = start + timedelta(seconds=39 / 40 * 7200)
        self.assertAlmostEqual(
            (right - expected_right).total_seconds(), 0, delta=2,
        )

    def test_time_to_cursor_roundtrip(self):
        start = _now() + timedelta(minutes=5)
        duration = timedelta(hours=4)
        for cursor_pos in [0, 10, 20, 39]:
            dt = cursor_to_time(cursor_pos, start, duration, 40)
            back = time_to_cursor(dt, start, duration, 40)
            self.assertEqual(cursor_pos, back)

    def test_time_to_cursor_clamped(self):
        start = _now()
        duration = timedelta(hours=1)
        before = time_to_cursor(start - timedelta(minutes=10), start, duration, 40)
        after = time_to_cursor(start + timedelta(hours=2), start, duration, 40)
        self.assertEqual(before, 0)
        self.assertEqual(after, 39)
