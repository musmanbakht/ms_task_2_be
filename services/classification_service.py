from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional, Any

import ee
from fastapi import HTTPException

from services.ee_auth import get_ee_credentials


# ============================================================
# EARTH ENGINE ASSETS / SETTINGS
# ============================================================

MINE_ROIS_ASSET = (
    "projects/etmodule/assets/AllMines_5km_ROIs"
)

LANDSAT_COLLECTION = (
    "LANDSAT/LE07/C02/T1_L2"
)


CLASSIFIER_PERIODS = [
    {
        "start_year": 2015,
        "end_year": 2019,
        "classifier_year": 2015,
        "asset": (
            "projects/etmodule/assets/classifiers/"
            "RF_Classifier_2015"
        ),
    },
    {
        "start_year": 2020,
        "end_year": 2022,
        "classifier_year": 2020,
        "asset": (
            "projects/etmodule/assets/classifiers/"
            "RF_Classifier_2020"
        ),
    },
    {
        "start_year": 2023,
        "end_year": 2023,
        "classifier_year": 2023,
        "asset": (
            "projects/etmodule/assets/classifiers/"
            "RF_Classifier_2023"
        ),
    },
]


# Search configuration
SEARCH_WINDOW_DAYS = 90

CLOUD_COVER_THRESHOLD = 60

SCENES_BEFORE = 2
SCENES_AFTER = 2

PREFERRED_SCENE_COUNT = 4
MINIMUM_SCENE_COUNT = 3


# ============================================================
# CLASS INFORMATION
# ============================================================

CLASS_INFO = [
    {
        "value": 0,
        "name": "Water",
        "color": "#0064C8",
    },
    {
        "value": 1,
        "name": "Barren",
        "color": "#B4B4B4",
    },
    {
        "value": 2,
        "name": "Vegetation",
        "color": "#006400",
    },
    {
        "value": 3,
        "name": "Mining",
        "color": "#D7191C",
    },
]


CLASS_VIS_PARAMS = {
    "min": 0,
    "max": 3,
    "palette": [
        "0064C8",
        "B4B4B4",
        "006400",
        "D7191C",
    ],
}


# ============================================================
# PREDICTOR BANDS
# Must remain identical to the classifier training scripts.
# ============================================================

PREDICTOR_BANDS = [
    "SR_B1",
    "SR_B2",
    "SR_B3",
    "SR_B4",
    "SR_B5",
    "SR_B7",
    "NDVI",
    "NDWI",
    "NDBI",
    "BSI",
    "MNDWI",
    "SWIR_RATIO",
    "SOIL_RATIO",
    "ALBEDO",
    "DARK_INDEX",
    "ELEVATION",
    "SLOPE",
]


# ============================================================
# PUBLIC SERVICE FUNCTION
# ============================================================

async def get_classification_map(
    session_id: str,
    request_date: date,
    mine_id: Optional[str] = None,
) -> dict:
    """
    Build an on-demand supervised classification map.

    The expensive/synchronous Earth Engine operations are run
    in a thread so they do not block FastAPI's event loop.
    """

    try:
        return await asyncio.to_thread(
            _get_classification_map_sync,
            session_id,
            request_date,
            mine_id,
        )

    except HTTPException:
        raise

    except ee.EEException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Earth Engine error: {str(exc)}",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Classification failed: {str(exc)}",
        )


# ============================================================
# MAIN CLASSIFICATION PIPELINE
# ============================================================

