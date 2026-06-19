"""Environment-driven application settings.

All configuration is read from the environment (prefix ``EDIS_``) or a ``.env``
file. Settings are never connected to a live resource at import time -- the
returned :class:`Settings` object is plain data, so importing it cannot fail in
CI without a database or broker. ``get_settings()`` is cached so the process
shares one immutable settings instance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The single, env-driven configuration surface for every EDIS service."""

    model_config = SettingsConfigDict(
        env_prefix="EDIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- identity ---
    app_name: str = "edis"
    service_name: str = "edis"
    environment: str = "dev"
    log_level: str = "INFO"

    # --- data plane ---
    database_url: str = "postgresql+asyncpg://edis:edis@localhost:5432/edis"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap_servers: str = "localhost:19092"

    # --- event bus backend selector (F3) ---
    sink_backend: Literal["kafka", "redis", "inproc"] = "inproc"

    # --- observability ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    # --- auth ---
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"

    # --- model providers (optional; never required to import a service) ---
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide, cached :class:`Settings` instance."""

    return Settings()
