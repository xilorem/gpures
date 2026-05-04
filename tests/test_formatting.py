from __future__ import annotations

import unittest

from gpures.formatting import table


class TableTests(unittest.TestCase):
    def test_basic(self):
        result = table(
            ["Name", "Age"],
            [["Alice", "30"], ["Bob", "25"]],
        )
        lines = result.split("\n")
        self.assertEqual(len(lines), 4)
        self.assertIn("Name", lines[0])
        self.assertIn("Age", lines[0])

    def test_empty(self):
        result = table(["Name", "Age"], [])
        lines = result.split("\n")
        self.assertEqual(len(lines), 2)
