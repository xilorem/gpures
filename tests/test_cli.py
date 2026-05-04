from __future__ import annotations

import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

from gpures.cli import (
    Gpu,
    Store,
    cursor_to_time,
    time_to_cursor,
    normalize_window,
    reservation_positions,
    utcnow,
)


class CursorTimeTests(unittest.TestCase):
    def test_cursor_to_time_linear(self):
        start = utcnow() + timedelta(minutes=5)
        duration = timedelta(hours=4)
        pos = cursor_to_time(10, start, duration, 40)
        expected = start + timedelta(hours=1)
        self.assertAlmostEqual(
            (pos - expected).total_seconds(), 0, delta=2,
        )

    def test_cursor_to_time_at_edges(self):
        start = utcnow()
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
        start = utcnow() + timedelta(minutes=5)
        duration = timedelta(hours=4)
        for cursor_pos in [0, 10, 20, 39]:
            dt = cursor_to_time(cursor_pos, start, duration, 40)
            back = time_to_cursor(dt, start, duration, 40)
            self.assertEqual(cursor_pos, back)

    def test_time_to_cursor_clamped(self):
        start = utcnow()
        duration = timedelta(hours=1)
        before = time_to_cursor(start - timedelta(minutes=10), start, duration, 40)
        after = time_to_cursor(start + timedelta(hours=2), start, duration, 40)
        self.assertEqual(before, 0)
        self.assertEqual(after, 39)


class StoreTests(unittest.TestCase):
    def make_store(self, tmp_path: Path) -> Store:
        store = Store(tmp_path)
        store.init_db()
        store.sync_gpus([Gpu("0", "GPU 0"), Gpu("1", "GPU 1")])
        return store

    def test_reserve_rejects_overlap(self):
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            start = utcnow() + timedelta(minutes=5)
            end = start + timedelta(hours=1)

            store.reserve("alice", ["0"], start, end, "first")

            with self.assertRaisesRegex(RuntimeError, "reservation conflicts"):
                store.reserve("bob", ["0"], start + timedelta(minutes=10), end, "second")

    def test_reserve_allows_different_gpu_same_time(self):
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            start = utcnow() + timedelta(minutes=5)
            end = start + timedelta(hours=1)

            first = store.reserve("alice", ["0"], start, end, None)
            second = store.reserve("bob", ["1"], start, end, None)

            self.assertNotEqual(first, second)

    def test_find_free_gpus(self):
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            start = utcnow() + timedelta(minutes=5)
            end = start + timedelta(hours=1)

            store.reserve("alice", ["0"], start, end, None)

            self.assertEqual(store.find_free_gpus(start, end), ["1"])

    def test_owner_or_admin_cancel(self):
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            start = utcnow() + timedelta(minutes=5)
            end = start + timedelta(hours=1)
            reservation_id = store.reserve("alice", ["0"], start, end, None)

            with self.assertRaisesRegex(RuntimeError, "owner or root"):
                store.cancel(reservation_id, "bob")

            store.cancel(reservation_id, "root", is_admin=True)

            rows = store.reservation_rows()
            self.assertIsNotNone(rows[0]["canceled_at"])

    def test_window_must_end_within_seven_days(self):
        class Args:
            start = "now"
            duration = "8d"
            until = None

        with self.assertRaisesRegex(ValueError, "end within the next 7 days"):
            normalize_window(Args())

    def test_window_must_start_within_seven_days(self):
        class Args:
            start = "2999-01-01 10:00"
            duration = "1h"
            until = None

        with self.assertRaisesRegex(ValueError, "start within the next 7 days"):
            normalize_window(Args())

    def test_reservation_positions_scale_to_timeline_width(self):
        with TemporaryDirectory() as tmp:
            store = self.make_store(Path(tmp))
            start = utcnow() + timedelta(minutes=5)
            reserved_start = start + timedelta(hours=1)
            reserved_end = start + timedelta(hours=2)
            end = start + timedelta(hours=4)
            store.reserve("alice", ["0"], reserved_start, reserved_end, None)

            positions = reservation_positions(
                store.reservations_between(start, end),
                start,
                end,
                40,
            )

            self.assertEqual(positions["0"][0][0], 10)
            self.assertEqual(positions["0"][0][1], 20)


if __name__ == "__main__":
    unittest.main()
