import ee
import jwt
import os 
import json
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from config import SECRET_KEY
# from db.database import init_db, close_db, get_db
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import EarthEngineKey
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql import text
from sqlalchemy.future import select
import tempfile
from services.ee_auth import get_ee_credentials
import datetime
# Path to your JSON key file
SERVICE_ACCOUNT_KEY_FILE = "etmodule-eab6a2672e89.json"

# Service account email from the JSON key file
SERVICE_ACCOUNT_EMAIL = "admin-384@etmodule.iam.gserviceaccount.com"

def authenticate_and_initialize():
    try:
        # Authenticate using the JSON key
        credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT_EMAIL, SERVICE_ACCOUNT_KEY_FILE)
        ee.Initialize(credentials)
        print("Earth Engine authenticated successfully!")
    except Exception as e:
        print("Authentication failed:", e)
        raise Exception("Failed to authenticate with Earth Engine.")

# Define the function to process Earth Engine data
async def get_et_map(product, province=None, geometry=None, input_date=None, palette=None, session_id = None):
    print("in function", session_id)
    # Validate session_id is provided
    if not session_id:
        return {"status": 400, "message": "Session ID is required"}
    
    try:
        # Get credentials using the centralized function
        credentials = get_ee_credentials(session_id)
        print("✅ Credentials retrieved for session:", session_id)
    except HTTPException as e:
        return {"status": e.status_code, "message": e.detail}
    except Exception as e:
        return {"status": 500, "message": f"Failed to get credentials: {str(e)}"}
    
    try:
        # Initialize Earth Engine with user credentials
        ee.Initialize(credentials=credentials)
        print("✅ EE initialized for session:", session_id)
    except Exception as e:
        print("EE INIT ERROR", e)
        return {"status": 500, "message": f"Failed to initialize Earth Engine: {str(e)}"}
    if not palette:
        palette = ['black', 'blue', 'purple', 'cyan', 'green', 'yellow', 'red']
    
    if not (province or geometry):
        return {
            "status": 409,
            "message": "At least one of the following fields is required: province or geometry."
        }
    
    try:
        # Handle geometry or province
        aoi = None
        if geometry:
            # print("GEOMMMM>>>>>>>>>>>>", geometry)
            aoi = convert_geojson_to_ee_geometry(geometry)
            # print("AOIII", aoi)
        elif province:
            # pakistan = ee.FeatureCollection('FAO/GAUL/2015/level1').filter(
            #     ee.Filter.eq('ADM0_NAME', 'Pakistan')
            # )
            south_africa = ee.FeatureCollection('FAO/GAUL/2015/level1').filter(
    ee.Filter.eq('ADM0_NAME', 'South Africa')
)
            aoi = south_africa.filter(ee.Filter.eq('ADM1_NAME', province))

        # Ensure input_date is parsed
        target_date = ee.Date(input_date)
        buffer_days = 30

        # Select dataset
        ee_collections = {
            # "MODIS_ET": "MODIS/006/MOD16A2",
            # Add more collections as needed
              "viirs_et": 'projects/usgs-ssebop/viirs_et_v6_monthly',
  "modis_usgs": 'projects/usgs-ssebop/modis_et_v5_monthly',
  "modis": 'MODIS/061/MOD16A2GF',
  "terraClimate": 'IDAHO_EPSCOR/TERRACLIMATE',
  "fewsnet": 'NASA/FLDAS/NOAH01/C/GL/M/V001',
        }

        et_band = {
            # "MODIS_ET": "ET",
            # Add more bands as needed1
              "viirs_et": 'et',
  "modis_usgs": 'et',
  "modis": 'ET',
  "terraClimate": 'aet',
  "fewsnet": 'Evap_tavg',
        }

        if product not in ee_collections:
            return {
                "status": 409,
                "message": "Product is not supported by the system yet."
            }

        selected_product = ee.ImageCollection(ee_collections[product])

        # Filter collection by date
        filtered_collection = selected_product.filterDate(
            target_date.advance(-buffer_days, 'day'),
            target_date.advance(buffer_days, 'day')
        )

        # Get closest image
        closest_image = filtered_collection.sort('system:time_start').first()

        # Clip to AOI and select band
        clipped_image = closest_image.clip(aoi).select(et_band[product])

        # Compute min/max stats
        stats = clipped_image.reduceRegion(
            reducer=ee.Reducer.minMax(),
            geometry=aoi,
            scale=1000,
            maxPixels=1e13
        )

        min_value = stats.get(f"{et_band[product]}_min")
        max_value = stats.get(f"{et_band[product]}_max")

        # Generate map tiles
        vis_params = {
            "min": min_value.getInfo(),
            "max": max_value.getInfo(),
            "palette": palette
        }
        map_id = clipped_image.getMapId(vis_params)

        # Get metadata
        image_date = closest_image.get("system:time_start").getInfo()
        spatial_resolution = clipped_image.projection().nominalScale().getInfo()
        crs = clipped_image.projection().crs().getInfo()
        # bbox = clipped_image.geometry().bounds().getInfo()['coordinates'][0]
        bbox = aoi.bounds().getInfo()['coordinates'][0]


