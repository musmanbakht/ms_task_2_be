import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from services import hotspot_service as service


def fixture():
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}, "properties": {"mine_id": str(i), "Mine_Name": f"Mine {i}", **{f"mine_pct_{year}": i + year for year in service.YEARS}, **{f"mine_km2_{year}": i for year in service.YEARS}, **{f"mine_{year}": i for year in service.YEARS}}} for i in range(4)]}

class HotspotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data.geojson"
        self.path.write_text(json.dumps(fixture()))
        service._CACHE.clear()
    def tearDown(self):
        self.tmp.cleanup()
    def test_geojson_and_all_years(self):
        mines = service.load_geojson(self.path)
        self.assertEqual(len(mines), 4)
        for mine in mines:
            self.assertEqual(mine['geometry']['type'], 'Polygon')
            for year in service.YEARS:
                self.assertIn(f'mine_pct_{year}', mine['statistics'])
                self.assertIn(f'mine_km2_{year}', mine['statistics'])
    def test_real_export_time_series(self):
        mines = service.load_geojson()
        self.assertEqual(len(mines), 4)
        for mine in mines:
            for year in service.YEARS:
                row = mine["time_series"][str(year)]
                self.assertEqual(row["mining_percentage"], mine["statistics"][f"mine_pct_{year}"])
                self.assertEqual(row["mining_area_km2"], mine["statistics"][f"mine_km2_{year}"])
            for key in ("mean_frac", "sd_frac", "max_frac", "slope_per_yr", "net_change_km2", "trend_dir", "ever_mined"):
                self.assertIn(key, mine["statistics"])

    def test_missing_year_rejected(self):
        data = fixture()
        del data["features"][0]["properties"]["mine_pct_2005"]
        self.path.write_text(json.dumps(data))
        with self.assertRaises(HTTPException) as ctx:
            service.load_geojson(self.path)
        self.assertEqual(ctx.exception.status_code, 503)



    def test_missing_and_invalid_data(self):
        with self.assertRaises(HTTPException): service.load_geojson(self.path.parent / 'missing')
        self.path.write_text('{}')
        with self.assertRaises(HTTPException): service.load_geojson(self.path)
    def test_assets_and_legends(self):
        self.assertEqual(service.candidates(2015)[0], service.ROOT + 'AllMines_RF_Classification_2015')
        self.assertEqual(service.candidates(2023)[0], service.ROOT + '2023/AllMines_RF_Classification_2023')
        self.assertEqual([x['color'] for x in service.LEGENDS['classification']], service.PALETTE)
        self.assertEqual(service.LEGENDS['mining_only'][0]['value'], 3)

if __name__ == '__main__': unittest.main()
