import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import engine
from app.routes.google_auth import router as google_auth_router
from app.routes.sms import router as sms_router

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Remy starting up")
    yield
    await engine.dispose()
    logger.info("Remy shut down")


app = FastAPI(title="Remy — Life OS", version="1.0.0", lifespan=lifespan)
app.include_router(sms_router, prefix="/sms", tags=["sms"])
app.include_router(google_auth_router, prefix="/sms", tags=["google-auth"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
