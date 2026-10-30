"""Regression checks for Talantix epoch-millisecond timestamps on Windows."""
import unittest

from archive import utc


class DatesTest(unittest.TestCase):
    def test_negative_epoch_ms(self):
        self.assertEqual(utc(-2208988800000), "1900-01-01T00:00:00+00:00")
        self.assertEqual(utc(-631152000000), "1950-01-01T00:00:00+00:00")

    def test_valid_positive_and_empty(self):
        self.assertEqual(utc(0), "1970-01-01T00:00:00+00:00")
        self.assertEqual(utc(None), "")
        self.assertEqual(utc(""), "")

    def test_out_of_range_preserves_raw(self):
        raw = 99999999999999999
        self.assertEqual(utc(raw), str(raw))


if __name__ == "__main__":
    unittest.main()
