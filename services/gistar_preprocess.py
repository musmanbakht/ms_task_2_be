"""Offline Gi* preprocessing. Run with Earth Engine application-default credentials."""
import argparse
import json
import math
import os
import tempfile
import sys
from google.oauth2.credentials import Credentials
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import ee
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, shape, mapping
from shapely.ops import transform
from scipy.stats import norm
from libpysal.weights import W
from esda.getisord import G_Local

from services.hotspot_service import YEARS, candidates, load_geojson

CRS = 'EPSG:32735'  # WGS 84 / UTM 35S, suitable for the study area.
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / 'classification_stats' / 'gistar_results.geojson'

def make_grid(mines, size):
    forward = Transformer.from_crs('EPSG:4326', CRS, always_xy=True).transform
    reverse = Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True).transform
    result = []
    for mine in mines:
        roi = transform(forward, shape(mine['geometry']))
        minx, miny, maxx, maxy = roi.bounds
        for x in range(math.floor(minx / size), math.ceil(maxx / size)):
            for y in range(math.floor(miny / size), math.ceil(maxy / size)):
                cell = roi.intersection(box(x*size, y*size, (x+1)*size, (y+1)*size))
                if cell.is_empty or cell.area < size*size*0.01:
                    continue
                result.append({'type': 'Feature', 'geometry': mapping(transform(reverse, cell)), 'properties': {'cell_id': f"{mine['mine_id']}:{x}:{y}", 'mine_id': mine['mine_id'], 'grid_x': x, 'grid_y': y, 'cell_area_m2': cell.area}})
    return result

def queen_weights(cells):
    positions = {(c['properties']['grid_x'], c['properties']['grid_y']): i for i, c in enumerate(cells)}
    neighbors = {}
    for i, c in enumerate(cells):
        x, y = c['properties']['grid_x'], c['properties']['grid_y']
        neighbors[i] = [positions[(x+dx,y+dy)] for dx in (-1,0,1) for dy in (-1,0,1) if (x+dx,y+dy) in positions]
    return W(neighbors, silence_warnings=True)

def adjust_bh(p):
    order = np.argsort(p); out = np.empty(len(p)); prev = 1.0
    for rank in range(len(p), 0, -1):
        idx = order[rank-1]; prev = min(prev, float(p[idx])*len(p)/rank); out[idx] = prev
    return out

def category(z, p):
    if z is None or p is None or not np.isfinite(z) or not np.isfinite(p): return 'unavailable'
    threshold = '99' if p <= .01 else '95' if p <= .05 else '90' if p <= .10 else None
    return ('hotspot_' if z > 0 else 'coldspot_') + threshold if threshold and z != 0 else 'not_significant'

def calculate(cells, year, values, minimum_coverage):
    output = []
    for cell in cells:
        data = values.get(cell['properties']['cell_id'], {})
        valid = data.get('valid_area') or 0
        expected = data.get('total_area') or 0
        fraction = data.get('mining_area', 0) / valid if valid else None
        coverage = min(1.0, valid/expected) if expected else 0.0
        props = {**cell['properties'], 'year': year, 'mining_fraction': fraction, 'valid_coverage': coverage, 'gi_star': None, 'z_score': None, 'p_value': None, 'adjusted_p_value': None, 'category': 'unavailable', 'reason': 'insufficient_valid_coverage' if coverage < minimum_coverage else None}
        output.append({'type':'Feature', 'geometry':cell['geometry'], 'properties':props})
    for mine_id in sorted({c['properties']['mine_id'] for c in output}):
        group = [f for f in output if f['properties']['mine_id'] == mine_id and f['properties']['mining_fraction'] is not None and f['properties']['valid_coverage'] >= minimum_coverage]
        if len(group) < 4 or np.std([f['properties']['mining_fraction'] for f in group]) < 1e-12:
            for f in group: f['properties']['reason'] = 'insufficient_cells_or_zero_variance'
            continue
        weights = queen_weights(group)
        valid_indices = [i for i in range(len(group)) if len(weights.neighbors[i]) > 1 and len(weights.neighbors[i]) < len(group)]
        try:
            model = G_Local(np.array([f['properties']['mining_fraction'] for f in group]), weights, transform='B', star=True, permutations=0)
            finite = [i for i in valid_indices if np.isfinite(model.Zs[i]) and np.isfinite(model.Gs[i])]
            pvalues = [min(1.0, 2*norm.sf(abs(model.Zs[i]))) for i in finite]
            adjusted = adjust_bh(pvalues)
            for j, i in enumerate(finite):
                z = float(model.Zs[i]); group[i]['properties'].update(gi_star=float(model.Gs[i]), z_score=z, p_value=float(pvalues[j]), adjusted_p_value=float(adjusted[j]), category=category(z, adjusted[j]), reason=None)
            for i in set(range(len(group))) - set(finite): group[i]['properties']['reason'] = 'invalid_neighborhood_or_statistic'
        except (ValueError, ZeroDivisionError, FloatingPointError):
            for f in group: f['properties']['reason'] = 'statistical_calculation_failed'
    return output

