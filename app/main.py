import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.database import engine
from app.limiter import limiter
from app.routes.google_auth import router as google_auth_router
from app.routes.photon_webhook import router as photon_router
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
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.include_router(sms_router, prefix="/sms", tags=["sms"])
app.include_router(google_auth_router, prefix="/sms", tags=["google-auth"])
app.include_router(photon_router, prefix="/sms", tags=["photon"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
