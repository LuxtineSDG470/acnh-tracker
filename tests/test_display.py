"""Regression tests for collection behavior previously present in user's tracker."""
import unittest
from datetime import datetime
from app.display import calendar_badges, currently_catchable, display_metadata


class DisplayTests(unittest.TestCase):
    def test_original_fish_shadow_names_restored(self):
        self.assertEqual(display_metadata('fish', {'size': '5'})['size'], 'Huge')
        self.assertEqual(display_metadata('fish', {'size': '6'})['size'], 'Massive')
        self.assertEqual(display_metadata('fish', {'size': '4 (Fin)'})['size'], 'Large (Fin)')
        self.assertEqual(display_metadata('fish', {'size': '6 (Fin)'})['size'], 'Massive (Fin)')
        self.assertEqual(display_metadata('fish', {'size': 'Narrow'})['size'], 'Narrow')

    def test_catalog_not_mutated(self):
        original = {'size': '5', 'location': 'River'}
        transformed = display_metadata('fish', original)
        self.assertEqual(transformed['size'], 'Huge')
        self.assertEqual(original['size'], '5')

    def test_diy_sizes_are_readable(self):
        self.assertEqual(display_metadata('diy', {'size': {'width': 2, 'height': 3}})['size'], '2 × 3')
        self.assertEqual(display_metadata('diy', {'type': 'wallpaperFlooringRug'})['type'],
                         'Wallpaper / Flooring / Rug')
        self.assertEqual(display_metadata('fossil', {'type': 'standalone'})['type'], 'Standalone')

    def test_season_start_end_wraps_year(self):
        # Dec + Jan season, absent in Nov and Feb.
        meta = {'months': {'north': '100000000001', 'south': '000000000000'}}
        dec = calendar_badges(meta, 'north', 12)
        jan = calendar_badges(meta, 'north', 1)
        self.assertTrue(dec['new'])
        self.assertFalse(dec['leaving'])
        self.assertFalse(jan['new'])
        self.assertTrue(jan['leaving'])
        self.assertFalse(calendar_badges(meta, 'south', 12)['seasonal'])

    def test_year_round_classification(self):
        meta = {'months': {'north': '111111111111'}}
        for month in (1, 7, 12):
            result = calendar_badges(meta, 'north', month)
            self.assertTrue(result['year_round'])
            self.assertFalse(result['new'])
            self.assertFalse(result['leaving'])

    def test_available_now_overnight_and_daytime(self):
        meta = {'months': {'north': '111111111111'}, 'time': '4 PM - 9 AM'}
        self.assertTrue(currently_catchable(meta, 'north', datetime(2026, 11, 22, 23, 30)))
        self.assertTrue(currently_catchable(meta, 'north', datetime(2026, 11, 22, 6, 30)))
        self.assertFalse(currently_catchable(meta, 'north', datetime(2026, 11, 22, 12, 30)))

    def test_available_now_multiple_windows_and_unicode_dash(self):
        meta = {'months': {'north': '111111111111'}, 'time': '9 AM – 4 PM, 9 PM – 4 AM'}
        self.assertTrue(currently_catchable(meta, 'north', datetime(2026, 11, 22, 22, 0)))
        self.assertTrue(currently_catchable(meta, 'north', datetime(2026, 11, 22, 10, 0)))
        self.assertFalse(currently_catchable(meta, 'north', datetime(2026, 11, 22, 18, 0)))
        meta['time'] = '4 AM - 8 AM & 5 PM - 7 PM'
        self.assertTrue(currently_catchable(meta, 'north', datetime(2026, 11, 22, 18, 0)))
        self.assertFalse(currently_catchable(meta, 'north', datetime(2026, 11, 22, 12, 0)))

    def test_season_blocks_real_time_label(self):
        meta = {'months': {'north': '000000000000'}, 'time': 'All day'}
        self.assertFalse(currently_catchable(meta, 'north', datetime(2026, 11, 22, 12, 0)))


if __name__ == '__main__':
    unittest.main()
