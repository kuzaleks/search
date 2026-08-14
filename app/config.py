from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 512


class Settings(BaseSettings):
    app_name: str = "Nevis Search API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "production"] = "local"
    database_url: str = (
        "postgresql+asyncpg://search:search@localhost:5432/search"
    )
    log_level: str = "INFO"
    openai_api_key: SecretStr | None = None
    embedding_timeout_seconds: float = Field(default=30.0, gt=0)
    embedding_max_retries: int = Field(default=1, ge=0, le=5)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