def _get_classification_map_sync(
    session_id: str,
    request_date: date,
    mine_id: Optional[str],
) -> dict:

    # --------------------------------------------------------
    # 1. EARTH ENGINE AUTHENTICATION
    # --------------------------------------------------------

    credentials = get_ee_credentials(session_id)

    ee.Initialize(
        credentials=credentials,    project="etmodule",
        
    )
    print(
            "MINE ASSET:",
    ee.data.getAsset(
        "projects/etmodule/assets/AllMines_5km_ROIs"
    )
)


    # --------------------------------------------------------
    # 2. DETERMINE CLASSIFIER
    # --------------------------------------------------------

    classifier_config = _resolve_classifier(
        request_date
    )

    classifier_year = (
        classifier_config["classifier_year"]
    )

    classifier_asset = (
        classifier_config["asset"]
    )

    classifier = ee.Classifier.load(
        classifier_asset
    )


    # --------------------------------------------------------
    # 3. LOAD MINE ROIs
    # --------------------------------------------------------

    mine_rois = ee.FeatureCollection(
        MINE_ROIS_ASSET
    )


    classified_images = []
    mines_response = []


    # ========================================================
    # SINGLE MINE REQUEST
    # ========================================================

    if mine_id is not None:

        mine_feature = _get_mine_feature(
            mine_rois,
            mine_id,
        )

        mine_geometry = mine_feature.geometry()

        mine_name = (
            mine_feature
            .get("Mine_Name")
            .getInfo()
        )

        result = _classify_geometry(
            geometry=mine_geometry,
            request_date=request_date,
            classifier=classifier,
        )

        classified_images.append(
            result["classified_image"]
        )

        mines_response.append({
            "mineId": str(mine_id),
            "mineName": mine_name,
            "sceneCount": len(
                result["scenes"]
            ),
            "scenes": result["scenes"],
        })

        final_geometry = mine_geometry

        scope = "single_mine"


    # ========================================================
    # ALL MINES REQUEST
    # ========================================================

    else:

        mine_ids = (
            mine_rois
            .aggregate_array("mine_id")
            .getInfo()
        )

        if not mine_ids:
            raise HTTPException(
                status_code=404,
                detail="No mine ROIs were found.",
            )

        for current_mine_id in mine_ids:

            mine_feature = _get_mine_feature(
                mine_rois,
                str(current_mine_id),
            )

            mine_geometry = (
                mine_feature.geometry()
            )

            mine_name = (
                mine_feature
                .get("Mine_Name")
                .getInfo()
            )

            result = _classify_geometry(
                geometry=mine_geometry,
                request_date=request_date,
                classifier=classifier,
            )

            classified_images.append(
                result["classified_image"]
            )

            mines_response.append({
                "mineId": str(
                    current_mine_id
                ),
                "mineName": mine_name,
                "sceneCount": len(
                    result["scenes"]
                ),
                "scenes": result["scenes"],
            })


        # Each mine may use different Landsat scenes.
        # Mosaic the already-classified mine images.
        final_geometry = mine_rois.geometry()

        scope = "all_mines"


    # --------------------------------------------------------
    # 4. COMBINE CLASSIFIED IMAGES
    # --------------------------------------------------------

    if len(classified_images) == 1:

        final_classified_image = (
            classified_images[0]
        )

    else:

        final_classified_image = (
            ee.ImageCollection.fromImages(
                classified_images
            )
            .mosaic()
            .rename("classification")
        )


    # --------------------------------------------------------
    # 5. GENERATE EARTH ENGINE MAP TILE
    # --------------------------------------------------------

    map_id = final_classified_image.getMapId(
        CLASS_VIS_PARAMS
    )

    tile_url = (
        map_id["tile_fetcher"]
        .url_format
    )


    # --------------------------------------------------------
    # 6. GET BOUNDING BOX
    # --------------------------------------------------------

    bounding_box = _get_bounding_box(
        final_geometry
    )


    # --------------------------------------------------------
    # 7. RETURN MAP RESPONSE
    # --------------------------------------------------------

    return {
        "status": 200,

        "requestedDate": (
            request_date.isoformat()
        ),

        "scope": scope,

        "classifier": {
    "modelYear": classifier_year,
    "periodStartYear": (
        classifier_config["start_year"]
    ),
    "periodEndYear": (
        classifier_config["end_year"]
    ),
    "asset": classifier_asset,
},

        "imagery": {
            "collection": (
                LANDSAT_COLLECTION
            ),

            "searchWindowDays": (
                SEARCH_WINDOW_DAYS
            ),

            "cloudCoverThreshold": (
                CLOUD_COVER_THRESHOLD
            ),

            "preferredSceneCount": (
                PREFERRED_SCENE_COUNT
            ),

            "minimumSceneCount": (
                MINIMUM_SCENE_COUNT
            ),

            "compositeMethod": "median",
        },

        "classes": CLASS_INFO,

        "mines": mines_response,

        "tileUrl": tile_url,

        "boundingBox": bounding_box,
    }


# ============================================================
# CLASSIFIER RESOLUTION
# ============================================================

def _resolve_classifier(
    request_date: date,
) -> dict:

    year = request_date.year

    for period in CLASSIFIER_PERIODS:

        if (
            period["start_year"]
            <= year
            <= period["end_year"]
        ):
            return period

    configured_periods = [
        (
            f"{period['start_year']}-"
            f"{period['end_year']}"
        )
        for period in CLASSIFIER_PERIODS
    ]

    raise HTTPException(
        status_code=400,
        detail=(
            f"No classifier is configured "
            f"for year {year}. "
            f"Configured periods: "
            f"{', '.join(configured_periods)}"
        ),
    )


