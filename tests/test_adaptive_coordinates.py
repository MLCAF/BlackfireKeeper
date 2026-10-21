"""Native Maa normalization and physical click mapping, no game access."""
import unittest
from bk2.coordinate_check import check_coordinates


class AdaptiveCoordinatesTests(unittest.TestCase):
    def test_native_mapping_at_standard_and_small_sizes(self):
        results=check_coordinates()
        self.assertEqual(len(results),5)
        self.assertTrue(all(r['screenshot'][1]==720 and r['max_error_pixels']<=1.01 for r in results))
