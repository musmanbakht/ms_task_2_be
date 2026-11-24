To Start The App Backend

python -m venv venv
venv/scripts/activate
pip install -r requirements.txt

uvicorn main:app --reload

GOOGLE EARTH ENGINE
Signup
CREATE a Project
REgister for nonn commercial use

Prerequisites:

Go to Google Cloud Console > APIs & Services > Credentials.
Create a new OAuth 2.0 Client ID:

Application type: Web application.
Authorized JavaScript origins: http://localhost:3000 (your React app URL).
Authorized redirect URIs: http://localhost:8000/api/auth/callback (your FastAPI endpoint).

Download the client secrets JSON (contains client_id and client_secret).
Enable the Earth Engine API in your project (if not already).
In the backend code, load the client secrets (e.g., from a file or env vars). I've shown it as a dict for simplicity.
Users must have signed up for Earth Engine (to grant the scope).

## Getis–Ord Gi* mining analysis

The existing `GET /api/mining/hotspot-analysis` serves the original GeoJSON mine records and precomputed grid results. If results are missing, a request with `session_id` starts one background preprocessing job using the credentials held in the existing Earth Engine session. A request without a session returns saved results and job status without starting a job. Credentials are passed to an isolated worker through stdin, not returned to the browser or placed in command-line arguments. The frontend keeps the data in a global in-memory store and polls job status without resending the session. The job status is process-local; the result file persists across restarts. The response contains `years`, `mines` (ROI geometry, source `statistics` and `time_series`), a mine-keyed `statistics` object, `hotspot_results` (GeoJSON FeatureCollection), `metadata`, `legends.hotspot`, `hotspot_status` and `hotspot_error`. Each grid feature includes its stable cell and mine IDs, year, mining fraction, valid coverage, Gi*, z score, raw and adjusted p values, category and unavailable reason. Metadata reports year/mine availability. Missing or invalid results return an empty collection and `not_computed`, not synthetic statistics. The original statistics file can be overridden with `MINING_CHANGE_GEOJSON`, and the results path with `GISTAR_RESULTS_PATH`.

### Preprocessing

Install `requirements.txt` and configure Earth Engine application-default or Earth Engine CLI credentials with read access to project `etmodule`, its raster assets and the existing ROI GeoJSON. From the backend directory run:

```sh
.venv/bin/python -m services.gistar_preprocess --cell-size 300 --minimum-coverage 0.8
```

The frontend flow starts the same workflow automatically after Earth Engine authentication when results are missing. The optional command above writes `classification_stats/gistar_results.geojson` atomically. Asset lookup tries the root and year folders (year folder first for 2023). It reports inaccessible assets by year. Re-run after updating assets; do not run on each API request. Use `--output` for another path and set `GISTAR_RESULTS_PATH` for the API. The output records generation time, settings, asset paths, library versions and per-mine/year availability. Credentials are never included.

The default grid is 300 m in EPSG:32735, globally aligned and clipped to each ROI; 300 m is ten nominal Landsat pixels per side and yields enough cells for local analysis, though it is an analytical choice rather than an established optimum. Edge pieces under 1% of a full cell are excluded. The same grid IDs and geometries are reused across years. One batch `reduceRegions` per year calculates mining-pixel area and valid-pixel area at nominal 30 m; cells below 80% valid coverage are excluded. Pixel areas, not counts, are used to compute the fraction.

Within each mine and year, binary queen neighbours include the cell itself; cells in other mines never become neighbours. PySAL `G_Local` computes Gi* and its analytical standardized z score. Two-sided normal-approximation p values are adjusted using Benjamini–Hochberg independently within each mine/year across valid tests. Categories use adjusted p thresholds of 0.10, 0.05 and 0.01, with z sign selecting hot or cold. This uses asymptotic inference, not permutation inference. Insufficient samples, zero variance, missing coverage, islands and invalid statistics are unavailable. Spatial autocorrelation may affect false-discovery guarantees, and these thresholds should not be interpreted as a temporal trend test.

**Resolution sensitivity remains to be assessed with actual data.** Run separate outputs at e.g. 150 m and 600 m and compare valid coverage and the spatial overlap and stability of significant categories against 300 m, accounting for changed cell sizes; do not overwrite the production file while comparing. This assessment requires accessible classification assets and has not been performed by the implementation tests.

Run `.venv/bin/python -m unittest discover -s tests -v` from the backend directory. Earth Engine-dependent preprocessing must be run separately with valid credentials; unit tests use synthetic data and cannot establish real-world statistical validity.

## Change detection

`GET /api/mining/change-detection?start_year=2000&end_year=2023&session_id=...` compares any chronological pair of the six supported years. The existing frontend route is `/change-detection`. The response supplies mine geometries, per-mine and union summaries, tile URLs, asset IDs and grid metadata. Matrix rows are initial class; columns are final class; units are km². Summaries describe only the common valid pixel area. Class gains and losses exclude the diagonal; annualised net changes divide by the actual year interval.

The comparison uses EPSG:32735 with a fixed 30 m affine transform `[30,0,0,0,-30,10000000]`; Earth Engine defaults to nearest-neighbour resampling for categorical inputs. It retains the intersection of both raster masks and verifies class values before grouping pixel area by `4 * initial + final`. The all-mines region is a geometric union. Outputs are cached in process memory per session and year pair for 25 minutes; they are recomputed afterwards. Map URLs are temporary. Source asset candidates are checked at the project root and year-specific folders. The historical mining chart uses the existing exported mine statistics and is not a replacement for the paired comparison.
