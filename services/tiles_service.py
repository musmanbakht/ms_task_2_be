from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import logging
from sqlalchemy.ext.asyncio import AsyncSession
import re

def quote_identifier(layer):
    # Only allows letters, numbers, dots, and underscores
    if not layer or not re.match(r'^[a-zA-Z0-9_.]+$', layer):
        raise ValueError("Invalid layer name")
    return ".".join([f'"{part}"' for part in layer.split('.')])


async def generate_mvt(session: AsyncSession, layer_name: str, z: int, x: int, y: int):
    try:
        print("HEREEEEE")
        table_name = quote_identifier(layer_name)
        sql = text(f"""
            SELECT ST_AsMVT(tile, :layer_name, 4096, 'geom_mvt') AS mvt
            FROM (
                SELECT
                    ST_AsMVTGeom(
                        ST_Transform(geom, 3857),
                        ST_TileEnvelope(:z, :x, :y),
                        4096,
                        256,
                        TRUE
                    ) AS geom_mvt,
                    *
                FROM {table_name}
                WHERE ST_Intersects(
                    geom,
                    ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)
                )
            ) AS tile
            WHERE geom_mvt IS NOT NULL;
        """)

        result = await session.execute(
            sql,
            {"layer_name": layer_name, "z": z, "x": x, "y": y}
        )
        row = result.fetchone()
        # Return empty bytes if no data
        return row.mvt if row and row.mvt else b""
    except Exception as exc:
        logging.error(f"Error in generate_mvt: {exc}")
        # Optionally, you can raise a custom error or FastAPI HTTPException here
        # from fastapi import HTTPException
        # raise HTTPException(status_code=500, detail=f"Failed to generate MVT: {str(exc)}")
        return b""  # Or raise, or propagate the error depending on your use-case
# async def generate_mvt(session: AsyncSession, layer_name: str, z: int, x: int, y: int):
#     print("HEREEEEE")
#     table_name = quote_identifier(layer_name)
#     sql = text(f"""
#         SELECT ST_AsMVT(tile, :layer_name, 4096, 'geom_mvt') AS mvt
#         FROM (
#             SELECT
#                 ST_AsMVTGeom(
#                     ST_Transform(geom, 3857),
#                     ST_TileEnvelope(:z, :x, :y),
#                     4096,
#                     256,
#                     TRUE
#                 ) AS geom_mvt,
#                 *
#             FROM {table_name}
#             WHERE ST_Intersects(
#                 geom,
#                 ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)
#             )
#         ) AS tile
#         WHERE geom_mvt IS NOT NULL;
#     """)

#     result = await session.execute(
#         sql,
#         {"layer_name": layer_name, "z": z, "x": x, "y": y}
#     )
#     row = result.fetchone()
#     return row.mvt if row and row.mvt else b""

async def get_layer_metadata(session: AsyncSession, layer_name: str):
    table_name = quote_identifier(layer_name)
    sql = text(f"""
        SELECT
            ST_SRID(geom) AS srid,
            ST_GeometryType(geom) AS geometry_type,
            ST_Extent(geom) AS extent
        FROM {table_name}
        LIMIT 1;
    """)
    result = await session.execute(sql)
    return result.fetchone()
