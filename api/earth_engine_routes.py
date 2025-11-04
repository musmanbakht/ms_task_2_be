from fastapi import APIRouter, Depends, Body, Form, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import get_db
from services import layer_service, earthengine_service
from typing import Dict, Optional
from db.schemas import LayerCreate

router = APIRouter()

@router.get("/earthengine", response_model=None)
async def authenticate( user_id: int = 1, db: AsyncSession = Depends(get_db)) -> Dict[str, any]:
    return await earthengine_service.authenticate_user_earth_engine( user_id, db)
@router.get("/earthengine/check-key/{user_id}", response_model=None)
async def check( user_id: int, db: AsyncSession = Depends(get_db)) -> Dict[str, any]:
    return await earthengine_service.check_earth_engine_key_exists( user_id, db )
@router.get("/earthengine/product-dates/{product_name}", response_model=None)
async def check( product_name, db: AsyncSession = Depends(get_db)) -> Dict[str, any]:
    return await earthengine_service.get_start_and_end_date( product_name, db )
# Route Not working for now
@router.post("/earthengine/upload-key/{user_id}", response_model=None)
async def upload_ee_json( user_id , file: UploadFile = File(...),db: AsyncSession = Depends(get_db)):
    print("HERE")
    return await earthengine_service.store_creds(user_id, file , db)
@router.get("/earthengine/get-map", response_model=None)

async def get_map(
    product: str = Form(..., description="Product name, e.g., 'modis'"),
    province: str = Form(None, description="Province name, e.g., 'Punjab'"),
    geometry: str = Form(None, description="Geometry for map (optional)"),
    input_date: str = Form(..., description="Input date, e.g., '2009-12-31'"),
    palette: str = Form(None, description="Palette for map (optional)"),
    session_id: str = Form(None, description="session_id"),
    # db: AsyncSession = Depends(get_db)
):
    product = "modis"
    province = "Punjab"
    geometry = None
    input_date = "2009-12-31"
    palette = None
    print("HERE")
    return await earthengine_service.get_et_map(product, province, geometry, input_date, palette, session_id)
# @router.post("/earthengine/get-map", response_model=None)
# async def upload_layer(db: AsyncSession = Depends(get_db)):
#     print("HERE")
#     product = "modis"
#     province = "Punjab"
#     geometry = None
#     input_date = "2009-12-31"
#     palette = None
#     return await earthengine_service.get_et_map(product, province, geometry, input_date, palette,db )
    # return await earthengine_service.store_creds(user_id, file , db)