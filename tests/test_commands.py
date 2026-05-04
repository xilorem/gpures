from __future__ import annotations

import unittest
from datetime import timedelta

from gpures.commands import normalize_window


class NormalizeWindowTests(unittest.TestCase):
    def test_duration_from_now(self):
        start, end = normalize_window("now", duration="1h")
        self.assertLessEqual(end - start, timedelta(hours=1, seconds=1))
        self.assertGreaterEqual(end - start, timedelta(minutes=59))

    def test_until_required(self):
        with self.assertRaisesRegex(ValueError, "use one of --for or --until"):
            normalize_window("2026-05-01 10:00")

    def test_past_start(self):
        with self.assertRaisesRegex(ValueError, "cannot start in the past"):
            normalize_window("2000-01-01 00:00", duration="1h")

    def test_beyond_seven_days(self):
        with self.assertRaisesRegex(ValueError, "end within the next 7 days"):
            normalize_window("now", duration="8d")
