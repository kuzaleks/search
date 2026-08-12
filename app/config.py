from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Nevis Search API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "production"] = "local"
    database_url: str = (
        "postgresql+asyncpg://search:search@localhost:5432/search"
    )
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
