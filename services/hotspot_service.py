"""Combined mining-change data and temporary Earth Engine map references."""
import json
import logging
import os
import threading
import subprocess
import sys
import time
from pathlib import Path
from functools import lru_cache

import ee
from fastapi import HTTPException
from services.ee_auth import get_ee_credentials

YEARS = (2000, 2005, 2010, 2015, 2020, 2023)
ROOT = "projects/etmodule/assets/"
ROI_ASSET = ROOT + "AllMines_5km_ROIs"
PALETTE = ["#0000FF", "#D2B48C", "#008000", "#FF0000"]
LEGENDS = {
    "classification": [{"value": i, "label": name, "color": color} for i, (name, color) in enumerate(zip(("Water", "Barren", "Vegetation", "Mining"), PALETTE))],
    "mining_only": [{"value": 3, "label": "Mining", "color": "#FF0000"}],
    "concentration": [{"label": "0%", "color": "#fff7ec"}, {"label": ">0–5%", "color": "#fdd49e"}, {"label": ">5–20%", "color": "#fc8d59"}, {"label": ">20%", "color": "#b30000"}],
}
# Earth Engine's Python client is process-global; serialize initialization and requests.
_LOCK = threading.Lock()
_CACHE = {}
_JOB_LOCK = threading.Lock()
_JOB = {"status": "idle", "error": None}
CACHE_SECONDS = 1800


def candidates(year):
    base = f"AllMines_RF_Classification_{year}"
    return [ROOT + (f"{year}/" if year == 2023 else "") + base,
            ROOT + ("" if year == 2023 else f"{year}/") + base]


def load_geojson(path=None):
    path = Path(path or os.environ.get("MINING_CHANGE_GEOJSON", Path(__file__).resolve().parent.parent / "classification_stats" / "AllMines_MiningChange_2000_2023.geojson"))
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, f"Mining-change GeoJSON unavailable or invalid at {path}. Set MINING_CHANGE_GEOJSON to its local path.") from exc
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise HTTPException(503, "Mining-change GeoJSON must be a FeatureCollection.")
    mines = []
    for feature in data["features"]:
        p, geom = feature.get("properties") or {}, feature.get("geometry") or {}
        if p.get("mine_id") is None or not p.get("Mine_Name") or geom.get("type") not in ("Polygon", "MultiPolygon") or not geom.get("coordinates"):
            raise HTTPException(503, "Mining-change GeoJSON contains a mine without an ID, name, or polygon geometry.")
        time_series = {}
        for year in YEARS:
            keys = (f"mine_{year}", f"mine_pct_{year}", f"mine_km2_{year}")
            if any(not isinstance(p.get(key), (int, float)) or isinstance(p.get(key), bool) for key in keys):
                raise HTTPException(503, f"Mining-change GeoJSON is missing numerical values for {p['Mine_Name']} in {year}.")
            time_series[str(year)] = {"mining_fraction": p[keys[0]], "mining_percentage": p[keys[1]], "mining_area_km2": p[keys[2]]}
        mines.append({"mine_id": str(p["mine_id"]), "name": p["Mine_Name"], "geometry": geom, "statistics": p, "time_series": time_series})
    if len(mines) != 4 or len({m['mine_id'] for m in mines}) != 4:
        raise HTTPException(503, "Mining-change GeoJSON must contain four mines with unique IDs.")
    return mines


HOTSPOT_LEGEND = [
    {"value": "hotspot_99", "label": "Hotspot, 99% confidence", "color": "#99000d"},
    {"value": "hotspot_95", "label": "Hotspot, 95% confidence", "color": "#ef3b2c"},
    {"value": "hotspot_90", "label": "Hotspot, 90% confidence", "color": "#fcae91"},
    {"value": "not_significant", "label": "Not statistically significant", "color": "#d9d9d9"},
    {"value": "coldspot_90", "label": "Coldspot, 90% confidence", "color": "#bdd7e7"},
    {"value": "coldspot_95", "label": "Coldspot, 95% confidence", "color": "#3182bd"},
    {"value": "coldspot_99", "label": "Coldspot, 99% confidence", "color": "#08519c"},
    {"value": "unavailable", "label": "Unavailable", "color": "#737373"},
]

