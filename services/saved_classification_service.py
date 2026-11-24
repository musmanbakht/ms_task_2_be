"""Saved classification raster tiles and historical statistics."""
import threading
import time
import ee
from fastapi import HTTPException
from services.ee_auth import get_ee_credentials
from services.hotspot_service import YEARS, load_geojson, ROOT
from services.classification_service import _load_classification_stats

_cache = {}
_lock = threading.Lock()

def get_saved_classification(year, session_id):
    if year not in YEARS:
        raise HTTPException(400, 'Unsupported classification year.')
    credentials = get_ee_credentials(session_id)
    with _lock:
        key = (session_id, year)
        cached = _cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1]
        stats = _load_classification_stats(year, None)
        if stats.get('error'):
            raise HTTPException(503, stats['error'])
        timeline = {}
        for historical_year in YEARS:
            historical = _load_classification_stats(historical_year, None)
            timeline[str(historical_year)] = historical['records'] if not historical.get('error') else None
        try:
            ee.Initialize(credentials=credentials, project='etmodule')
            name = f'AllMines_RF_Classification_{year}'
            asset = None
            for candidate in (ROOT + name, ROOT + str(year) + '/' + name):
                try:
                    if ee.data.getAsset(candidate):
                        asset = candidate
                        break
                except Exception as exc:
                    if '404' not in str(exc) and 'not found' not in str(exc).lower():
                        raise
            if not asset:
                raise HTTPException(404, f'Saved classification asset for {year} not found.')
            roi = ee.FeatureCollection(ROOT + 'AllMines_5km_ROIs').geometry()
            tile = ee.Image(asset).select('classification').clip(roi).getMapId({'min':0,'max':3,'palette':['#0064C8','#B4B4B4','#006400','#D7191C']})['tile_fetcher'].url_format
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, 'Unable to load saved classification from Earth Engine. Check asset access.') from exc
        result = {'year':year,'asset':asset,'tile_url':tile,'mines':load_geojson(),'statistics':stats['records'],'statistics_by_year':timeline,'cache_seconds':1500,'legend':[{'label':n,'color':c} for n,c in zip(('Water','Barren','Vegetation','Mining'),('#0064C8','#B4B4B4','#006400','#D7191C'))]}
        _cache[key] = (time.time()+1500,result)
        return result
