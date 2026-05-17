from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gpures.tui import cursor_to_time, snap_time, time_to_cursor


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


class SnapTimeTests(unittest.TestCase):
    def test_snap_to_interval_round_down(self):
        dt = datetime(2026, 6, 15, 10, 17, 0, tzinfo=timezone.utc)
        snapped = snap_time(dt, 15)
        self.assertEqual(snapped.minute, 15)

    def test_snap_to_interval_round_up(self):
        dt = datetime(2026, 6, 15, 10, 23, 0, tzinfo=timezone.utc)
        snapped = snap_time(dt, 15)
        self.assertEqual(snapped.minute, 30)

    def test_snap_already_aligned(self):
        dt = datetime(2026, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        snapped = snap_time(dt, 15)
        self.assertEqual(snapped, dt)

    def test_snap_zero_interval(self):
        dt = datetime(2026, 6, 15, 10, 17, 0, tzinfo=timezone.utc)
        snapped = snap_time(dt, 0)
        self.assertEqual(snapped, dt)

    def test_snap_hour_boundary(self):
        dt = datetime(2026, 6, 15, 10, 45, 0, tzinfo=timezone.utc)
        snapped = snap_time(dt, 60)
        self.assertEqual(snapped.hour, 11)
        self.assertEqual(snapped.minute, 0)
