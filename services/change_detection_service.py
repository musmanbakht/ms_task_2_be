"""Post-classification comparison on a common 30 m UTM grid."""
import threading
import time
import ee
from fastapi import HTTPException
from services.ee_auth import get_ee_credentials
from services.hotspot_service import YEARS, candidates, load_geojson

NAMES = ['Water', 'Barren', 'Vegetation', 'Mining']
GRID = 'EPSG:32735'
TRANSFORM = [30, 0, 0, 0, -30, 10000000]
LOCK = threading.Lock()
CACHE = {}
PALETTE = ['#1f78b4','#a6cee3','#6baed6','#08519c','#e6ab02','#d2b48c','#e6c56e','#a6611a','#74c476','#d95f02','#008000','#e31a1c','#6a51a3','#fd8d3c','#31a354','#ff0000']

def summary(matrix, years):
    initial = [sum(row) for row in matrix]
    final = [sum(matrix[i][j] for i in range(4)) for j in range(4)]
    total = sum(initial)
    classes = []
    for i, name in enumerate(NAMES):
        gain = final[i] - matrix[i][i]; loss = initial[i] - matrix[i][i]
        classes.append({'name': name, 'initial_km2': initial[i], 'final_km2': final[i], 'gain_km2': gain, 'loss_km2': loss, 'net_km2': gain-loss, 'annualised_km2': (gain-loss)/years, 'percentage_point_change': (final[i]-initial[i])*100/total if total else None})
    changed = total - sum(matrix[i][i] for i in range(4))
    return {'matrix_km2':matrix, 'valid_area_km2':total, 'changed_km2':changed, 'changed_percent':changed*100/total if total else None, 'classes':classes, 'mining_expansion_km2':classes[3]['gain_km2'], 'mining_contraction_km2':classes[3]['loss_km2'], 'mining_unchanged_km2':matrix[3][3], 'vegetation_to_barren_km2':matrix[2][1], 'vegetation_to_mining_km2':matrix[2][3], 'vegetation_to_other_km2':matrix[2][0]+matrix[2][1]+matrix[2][3], 'water_to_barren_km2':matrix[0][1], 'water_to_vegetation_km2':matrix[0][2], 'water_to_mining_km2':matrix[0][3]}

def matrix_from_groups(groups):
    matrix = [[0.0]*4 for _ in range(4)]
    for group in groups or []:
        code = int(group['code'])
        if not 0 <= code < 16: raise ValueError('Invalid transition code')
        matrix[code//4][code%4] += float(group['sum']) / 1e6
    return matrix

def resolve(year):
    for asset in candidates(year):
        try:
            if ee.data.getAsset(asset): return asset
        except Exception as exc:
            if '404' not in str(exc) and 'not found' not in str(exc).lower(): raise
    raise HTTPException(404, f'Classification raster for {year} was not found or is inaccessible.')

def get_change(session_id, start, end):
    if start not in YEARS or end not in YEARS or start >= end: raise HTTPException(400, 'Choose supported years in chronological order.')
    credentials = get_ee_credentials(session_id)
    key=(session_id,start,end)
    with LOCK:
        if key in CACHE and CACHE[key][0] > time.time(): return CACHE[key][1]
        try:
            ee.Initialize(credentials=credentials, project='etmodule')
            assets = [resolve(start), resolve(end)]
            mines = load_geojson()
            geometries = [ee.Geometry(m['geometry']) for m in mines]
            roi = ee.FeatureCollection([ee.Feature(g) for g in geometries]).geometry()
            images = [ee.Image(asset).select('classification') for asset in assets]
            common = images[0].mask().And(images[1].mask())
            for image in images:
                invalid = image.neq(0).And(image.neq(1)).And(image.neq(2)).And(image.neq(3)).updateMask(common)
                check = invalid.reduceRegion(reducer=ee.Reducer.max(), geometry=roi, crs=GRID, crsTransform=TRANSFORM, maxPixels=1e9).getInfo()
                if any(v for v in check.values() if v is not None): raise HTTPException(422, 'Unexpected classification values found in the common valid area.')
            a,b = images
            transition = a.multiply(4).add(b).toInt().rename('code').updateMask(common).clip(roi)
            def aggregate(geometry):
                values = ee.Image.pixelArea().rename('area').addBands(transition).reduceRegion(reducer=ee.Reducer.sum().group(groupField=1,groupName='code'), geometry=geometry, crs=GRID, crsTransform=TRANSFORM, maxPixels=1e9, tileScale=4).getInfo()
                return summary(matrix_from_groups(values.get('groups')),end-start)
            per_mine = {mine['mine_id']:aggregate(geom) for mine,geom in zip(mines,geometries)}
            combined = aggregate(roi)
            tile = lambda image,vis: image.getMapId(vis)['tile_fetcher'].url_format
            layers = {'transitions':tile(transition,{'min':0,'max':15,'palette':PALETTE})}
            for code in range(16):
                layers[f'transition_{code}'] = tile(transition.eq(code).selfMask(), {'min':0,'max':1,'palette':[PALETTE[code], PALETTE[code]]})
            for name, target in [('mining',3),('vegetation',2),('water',0)]:
                # 0 unchanged; 1 loss; 2 gain. Other transitions are transparent.
                focused = ee.Image(0).where(a.eq(target).And(b.neq(target)),1).where(a.neq(target).And(b.eq(target)),2).updateMask(common).clip(roi)
                relevant = a.eq(target).Or(b.eq(target))
                layers[name] = tile(focused.updateMask(relevant),{'min':0,'max':2,'palette':['#bdbdbd','#d73027','#1a9850']})
            result = {'start_year':start,'end_year':end,'mines':mines,'summaries':{'all':combined,**per_mine},'layers':layers,'assets':assets,'grid':{'crs':GRID,'transform':TRANSFORM,'scale_m':30,'method':'nearest-neighbour; common valid mask; pixelArea grouped by transition'},'cache_seconds':1500}
            CACHE[key]=(time.time()+1500,result)
            return result
        except HTTPException: raise
        except Exception as exc:
            raise HTTPException(502, 'Earth Engine change detection failed. Check asset access and server logs.') from exc