# Extract min and max coordinates
        min_lon = bbox[0][0]
        min_lat = bbox[0][1]
        max_lon = bbox[2][0]
        max_lat = bbox[2][1]

        # Format as [minLon, minLat, maxLon, maxLat]
        bounding_box = [min_lon, min_lat, max_lon, max_lat]

        return {
            "status": 200,
            "spatialResolution": spatial_resolution,
            "imageDate": image_date,
            "bandMinValue": min_value.getInfo(),
            "bandMaxValue": max_value.getInfo(),
            "palette": palette,
            "crs": crs,
            "tileUrl": map_id["tile_fetcher"].url_format,
            "boundingBox": bounding_box
        }

    except Exception as e:
        return {
            "status": 500,
            "message": str(e)
        }
    
async def get_product_metadata(product_id: str, session_id: str):
    """
    Fetch metadata and availability details for an Earth Engine dataset.
    """
    print("Getting metadata for:", product_id)

    if not session_id:
        return {"status": 400, "message": "Session ID is required"}

    try:
        credentials = get_ee_credentials(session_id)
        ee.Initialize(credentials=credentials)
        print("✅ EE initialized for session:", session_id)
    except Exception as e:
        return {"status": 500, "message": f"Failed to initialize Earth Engine: {str(e)}"}

    try:
        # 1. Use ee.data.getInfo() to fetch the static asset JSON
        # This is the key to getting the description
        asset_info = ee.data.getInfo(product_id)
        if not asset_info:
            return {"status": 404, "message": "Product ID not found."}

        asset_type = asset_info.get('type')
        properties = asset_info.get('properties', {})

        # 2. Extract static metadata (works for all asset types)
        # This is the description you wanted:
        description = properties.get('system:description') or properties.get('description') or "No description available."
        
        title = properties.get('title', product_id)
        provider = properties.get('provider', 'Unknown')
        
        start_date, end_date = None, None
        bands_metadata = []
        crs, scale, bbox = None, None, None

        # 3. Handle based on asset type (ImageCollection vs. Image)
        if asset_type == "IMAGE_COLLECTION":
            # --- Get dates (try fast static properties first) ---
            start_timestamp = properties.get('system:time_start')
            end_timestamp = properties.get('system:time_end')

            # --- Get dynamic info (bands, crs) from the first image ---
            dataset = ee.ImageCollection(product_id)
            first_img = dataset.first()

            # --- Fallback for dates (your original, slower method) ---
            if not start_timestamp:
                start_timestamp = dataset.aggregate_min('system:time_start').getInfo()
            if not end_timestamp:
                # Use time_start for robustness, as time_end isn't always present
                end_timestamp = dataset.aggregate_max('system:time_start').getInfo() 
            
            # --- Get bands, crs, scale, bbox from the first image ---
            try:
                bands_info = first_img.bandNames().getInfo()
                bands_metadata = [{"band": band} for band in bands_info]
                
                projection = first_img.projection().getInfo()
                crs = projection.get("crs")
                scale = projection.get("transform", [])[0] if "transform" in projection else None
                
                bbox = first_img.geometry().bounds().getInfo()['coordinates'][0]
            except Exception as e:
                print(f"Warning: Could not get dynamic info from collection's first image: {e}")

        elif asset_type == "IMAGE":
            # --- Get info directly from the IMAGE asset ---
            image = ee.Image(product_id)
            
            # Get dates (images just have one time)
            start_timestamp = properties.get('system:time_start')
            end_timestamp = properties.get('system:time_end') or start_timestamp # End is same as start

            # Get bands (from static info, more reliable for single image)
            bands_info = asset_info.get('bands', [])
            bands_metadata = [{"band": b.get('id', f'band_{i}')} for i, b in enumerate(bands_info)]

            # Get projection and bbox from the image itself
            try:
                projection = image.projection().getInfo()
                crs = projection.get("crs")
                scale = projection.get("transform", [])[0] if "transform" in projection else None
                
                bbox = image.geometry().bounds().getInfo()['coordinates'][0]
            except Exception as e:
                print(f"Warning: Could not get dynamic info from image: {e}")

        else:
            print(f"Asset is of unhandled type: {asset_type}")
            # Still return basic info
            pass
            
        # 4. Format Dates
        if start_timestamp:
            start_date = datetime.datetime.utcfromtimestamp(start_timestamp / 1000).strftime('%Y-%m-%d')
        if end_timestamp:
            end_date = datetime.datetime.utcfromtimestamp(end_timestamp / 1000).strftime('%Y-%m-%d')
        
        # 5. Assemble the final response
        return {
            "status": 200,
            "productId": product_id,
            "assetType": asset_type,
            "title": title,
            "description": description,  # <-- Here is your description
            "provider": provider,
            "availableFrom": start_date,
            "availableTo": end_date,
            "bandList": bands_metadata,
            "crs": crs,
            "scale": scale,
            "boundingBox": bbox
        }

    except ee.EEException as e:
        # Handle common EE errors
        if "ID does not refer to a" in str(e) or "Asset does not exist" in str(e):
            return {"status": 404, "message": f"Product ID not found: {product_id}"}
        return {
            "status": 500,
            "message": f"Earth Engine error: {str(e)}"
        }
    except Exception as e:
        # Catch any other unexpected errors
        return {
            "status": 500,
            "message": f"An unexpected error occurred: {str(e)}"
        }
