"""
HT Proxy - Backend API
CSDL: Firebase Firestore | Router: MikroTik RouterOS port 2601 (an)
Chay: uvicorn main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from firebase_app import init_firebase
from quick_db import init_quick_db
from routers import proxy_router, settings_router, apikey_router, check_router, admin_router, quick_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_firebase()
    init_quick_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(proxy_router.router)
app.include_router(settings_router.router)
app.include_router(apikey_router.router)
app.include_router(check_router.router)
app.include_router(admin_router.router)
app.include_router(quick_router.router)


@app.get("/")
def root():
    return {"message": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health")
def health():
    return {"status": "ok"}
