import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/task_2"
)

ALLOWED_LAYERS = [
    "zaf_adm0",
    "zaf_adm1",
    "zaf_water_areas_dcw",
    "zaf_water_lines_dcw",
    "zaf_osm_buildings"
]
