import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel

from app.api import router
from app.config import get_settings
from app.database import close_database, database_is_ready
from app.embeddings import close_embedding_provider
from app.errors import APIError, documented_error, install_error_handlers


logger = logging.getLogger(__name__)
settings = get_settings()
OPENAPI_TAGS = [
    {
        "name": "system",
        "description": "Service liveness and dependency readiness checks.",
    },
    {
        "name": "clients",
        "description": "Client profile creation and validation.",
    },
    {
        "name": "documents",
        "description": "Client document ingestion and indexing.",
    },
    {
        "name": "search",
        "description": "Lexical, semantic, and hybrid retrieval.",
    },
]


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
    description=(
        "Search API for client profiles and documents, backed by PostgreSQL "
        "full-text search, pg_trgm, and pgvector."
    ),
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)
install_error_handlers(app)
app.include_router(router)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Check service liveness",
    description=(
        "Reports whether the API process is running. This endpoint does not "
        "check external dependencies."
    ),
    response_description="The API process is running",
    responses={
        status.HTTP_500_INTERNAL_SERVER_ERROR: documented_error(
            "Unexpected server error",
            "internal_server_error",
            "An unexpected error occurred",
        )
    },
    tags=["system"],
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(
    "/ready",
    response_model=HealthResponse,
    summary="Check service readiness",
    description="Reports whether the API can connect to PostgreSQL.",
    response_description="The API and database are ready",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: documented_error(
            "Database unavailable",
            "database_unavailable",
            "Database is unavailable",
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: documented_error(
            "Unexpected server error",
            "internal_server_error",
            "An unexpected error occurred",
        ),
    },
    tags=["system"],
)
async def readiness() -> HealthResponse:
    try:
        await database_is_ready()
    # Async drivers can surface connection failures without a SQLAlchemy wrapper.
    except Exception as error:
        logger.warning("Database readiness check failed: %s", error)
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="database_unavailable",
            message="Database is unavailable",
        ) from error

    return HealthResponse(status="ok")