# ============================================================
# MINE LOOKUP
# ============================================================

def _get_mine_feature(
    mine_rois: ee.FeatureCollection,
    mine_id: str,
) -> ee.Feature:

    filtered = mine_rois.filter(
        ee.Filter.eq(
            "mine_id",
            mine_id,
        )
    )

    mine_count = filtered.size().getInfo()

    if mine_count == 0:

        raise HTTPException(
            status_code=404,
            detail=(
                f"Mine ID '{mine_id}' "
                f"was not found."
            ),
        )

    return ee.Feature(
        filtered.first()
    )


# ============================================================
# CLASSIFY ONE GEOMETRY / MINE
# ============================================================

def _classify_geometry(
    geometry: ee.Geometry,
    request_date: date,
    classifier: ee.Classifier,
) -> dict:

    # --------------------------------------------------------
    # Select 3-4 Landsat images around requested date
    # --------------------------------------------------------

    selected_collection, scenes = (
        _select_landsat_scenes(
            geometry=geometry,
            request_date=request_date,
        )
    )


    # --------------------------------------------------------
    # Apply same preprocessing as training scripts
    # --------------------------------------------------------

    prepared_collection = (
        selected_collection
        .map(_prepare_landsat7)
    )


    # --------------------------------------------------------
    # Median stack to reduce Landsat 7 SLC-off gaps
    # --------------------------------------------------------

    landsat_composite = (
        prepared_collection
        .median()
    )


    # --------------------------------------------------------
    # Re-create same 17 predictor bands
    # --------------------------------------------------------

    predictor_image = (
        _build_predictor_image(
            landsat_composite
        )
    )


    # --------------------------------------------------------
    # Apply saved Random Forest classifier
    # --------------------------------------------------------

    classified_image = (
        predictor_image
        .select(PREDICTOR_BANDS)
        .classify(classifier)
        .rename("classification")
        .clip(geometry)
    )


    return {
        "classified_image": (
            classified_image
        ),
        "scenes": scenes,
    }


# ============================================================
# LANDSAT SCENE SELECTION
# ============================================================

def _select_landsat_scenes(
    geometry: ee.Geometry,
    request_date: date,
) -> tuple[ee.ImageCollection, list[dict]]:

    target_date = ee.Date(
        request_date.isoformat()
    )

    search_start = target_date.advance(
        -SEARCH_WINDOW_DAYS,
        "day",
    )

    search_end = target_date.advance(
        SEARCH_WINDOW_DAYS,
        "day",
    )


    # --------------------------------------------------------
    # Base Landsat collection
    # --------------------------------------------------------

    collection = (
        ee.ImageCollection(
            LANDSAT_COLLECTION
        )
        .filterBounds(geometry)
        .filterDate(
            search_start,
            search_end,
        )
        .filter(
            ee.Filter.lt(
                "CLOUD_COVER",
                CLOUD_COVER_THRESHOLD,
            )
        )
    )


    # --------------------------------------------------------
    # Two closest images BEFORE requested date
    # --------------------------------------------------------

    before = (
        collection
        .filterDate(
            search_start,
            target_date,
        )
        .sort(
            "system:time_start",
            False,
        )
        .limit(
            SCENES_BEFORE
        )
    )


    # --------------------------------------------------------
    # Two closest images AFTER requested date
    #
    # filterDate start is inclusive, therefore an image
    # acquired exactly on the requested date is included here.
    # --------------------------------------------------------

    after = (
        collection
        .filterDate(
            target_date,
            search_end,
        )
        .sort(
            "system:time_start",
            True,
        )
        .limit(
            SCENES_AFTER
        )
    )


    # --------------------------------------------------------
    # Preferred balanced stack:
    # 2 before + 2 after
    # --------------------------------------------------------

    selected = (
        before
        .merge(after)
        .sort("system:time_start")
    )

    selected_count = (
        selected
        .size()
        .getInfo()
    )


    # --------------------------------------------------------
    # If the balanced search gives fewer than 3,
    # use the 3 nearest available scenes in the ±90 day
    # window regardless of which side they fall on.
    # --------------------------------------------------------

    if selected_count < MINIMUM_SCENE_COUNT:

        target_millis = (
            target_date.millis()
        )

        def add_date_distance(
            image: ee.Image,
        ) -> ee.Image:

            image = ee.Image(image)

            distance = (
                ee.Number(
                    image.get(
                        "system:time_start"
                    )
                )
                .subtract(target_millis)
                .abs()
            )

            return image.set(
                "_date_distance_ms",
                distance,
            )


        selected = (
            collection
            .map(add_date_distance)
            .sort("_date_distance_ms")
            .limit(
                MINIMUM_SCENE_COUNT
            )
            .sort(
                "system:time_start"
            )
        )

        selected_count = (
            selected
            .size()
            .getInfo()
        )


    # --------------------------------------------------------
    # Still fewer than 3 -> cannot make reliable stack
    # --------------------------------------------------------

    if selected_count < MINIMUM_SCENE_COUNT:

        raise HTTPException(
            status_code=422,
            detail=(
                "Insufficient Landsat 7 imagery. "
                f"Only {selected_count} usable "
                "scene(s) were found within "
                f"±{SEARCH_WINDOW_DAYS} days "
                "with CLOUD_COVER < "
                f"{CLOUD_COVER_THRESHOLD}%."
            ),
        )


    # --------------------------------------------------------
    # Get lightweight scene metadata for frontend
    # --------------------------------------------------------

    scenes = _get_scene_metadata(
        selected
    )


    return selected, scenes


