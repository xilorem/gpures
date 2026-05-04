from __future__ import annotations

import unittest
from datetime import timedelta

from gpures.parsers import parse_duration, parse_gpu_list, parse_local_datetime


class ParseDurationTests(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(parse_duration("30m"), timedelta(minutes=30))

    def test_hours(self):
        self.assertEqual(parse_duration("4h"), timedelta(hours=4))

    def test_days(self):
        self.assertEqual(parse_duration("1d"), timedelta(days=1))

    def test_combined(self):
        self.assertEqual(parse_duration("1h30m"), timedelta(hours=1, minutes=30))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_duration("abc")


class ParseGpuListTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(parse_gpu_list("0"), ["0"])

    def test_multiple(self):
        self.assertEqual(parse_gpu_list("0,1,2"), ["0", "1", "2"])

    def test_whitespace(self):
        self.assertEqual(parse_gpu_list(" 0 , 1 "), ["0", "1"])


class ParseLocalDatetimeTests(unittest.TestCase):
    def test_now(self):
        result = parse_local_datetime("now")
        self.assertIsNotNone(result)

    def test_yyyy_mm_dd_hh_mm(self):
        result = parse_local_datetime("2026-04-21 09:00")
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 4)
        self.assertEqual(result.day, 21)
