import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from app.api import router
from app.config import get_settings
from app.database import close_database, database_is_ready
from app.embeddings import close_embedding_provider


logger = logging.getLogger(__name__)
settings = get_settings()


class HealthResponse(BaseModel):
    status: Literal["ok"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    try:
        await close_embedding_provider()
    finally:
        await close_database()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(
    "/ready",
    response_model=HealthResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Database unavailable"
        }
    },
    tags=["system"],
)
async def readiness() -> HealthResponse:
    try:
        await database_is_ready()
    # Async drivers can surface connection failures without a SQLAlchemy wrapper.
    except Exception as error:
        logger.warning("Database readiness check failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from error

    return HealthResponse(status="ok")
