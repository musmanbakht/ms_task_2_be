import asyncio
from fastapi import FastAPI
from api.routes import router
from services.ee_auth import eerouter
from fastapi.middleware.cors import CORSMiddleware
from services.earthengine_service import get_et_map

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(eerouter)