# async def get_start_and_end_date(product, db: AsyncSession = Depends(get_db)):
#     user_id = 1
#     authentication = await authenticate_user_earth_engine(user_id, db)
#     if authentication["status"] != 200:
#         return authentication

#     try:
#         # Authenticate Earth Engine (if not already done)
#         if not ee.data._credentials:
#             ee.Initialize()

#         # Get the ImageCollection corresponding to the product
#         ee_collections = {
#             # "MODIS_ET": "MODIS/006/MOD16A2",
#             # Add more collections as needed
#               "viirs_et": 'projects/usgs-ssebop/viirs_et_v6_monthly',
#             "modis_usgs": 'projects/usgs-ssebop/modis_et_v5_monthly',
#             "modis": 'MODIS/061/MOD16A2GF',
#             "terraClimate": 'IDAHO_EPSCOR/TERRACLIMATE',
#             "fewsnet": 'NASA/FLDAS/NOAH01/C/GL/M/V001',
#         }

#         if product not in ee_collections:
#             return {"status": 400, "message": "Invalid product specified."}

#         # Access the ImageCollection for the given product
#         product_data = ee.ImageCollection(ee_collections[product])

#         # Aggregate the earliest and latest dates available in the collection
#         start_date = product_data.aggregate_min('system:time_start')
#         end_date = product_data.aggregate_max('system:time_start')

#         # Convert to human-readable dates
#         start_date_formatted = ee.Date(start_date).format('YYYY-MM-dd').getInfo()
#         end_date_formatted = ee.Date(end_date).format('YYYY-MM-dd').getInfo()

#         return {
#             "status": 200,
#             "startDate": start_date_formatted,
#             "endDate": end_date_formatted,
#         }
    
#     except Exception as e:
#         return {"status": 500, "message": f"Error occurred: {str(e)}"}
# product = "modis"
# province = "Punjab"
# geometry = None
# input_date = "2009-12-31"
# palette = None
# result = get_et_map(product, province, geometry, input_date, palette)
# print(">>>>", result)

# Path to the JSON file in the main directory
# JSON_FILE_PATH = os.path.join(os.path.dirname(__file__), "service-account-key.json")

# def read_json_and_encode(file_path, secret_key):
#     try:
#         # Read the JSON file
#         with open(file_path, "r") as json_file:
#             json_data = json.load(json_file)

#         # Encode the JSON data into a JWT
#         encoded_jwt = jwt.encode(
#             {
#                 "creds": json_data,
#                 # "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1)  # Token expiration
#             },
#             secret_key,
#             algorithm="HS256"
#         )

#         return encoded_jwt
#     except FileNotFoundError:
#         print(f"Error: File not found at {file_path}")
#         return None
#     except Exception as e:
#         print(f"Error: {str(e)}")
#         return None


# @app.post("/store-creds")
# async def store_creds(user_id,file: UploadFile, db: AsyncSession = Depends(get_db)):
#     try:
#         print("USERRRRR ID", user_id)
#         if user_id is None:
#             user_id = 1
#         user_id = int(user_id) 
#         # Ensure the uploaded file is a JSON file
#         if not file.filename.endswith(".json"):
#             raise HTTPException(status_code=400, detail="File must be a JSON file")
        
#         # Read the file's content
#         file_content = await file.read()        
#         # Parse the JSON content
#         try:
#             creds = json.loads(file_content)
#         except json.JSONDecodeError:
#             raise HTTPException(status_code=400, detail="Invalid JSON format")