# ============================================================
# SCENE METADATA
# ============================================================

def _get_scene_metadata(
    collection: ee.ImageCollection,
) -> list[dict]:

    def attach_metadata(
        image: ee.Image,
    ) -> ee.Image:

        image = ee.Image(image)

        scene_metadata = ee.Dictionary({
            "sceneId": (
                image.get(
                    "system:index"
                )
            ),

            "date": (
                ee.Date(
                    image.get(
                        "system:time_start"
                    )
                )
                .format("YYYY-MM-dd")
            ),

            "cloudCover": (
                image.get(
                    "CLOUD_COVER"
                )
            ),
        })

        return image.set(
            "_api_scene_metadata",
            scene_metadata,
        )


    metadata = (
        collection
        .map(attach_metadata)
        .aggregate_array(
            "_api_scene_metadata"
        )
        .getInfo()
    )


    result = []

    for scene in metadata:

        cloud_cover = (
            scene.get("cloudCover")
        )

        result.append({
            "sceneId": str(
                scene.get("sceneId")
            ),

            "date": scene.get("date"),

            "cloudCover": (
                float(cloud_cover)
                if cloud_cover is not None
                else None
            ),
        })


    return result


# ============================================================
# LANDSAT 7 PREPROCESSING
#
# Direct translation of the working MSc GEE scripts.
# ============================================================

def _prepare_landsat7(
    image: ee.Image,
) -> ee.Image:

    image = ee.Image(image)

    qa = image.select(
        "QA_PIXEL"
    )


    # Remove:
    # bit 0 = fill
    # bit 1 = dilated cloud
    # bit 3 = cloud
    # bit 4 = cloud shadow
    clear = (
        qa.bitwiseAnd(1 << 0)
        .eq(0)
        .And(
            qa.bitwiseAnd(1 << 1)
            .eq(0)
        )
        .And(
            qa.bitwiseAnd(1 << 3)
            .eq(0)
        )
        .And(
            qa.bitwiseAnd(1 << 4)
            .eq(0)
        )
    )


    # Remove radiometrically saturated pixels
    saturation_mask = (
        image
        .select("QA_RADSAT")
        .eq(0)
    )


    # Landsat Collection 2 Level 2 scaling
    optical = (
        image
        .select([
            "SR_B1",
            "SR_B2",
            "SR_B3",
            "SR_B4",
            "SR_B5",
            "SR_B7",
        ])
        .multiply(0.0000275)
        .add(-0.2)
    )


    return (
        optical
        .updateMask(clear)
        .updateMask(
            saturation_mask
        )
        .copyProperties(
            image,
            [
                "system:time_start",
                "system:index",
                "CLOUD_COVER",
            ],
        )
    )


# ============================================================
# PREDICTOR IMAGE
#
# Direct Python translation of the predictor logic used
# when the RF classifiers were trained.
# ============================================================