def extract(image, cells):
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(c['geometry']), {'cell_id': c['properties']['cell_id']}) for c in cells])
    area = ee.Image.pixelArea()
    valid = image.mask().reduce(ee.Reducer.min()).unmask(0).gt(0)
    bands = area.rename('total_area').addBands(area.updateMask(valid).unmask(0).rename('valid_area')).addBands(area.updateMask(image.eq(3)).unmask(0).rename('mining_area'))
    features = bands.reduceRegions(collection=fc, reducer=ee.Reducer.sum(), scale=30, crs=CRS, tileScale=4).getInfo()['features']
    return {f['properties']['cell_id']: f['properties'] for f in features}

def credentials_from_session(info):
    # A newly authorised session may have an access token without a refresh token.
    # from_authorized_user_info requires a refresh token and rejects such sessions.
    if not info.get('token') and not info.get('refresh_token'):
        raise ValueError('The Earth Engine session has no usable credentials. Reconnect to Google Earth Engine.')
    return Credentials(
        token=info.get('token'), refresh_token=info.get('refresh_token'),
        token_uri=info.get('token_uri'), client_id=info.get('client_id'),
        client_secret=info.get('client_secret'), scopes=info.get('scopes'),
    )


def run(args, credentials=None):
    if args.cell_size <= 0 or not 0 < args.minimum_coverage <= 1: raise ValueError('Invalid cell size or coverage threshold')
    ee.Initialize(credentials=credentials, project='etmodule')
    cells = make_grid(load_geojson(), args.cell_size)
    all_features, availability = [], {}
    for year in YEARS:
        try:
            asset = None
            for candidate in candidates(year):
                try:
                    if ee.data.getAsset(candidate): asset = candidate; break
                except Exception as exc:
                    if 'not found' not in str(exc).lower() and '404' not in str(exc): raise
            if not asset: raise ValueError('Classification asset not found')
            year_features = calculate(cells, year, extract(ee.Image(asset).select('classification'), cells), args.minimum_coverage)
            all_features.extend(year_features)
            mine_status = {}
            for mine in {c['properties']['mine_id'] for c in cells}:
                subset = [c for c in year_features if c['properties']['mine_id'] == mine]
                valid_count = sum(c['properties']['category'] != 'unavailable' for c in subset)
                mine_status[mine] = {'status': 'available' if valid_count else 'unavailable', 'valid_cells': valid_count, 'total_cells': len(subset), 'reason': None if valid_count else 'No cells had sufficient coverage and a valid statistic.'}
            availability[str(year)] = {'status':'available' if any(v['valid_cells'] for v in mine_status.values()) else 'unavailable', 'asset':asset, 'mines':mine_status}

        except Exception as exc:
            availability[str(year)] = {'status':'unavailable', 'reason':str(exc)}
    output = {'type':'FeatureCollection', 'features':all_features, 'metadata': {'created_at':datetime.now(timezone.utc).isoformat(), 'grid_crs':CRS, 'cell_size_m':args.cell_size, 'minimum_valid_coverage':args.minimum_coverage, 'weights':'binary queen contiguity including self, within mine', 'reference_population':'valid cells within each mine and year', 'inference':'two-sided normal approximation, Benjamini-Hochberg per mine and year', 'permutations':0, 'seed':None, 'versions':{p:version(p) for p in ('numpy','scipy','libpysal','esda','shapely','pyproj','earthengine-api')}, 'availability':availability}}
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False, encoding='utf8') as f:
        json.dump(output, f, allow_nan=False); temp = f.name
    os.replace(temp, target)
    print(f'Saved {len(all_features)} grid records to {target}; availability: {availability}')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cell-size', type=int, default=300)
    parser.add_argument('--minimum-coverage', type=float, default=0.8)
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--credentials-stdin', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    credentials = credentials_from_session(json.load(sys.stdin)) if args.credentials_stdin else None
    run(args, credentials=credentials)
