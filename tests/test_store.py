from __future__ import annotations

import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

from gpures.models import Gpu, Reservation
from gpures.store import Store, utcnow


class ReservationTests(unittest.TestCase):
    def test_reservation_from_store(self):
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.init_db()
            store.sync_gpus([Gpu("0", "GPU 0")])
            start = utcnow() + timedelta(minutes=5)
            end = start + timedelta(hours=1)
            rid = store.reserve("alice", ["0"], start, end, "test")

            rows = store.reservation_rows()
            self.assertEqual(len(rows), 1)
            res = rows[0]
            self.assertIsInstance(res, Reservation)
            self.assertEqual(res.id, rid)
            self.assertEqual(res.username, "alice")
            self.assertEqual(res.gpu_ids, ["0"])
            self.assertEqual(res.reason, "test")


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
            self.assertIsNotNone(rows[0].canceled_at)
