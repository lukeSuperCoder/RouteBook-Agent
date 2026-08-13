from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://routebook:routebook@localhost:5432/routebook"
    langgraph_database_url: str = (
        "postgresql://routebook:routebook@localhost:5432/routebook"
        "?options=-csearch_path%3Dlanggraph"
    )
    redis_url: str = "redis://localhost:6379/1"
    celery_broker_url: str = "redis://localhost:6379/0"
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    model_id: str = "claude-haiku-4-5"
    requirement_prompt_version: str = "requirement-extraction-v1"
    requirement_max_attempts: int = Field(default=2, ge=1, le=3)
    requirement_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    amap_api_key: SecretStr | None = None
    amap_base_url: str = "https://restapi.amap.com"
    qweather_api_key: SecretStr | None = None
    qweather_api_host: str = ""
    provider_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    provider_read_timeout_seconds: float = Field(default=8.0, gt=0)
    provider_max_attempts: int = Field(default=2, ge=1, le=4)
    provider_retry_backoff_seconds: float = Field(default=0.2, ge=0)
    provider_cache_prefix: str = "routebook:provider:v1"
    provider_cache_enabled: bool = True
    provider_cache_timeout_seconds: float = Field(default=0.25, gt=0, le=5)
    poi_cache_ttl_seconds: int = Field(default=2_592_000, gt=0)
    geocode_cache_ttl_seconds: int = Field(default=2_592_000, gt=0)
    route_cache_ttl_seconds: int = Field(default=21_600, gt=0)
    weather_daily_cache_ttl_seconds: int = Field(default=3_600, gt=0)
    weather_hourly_cache_ttl_seconds: int = Field(default=1_800, gt=0)
    weather_warning_cache_ttl_seconds: int = Field(default=600, gt=0)
    provider_stale_ttl_seconds: int = Field(default=86_400, gt=0)
    poi_auto_adopt_threshold: float = Field(default=0.82, ge=0, le=1)
    poi_minimum_margin: float = Field(default=0.12, ge=0, le=1)
    poi_confirmation_threshold: float = Field(default=0.45, ge=0, le=1)
    poi_exact_name_weight: float = Field(default=0.48, ge=0, le=1)
    poi_partial_name_weight: float = Field(default=0.28, ge=0, le=1)
    poi_attraction_weight: float = Field(default=0.28, ge=0, le=1)
    poi_unknown_semantic_weight: float = Field(default=0.08, ge=0, le=1)
    poi_region_match_weight: float = Field(default=0.14, ge=0, le=1)
    poi_region_mismatch_penalty: float = Field(default=0.25, ge=0, le=1)
    poi_address_weight: float = Field(default=0.05, ge=0, le=1)
    poi_adcode_weight: float = Field(default=0.05, ge=0, le=1)
    poi_hard_filter_score_cap: float = Field(default=0.20, ge=0, le=1)

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