def _build_predictor_image(
    landsat: ee.Image,
) -> ee.Image:

    # --------------------------------------------------------
    # NDVI
    # --------------------------------------------------------

    ndvi = (
        landsat
        .normalizedDifference([
            "SR_B4",
            "SR_B3",
        ])
        .rename("NDVI")
    )


    # --------------------------------------------------------
    # NDWI
    # --------------------------------------------------------

    ndwi = (
        landsat
        .normalizedDifference([
            "SR_B2",
            "SR_B4",
        ])
        .rename("NDWI")
    )


    # --------------------------------------------------------
    # NDBI
    # --------------------------------------------------------

    ndbi = (
        landsat
        .normalizedDifference([
            "SR_B5",
            "SR_B4",
        ])
        .rename("NDBI")
    )


    # --------------------------------------------------------
    # Bare Soil Index
    # --------------------------------------------------------

    bsi = landsat.expression(
        (
            "((SWIR + RED) - "
            "(NIR + BLUE)) / "
            "((SWIR + RED) + "
            "(NIR + BLUE))"
        ),
        {
            "SWIR": landsat.select(
                "SR_B5"
            ),
            "RED": landsat.select(
                "SR_B3"
            ),
            "NIR": landsat.select(
                "SR_B4"
            ),
            "BLUE": landsat.select(
                "SR_B1"
            ),
        },
    ).rename("BSI")


    # --------------------------------------------------------
    # MNDWI
    # --------------------------------------------------------

    mndwi = (
        landsat
        .normalizedDifference([
            "SR_B2",
            "SR_B5",
        ])
        .rename("MNDWI")
    )


    # --------------------------------------------------------
    # SWIR ratio
    # --------------------------------------------------------

    swir_ratio = (
        landsat
        .select("SR_B5")
        .divide(
            landsat.select(
                "SR_B7"
            )
        )
        .rename("SWIR_RATIO")
    )


    # --------------------------------------------------------
    # Soil ratio
    # --------------------------------------------------------

    soil_ratio = (
        landsat
        .select("SR_B4")
        .divide(
            landsat.select(
                "SR_B5"
            )
        )
        .rename("SOIL_RATIO")
    )


    # --------------------------------------------------------
    # Albedo
    # --------------------------------------------------------

    albedo = landsat.expression(
        (
            "0.356 * B1 + "
            "0.130 * B3 + "
            "0.373 * B4 + "
            "0.085 * B5 + "
            "0.072 * B7 - "
            "0.0018"
        ),
        {
            "B1": landsat.select(
                "SR_B1"
            ),
            "B3": landsat.select(
                "SR_B3"
            ),
            "B4": landsat.select(
                "SR_B4"
            ),
            "B5": landsat.select(
                "SR_B5"
            ),
            "B7": landsat.select(
                "SR_B7"
            ),
        },
    ).rename("ALBEDO")


    # --------------------------------------------------------
    # Dark Index
    # --------------------------------------------------------

    dark_index = landsat.expression(
        (
            "(SWIR2 - RED) / "
            "(SWIR2 + RED)"
        ),
        {
            "SWIR2": landsat.select(
                "SR_B7"
            ),
            "RED": landsat.select(
                "SR_B3"
            ),
        },
    ).rename("DARK_INDEX")


    # --------------------------------------------------------
    # Terrain
    # --------------------------------------------------------

    srtm = ee.Image(
        "USGS/SRTMGL1_003"
    )

    elevation = (
        srtm
        .select("elevation")
        .rename("ELEVATION")
    )

    slope = (
        ee.Terrain
        .slope(srtm)
        .rename("SLOPE")
    )


    # --------------------------------------------------------
    # Final 17-band predictor image
    # --------------------------------------------------------

    predictor_image = (
        landsat
        .addBands(ndvi)
        .addBands(ndwi)
        .addBands(ndbi)
        .addBands(bsi)
        .addBands(mndwi)
        .addBands(swir_ratio)
        .addBands(soil_ratio)
        .addBands(albedo)
        .addBands(dark_index)
        .addBands(elevation)
        .addBands(slope)
    )


    return predictor_image


# ============================================================
# MAP BOUNDS
# ============================================================

def _get_bounding_box(
    geometry: ee.Geometry,
) -> list[float]:

    coordinates = (
        geometry
        .bounds()
        .coordinates()
        .getInfo()[0]
    )

    longitudes = [
        point[0]
        for point in coordinates
    ]

    latitudes = [
        point[1]
        for point in coordinates
    ]

    return [
        min(longitudes),
        min(latitudes),
        max(longitudes),
        max(latitudes),
    ]