@lru_cache(maxsize=4)
def _read_results(path, modified_ns, size):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _results_path():
    return Path(os.environ.get("GISTAR_RESULTS_PATH", Path(__file__).resolve().parent.parent / "classification_stats" / "gistar_results.geojson"))


def _redact_worker_output(output, credential_json):
    """Remove credential values before writing worker diagnostics to server logs."""
    try:
        credentials = json.loads(credential_json)
    except (TypeError, ValueError):
        return "Worker diagnostics unavailable."
    for key in ("token", "refresh_token", "client_secret", "id_token"):
        value = credentials.get(key)
        if isinstance(value, str) and value:
            output = output.replace(value, "[redacted]")
    return output[-12000:]


def _run_job(credential_json, output_path):
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "services.gistar_preprocess", "--credentials-stdin", "--output", str(output_path)],
            input=credential_json, text=True, capture_output=True, timeout=3600,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if completed.returncode:
            diagnostics = _redact_worker_output(completed.stderr or "No diagnostic output from worker.", credential_json)
            logging.error("Gi* preprocessing worker exited with code %s. Diagnostic output:\n%s", completed.returncode, diagnostics)
            raise RuntimeError("Earth Engine analysis failed. Check server logs and asset permissions.")
        data = json.loads(output_path.read_text(encoding="utf-8"))
        if not any(f.get("properties", {}).get("category") not in (None, "unavailable") for f in data.get("features", [])):
            availability = data.get("metadata", {}).get("availability", {})
            logging.error("Gi* preprocessing produced no valid results. Year availability: %s", _redact_worker_output(json.dumps(availability), credential_json))
            raise RuntimeError("Analysis finished but no valid hotspot results were produced. Check server logs for year-specific details.")
        with _JOB_LOCK:
            _JOB.update(status="completed", error=None)
    except Exception as exc:
        logging.exception("Gi* preprocessing job failed")
        with _JOB_LOCK:
            _JOB.update(status="failed", error=str(exc) if isinstance(exc, RuntimeError) else "Analysis could not be completed. Check server logs.")


def _start_job(session_id, path):
    with _JOB_LOCK:
        if _JOB["status"] == "running":
            return
        credentials = get_ee_credentials(session_id)
        credential_json = credentials.to_json()
        _JOB.update(status="running", error=None)
        thread = threading.Thread(target=_run_job, args=(credential_json, path), daemon=True)
        try:
            thread.start()
        except Exception:
            _JOB.update(status="failed", error="Could not start analysis.")
            raise


def get_analysis(session_id=None):
    mines = load_geojson()
    path = _results_path()
    result = None
    reason = None
    try:
        stat = path.stat()
        result = _read_results(str(path), stat.st_mtime_ns, stat.st_size)
        if result.get("type") != "FeatureCollection" or not isinstance(result.get("features"), list) or not isinstance(result.get("metadata"), dict):
            raise ValueError("Invalid result format")
    except (OSError, ValueError, TypeError) as exc:
        if not isinstance(exc, FileNotFoundError):
            logging.warning("Precomputed Gi* results invalid or inaccessible: %s", exc)
        reason = "Precomputed Gi* results are missing or invalid; run the preprocessing workflow."
        result = {"type": "FeatureCollection", "features": [], "metadata": {}}
    available = any(f.get("properties", {}).get("category") not in (None, "unavailable") for f in result["features"])
    if not available and session_id:
        _start_job(session_id, path)
    with _JOB_LOCK:
        job = dict(_JOB)
    return {"analysis_type": "mining_change", "years": list(YEARS), "mines": mines,
            "statistics": {m["mine_id"]: m["statistics"] for m in mines},
            "hotspot_status": "available" if available else "not_computed",
            "hotspot_results": result, "hotspot_error": reason,
            "job": job, "metadata": result.get("metadata", {}), "legends": {"hotspot": HOTSPOT_LEGEND}}
