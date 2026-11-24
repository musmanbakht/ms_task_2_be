import unittest
from unittest.mock import patch
from fastapi import HTTPException
from services.change_detection_service import matrix_from_groups, summary, get_change

class ChangeTests(unittest.TestCase):
    def test_all_transitions_and_square_metres(self):
        matrix = matrix_from_groups([{'code': i, 'sum': (i+1)*1e6} for i in range(16)])
        self.assertEqual(matrix, [[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,15,16]])
    def test_summaries_and_annualised_periods(self):
        matrix = [[1,2,3,4],[5,6,7,8],[9,10,11,12],[13,14,15,16]]
        for period in (3,5):
            result = summary(matrix, period)
            self.assertEqual(result['valid_area_km2'],136)
            self.assertEqual(result['changed_km2'],102)
            self.assertAlmostEqual(result['classes'][3]['annualised_km2'],result['classes'][3]['net_km2']/period)
            self.assertEqual(result['mining_expansion_km2'],24)
            self.assertEqual(result['mining_contraction_km2'],42)
            self.assertAlmostEqual(sum(c['net_km2'] for c in result['classes']),0)
    def test_empty_and_invalid(self):
        self.assertIsNone(summary(matrix_from_groups([]),5)['changed_percent'])
        with self.assertRaises(ValueError): matrix_from_groups([{'code':16,'sum':1}])
    def test_invalid_years(self):
        for start,end in [(2005,2000),(2000,2000),(1999,2023)]:
            with self.assertRaises(HTTPException): get_change('unused',start,end)
