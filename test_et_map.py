import asyncio
from services.earthengine_service import get_et_map

async def main():
    product = "modis"
    province = "Punjab"
    geometry = None
    input_date = "2009-12-31"
    palette = None
    session_id = "ad84b22f-00ff-486d-966a-2826086fb58e"

    result = await get_et_map(product, province, geometry, input_date, palette,session_id)
    print("RESULT", result)

if __name__ == "__main__":
    asyncio.run(main())
