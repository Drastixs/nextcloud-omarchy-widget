import unittest

from support import fixture
from ncstatus.statusdetails import parse_eta, parse_status_details as parse


class Shapes(unittest.TestCase):
    def test_size_and_eta(self):
        d = parse("Connected - Syncing 191 MB (6 minutes 22 seconds left)")
        self.assertTrue(d.connected)
        self.assertEqual(d.kind, "syncing")
        self.assertEqual(d.total_bytes, 191 * 1024 ** 2)  # 1024-based though labelled MB
        self.assertEqual(d.eta_sec, 382)
        self.assertIsNone(d.files_total)

    def test_fractional_size(self):
        self.assertEqual(parse("Connected - Syncing 4.8 MB").total_bytes, round(4.8 * 1024 ** 2))

    def test_size_only_and_kb(self):
        d = parse("Connected - Syncing 2 KB")
        self.assertEqual((d.total_bytes, d.eta_sec), (2048, None))

    def test_file_count_with_two_spaces(self):
        d = parse("Connected - Syncing 2 of 12  (0 seconds left)")
        self.assertEqual((d.files_done, d.files_total, d.eta_sec), (2, 12, 0))
        self.assertIsNone(d.total_bytes)

    def test_zero_of_zero_has_no_numbers(self):
        d = parse("Connected - Syncing 0 of 0")
        self.assertEqual(d.kind, "syncing")
        self.assertEqual((d.files_done, d.files_total, d.eta_sec), (None, None, None))

    def test_bare_syncing(self):
        d = parse("Connected - Syncing")
        self.assertEqual((d.kind, d.total_bytes, d.eta_sec), ("syncing", None, None))

    def test_unknown_syncing_shape_falls_back_to_no_numbers(self):
        d = parse("Connected - Syncing some brand new shape")
        self.assertEqual((d.kind, d.total_bytes, d.files_total), ("syncing", None, None))

    def test_other_states(self):
        self.assertEqual(parse("Connected - Success").kind, "idle")
        self.assertEqual(parse("Connected - Success, some files were ignored.").kind, "ignored")
        self.assertEqual(parse("Connected - Sync paused").kind, "paused")
        self.assertEqual(parse("Connected - Error").kind, "error")
        self.assertEqual(parse('Connected - Checking for changes in "Docs/x"').kind, "checking")

    def test_not_connected(self):
        for text in ("Disconnected - Success", "Network error - Syncing", "", None):
            self.assertFalse(parse(text).connected, text)

    def test_every_captured_string_parses_as_connected(self):
        for line in fixture("statusdetails-strings.txt").splitlines():
            d = parse(line)
            self.assertTrue(d.connected, line)
            self.assertNotEqual(d.kind, "other", line)
            if "left)" in line:
                self.assertIsNotNone(d.eta_sec, line)


class Eta(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_eta("1 second"), 1)
        self.assertEqual(parse_eta("1 minute"), 60)
        self.assertEqual(parse_eta("0 seconds"), 0)
        self.assertEqual(parse_eta("21 minutes"), 1260)
        self.assertEqual(parse_eta("2 hours 3 minutes"), 7380)
        self.assertEqual(parse_eta("1 day 1 hour"), 90000)

    def test_unknown_text_is_none(self):
        self.assertIsNone(parse_eta("a few moments"))
        self.assertIsNone(parse_eta("3 fortnights"))
        self.assertIsNone(parse_eta(None))


if __name__ == "__main__":
    unittest.main()