#         # Encode the JSON credentials into a JWT
#         encoded_jwt = jwt.encode({"creds": creds}, SECRET_KEY, algorithm="HS256")
#         # new_key = EarthEngineKey(encoded_jwt= encoded_jwt, user_id = user_id)
#         # print(">>>>>0", new_key)
#         #         # db: AsyncSession = Depends(get_db)
#         # db.add(new_key)
#         result = await db.execute(select(EarthEngineKey).where(EarthEngineKey.user_id == user_id))
#         existing_key = result.scalars().first()

#         if existing_key:
#             # If the row exists, update it
#             existing_key.encoded_jwt = encoded_jwt
#             message = "Key updated successfully"
#         else:
#             # If the row doesn't exist, create a new one
#             new_key = EarthEngineKey(encoded_jwt=encoded_jwt, user_id=user_id)
#             db.add(new_key)
#             message = "Key uploaded successfully"
#         try:
#             await db.commit()
#             if existing_key:
#                 await db.refresh(existing_key)
#             else:
#                 await db.refresh(new_key)
#             return {"status": 200, "message": message}
#         except IntegrityError as e:
#             print("ROLLBACK")
#             await db.rollback()
#             print("IntegrityError details:", e.orig)  # This prints the underlying database error message
#             return {"status": 400, "message": "Layer not found or other integrity error", "error": str(e.orig)}
#             raise HTTPException(status_code=400, detail="Layer not found or other integrity error")

#         # Return success response with the token
#         return {"status": 200,
#                 "message": "Credentials stored successfully",
#                 "token": encoded_jwt
#             }
        

#     except Exception as e:
#         print(">>>>>1", e)
#         raise HTTPException(status_code=500, detail=str(e))
    
async def authenticate_user_earth_engine(user_id, db: AsyncSession):
    try:
        print("HERE")
        # Fetch the encoded key from the database
        result = await db.execute(
            text("SELECT encoded_jwt FROM earth_engine_keys WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        encoded_key = result.scalar_one_or_none()

        if not encoded_key:
            raise HTTPException(status_code=404, detail="No Earth Engine key found for the user")
        print("BeFORE DECODE")
        # Decode the JWT
        try:
            decoded_data = jwt.decode(encoded_key, SECRET_KEY, algorithms=["HS256"])
            credentials = decoded_data.get("creds")
            if not credentials:
                raise ValueError("Decoded JWT does not contain credentials")
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="The token has expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=400, detail="Invalid token")
        # Create a temporary JSON file for Earth Engine credentials
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as temp_file:
            json.dump(credentials, temp_file)
            temp_file_path = temp_file.name
        # Authenticate with Earth Engine using the temporary file
        client_email = credentials.get("client_email")
        if not client_email:
            raise ValueError("client_email is missing from the decoded credentials")

        try:
            credentials = ee.ServiceAccountCredentials(client_email, temp_file_path)
            ee.Initialize(credentials)
            print("Successfully authenticated with Earth Engine!")
        finally:
            # Clean up the temporary file
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

        return {"status": 200, "message": "Successfully authenticated with Earth Engine"}
    except Exception as e:
        # raise HTTPException(status_code=500, detail=f"Error during Earth Engine authentication: {str(e)}")
        return {"status": 500, "message": f"Error during Earth Engine authentication: {str(e)}"}
    

async def check_earth_engine_key_exists(user_id: int, db: AsyncSession):
    try:
        # Query the EarthEngineKey table to check if the key exists for the user
        result = await db.execute(select(EarthEngineKey).filter(EarthEngineKey.user_id == user_id))
        earth_engine_key = result.scalar_one_or_none()  # Returns the object or None if not found
        
        if earth_engine_key:
            # If the Earth Engine key exists, return a success response
            return {"status": 200, "message": "Earth Engine key exists for the user"}
        else:
            # If the Earth Engine key does not exist, return a 404 response
            raise HTTPException(status_code=404, detail="Earth Engine key not found for this user")
    
    except SQLAlchemyError as e:
        # Handle any database-related errors
        print(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
    
def convert_geojson_to_ee_geometry(geo_json):
    # If geo_json is a string, parse it into a dictionary
    if isinstance(geo_json, str):
        geo_json = json.loads(geo_json)
    
    print('IN CONVERSION JSON TYPE', geo_json['type'])
    
    # Check if the input is a FeatureCollection
    if geo_json['type'] == 'FeatureCollection':
        print('in feature collection')
        # Extract the first feature's geometry
        return ee.Geometry(geo_json['features'][0]['geometry'])
    
    # Check if the input is a Feature
    elif geo_json['type'] == 'Feature':
        print('in feature')
        return ee.Geometry(geo_json['geometry'])
    
    # If it's already a geometry, return it directly
    else:
        print('IN ELSE of conversion')
        return ee.Geometry(geo_json)