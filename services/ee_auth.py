from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import ee
import uuid
from typing import Dict, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Response
import json
import os
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from fastapi.responses import RedirectResponse
# from services.earthengine_service import get_et_map
from config import CLIENT_SECRET, CLIENT_ID
# Store pending authentications and credentials in memory
# In production, use Redis or a database
pending_auth: Dict[str, dict] = {}
user_credentials: Dict[str, dict] = {}
eerouter = APIRouter()

# Your OAuth client config (from Google Cloud Console client secrets JSON)
# In production, load from env or secret manager
CLIENT_CONFIG = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8000/api/auth/callback"],  # Must match console
    }
}
SCOPES = ["https://www.googleapis.com/auth/earthengine"]
REDIRECT_URI = "http://localhost:8000/api/auth/callback"
FRONTEND_CALLBACK_URL = "http://localhost:5173"  # Your React app URL

@eerouter.get("/api/auth/initialize")
async def initialize_auth():
    """
    Initialize Earth Engine authentication and return the auth URL
    """
    try:
        print("in func")
        session_id = str(uuid.uuid4())

        flow = Flow.from_client_config(
            CLIENT_CONFIG,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )

        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account"  # Or 'consent' if you need to force consent
        )
        print("func 2", auth_url, state)
        # Store state for verification in callback
        pending_auth[session_id] = {
            "state": state,
            "created_at": datetime.now(),
            "status": "pending"
        }

        return {
            "session_id": session_id,
            "auth_url": auth_url,
            "message": "Redirecting to authentication."
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize auth: {str(e)}"
        )
    
@eerouter.get("/api/auth/callback")
async def auth_callback(code: str, state: str, error: Optional[str] = None):
    """
    Handle OAuth callback from Google
    """
    if error:
        return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?auth_status=error&message={error}")

    # Find session by state
    session_id = None
    for sid, data in pending_auth.items():
        if data.get("state") == state:
            session_id = sid
            break

    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid state")

    try:
        # Recreate flow
        flow = Flow.from_client_config(
            CLIENT_CONFIG,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
            state=state  # Add state here
        )

        # Exchange code for tokens
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # ✅ Store credentials as a dictionary with ALL required fields
        user_credentials[session_id] = {
            "credentials": {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes,
                "expiry": credentials.expiry.isoformat() if credentials.expiry else None
            },
            "created_at": datetime.now()
        }

        pending_auth[session_id]["status"] = "completed"

        # Redirect to frontend with success
        return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?session_id={session_id}&auth_status=success")

    except Exception as e:
        return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?auth_status=error&message={str(e)}")


def get_ee_credentials(session_id: str):
    """
    Dependency to get Earth Engine credentials for a session
    """
    if session_id not in user_credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # ✅ Reconstruct the Credentials object from stored data
    cred_data = user_credentials[session_id]["credentials"]
    
    credentials = Credentials(
        token=cred_data["token"],
        refresh_token=cred_data["refresh_token"],
        token_uri=cred_data["token_uri"],
        client_id=cred_data["client_id"],
        client_secret=cred_data["client_secret"],
        scopes=cred_data["scopes"]
    )
    
    return credentials


@eerouter.get("/api/ee/example")
async def example_ee_operation(session_id: str):
    """
    Example endpoint that uses Earth Engine
    """
    credentials = get_ee_credentials(session_id)
    
    try:
        print("EE INIT STARTING")
        # Initialize EE with user's credentials (will refresh if needed)
        ee.Initialize(credentials=credentials)
        print("EE INIT SUCCESS")
        
        # Example: Get info about a region
        point = ee.Geometry.Point([-122.262, 37.8719])
        image = ee.Image('USGS/SRTMGL1_003')
        elevation = image.sample(point, 30).first().get('elevation').getInfo()
        
        return {
            "elevation": elevation,
            "message": "Successfully queried Earth Engine"
        }
    
    except Exception as e:
        print("EE INIT ERROR", str(e))
        raise HTTPException(status_code=500, detail=f"Earth Engine operation failed: {str(e)}")


@eerouter.get("/api/auth/status/{session_id}")
async def check_auth_status(session_id: str):
    """
    Check if a session is authenticated
    """
    print("user cre", user_credentials)
    if session_id in user_credentials:
        return {"authenticated": True}
    elif session_id in pending_auth:
        return {"authenticated": False, "status": pending_auth[session_id]["status"]}
    else:
        raise HTTPException(status_code=404, detail="Session not found")


# @eerouter.get("/api/auth/callback")
# async def auth_callback(code: str, state: str, error: Optional[str] = None):
#     """
#     Handle OAuth callback from Google
#     """
#     if error:
#         # Redirect to frontend with error
#         return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?auth_status=error&message={error}")

#     # Find session by state
#     session_id = None
#     for sid, data in pending_auth.items():
#         if data.get("state") == state:
#             session_id = sid
#             break

#     if not session_id:
#         raise HTTPException(status_code=400, detail="Invalid state")

#     try:
#         # Recreate flow
#         flow = Flow.from_client_config(
#             CLIENT_CONFIG,
#             scopes=SCOPES,
#             redirect_uri=REDIRECT_URI
#         )

#         # Exchange code for tokens
#         flow.fetch_token(code=code)

#         credentials = flow.credentials

#         # Store credentials
#         user_credentials[session_id] = {
#             "credentials": credentials,
#             "created_at": datetime.now()
#         }

#         pending_auth[session_id]["status"] = "completed"

#         # Redirect to frontend with success
#         return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?session_id={session_id}&auth_status=success")

#     except Exception as e:
#         return RedirectResponse(f"{FRONTEND_CALLBACK_URL}?auth_status=error&message={str(e)}")

# @eerouter.get("/api/auth/status/{session_id}")
# async def check_auth_status(session_id: str):
#     """
#     Check if a session is authenticated
#     """
#     print("user cre", user_credentials)
#     if session_id in user_credentials:
#         return {"authenticated": True}
#     elif session_id in pending_auth:
#         return {"authenticated": False, "status": pending_auth[session_id]["status"]}
#     else:
#         raise HTTPException(status_code=404, detail="Session not found")

# def get_ee_credentials(session_id: str):
#     """
#     Dependency to get Earth Engine credentials for a session
#     """
#     if session_id not in user_credentials:
#         raise HTTPException(status_code=401, detail="Not authenticated")
    
#     return user_credentials[session_id]["credentials"]

# @eerouter.get("/api/ee/example")
# async def example_ee_operation(session_id: str):
#     """
#     Example endpoint that uses Earth Engine
#     """
#     credentials = get_ee_credentials(session_id)
    
#     try:
#         # Initialize EE with user's credentials (will refresh if needed)
#         ee.Initialize(credentials=credentials)
        
#         # Example: Get info about a region
#         point = ee.Geometry.Point([-122.262, 37.8719])
#         image = ee.Image('USGS/SRTMGL1_003')
#         elevation = image.sample(point, 30).first().get('elevation').getInfo()
        
#         return {
#             "elevation": elevation,
#             "message": "Successfully queried Earth Engine"
#         }
    
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Earth Engine operation failed: {str(e)}")
    
# @eerouter.get("/api/auth/et-map")
# async def main():
#     product = "modis"
#     province = "Punjab"
#     geometry = None
#     input_date = "2009-12-31"
#     palette = None

#     result = await get_et_map(product, province, geometry, input_date, palette)
#     print("RESULT", result)
#     return result