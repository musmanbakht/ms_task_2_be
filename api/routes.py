from fastapi import APIRouter, Depends, HTTPException, Response
from services.tiles_service import generate_mvt
from config import ALLOWED_LAYERS
from db.session import get_session

router = APIRouter()

@router.get("/tiles/{layer}/{z}/{x}/{y}.mvt")
async def get_mvt_tile(
    layer: str,
    z: int,
    x: int,
    y: int,
    session=Depends(get_session)
):
    if layer not in ALLOWED_LAYERS:
        raise HTTPException(status_code=403, detail="Layer not allowed")
    if not (0 <= z <= 22):
        raise HTTPException(status_code=400, detail="Zoom out of range")
    try:
        mvt = await generate_mvt(session, layer, z, x, y)
        return Response(
            content=mvt,
            media_type="application/x-protobuf",
            headers={
                "Access-Control-Allow-Origin": "*",
                # "Cache-Control": "public, max-age=86400"
                            # Prevent caching during development
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate tile: {e}")
