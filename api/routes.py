from fastapi import APIRouter, Depends, HTTPException, Response
from services.tiles_service import generate_mvt
from config import ALLOWED_LAYERS
from db.session import get_session
from services import earthengine_service

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

@router.post("/earthengine/get-map", response_model=None)

async def get_map(
    # product: str = Form(..., description="Product name, e.g., 'modis'"),
    # province: str = Form(None, description="Province name, e.g., 'Punjab'"),
    # geometry: str = Form(None, description="Geometry for map (optional)"),
    # input_date: str = Form(..., description="Input date, e.g., '2009-12-31'"),
    # palette: str = Form(None, description="Palette for map (optional)"),
    # session_id: str = Form(None, description="session_id"),
    # db: AsyncSession = Depends(get_db)
        product: str = "modis",
    province: str = "Punjab",
    geometry: str = None,
    input_date: str = "2009-12-31",
    palette: str = None,
    session_id: str = None, 
):
    # product = "modis"
    # province = "Punjab"
    # geometry = None
    # input_date = "2009-12-31"
    # palette = None
    print("HERE")
    return await earthengine_service.get_et_map(product, province, geometry, input_date, palette, session_id)
