"""Regression coverage for missing/empty/invalid Portainer timezone settings."""
import datetime
import unittest
from unittest.mock import patch

from app.timezone_utils import DEFAULT_TIMEZONE, local_date, timezone_name


class TimezoneTests(unittest.TestCase):
    def test_missing_and_blank_tz_default_to_chicago(self):
        for value in (None, '', ' ', '\t\n'):
            with self.subTest(tz=value):
                self.assertEqual(timezone_name(value), DEFAULT_TIMEZONE)
                self.assertIsInstance(local_date(value), datetime.date)

    def test_valid_timezones_are_preserved(self):
        for supplied, expected in (('UTC', 'UTC'), ('America/New_York', 'America/New_York'),
                                   (' America/Chicago ', DEFAULT_TIMEZONE)):
            with self.subTest(tz=supplied):
                self.assertEqual(timezone_name(supplied), expected)

    def test_invalid_zone_does_not_crash_login_page_context(self):
        for invalid in ('NotAPlace/Nowhere', '/etc/passwd', '../foo', ':', 'America/Chicago\\n'):
            with self.subTest(tz=invalid):
                self.assertEqual(timezone_name(invalid), DEFAULT_TIMEZONE)
                self.assertIsInstance(local_date(invalid), datetime.date)

    def test_missing_tz_database_falls_back_to_utc(self):
        from zoneinfo import ZoneInfoNotFoundError
        with patch('app.timezone_utils.ZoneInfo', side_effect=ZoneInfoNotFoundError):
            self.assertEqual(timezone_name('America/Chicago'), 'UTC')
            self.assertIsInstance(local_date(''), datetime.date)


if __name__ == '__main__':
    unittest.main()
