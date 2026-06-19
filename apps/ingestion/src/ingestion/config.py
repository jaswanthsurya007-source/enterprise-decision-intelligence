"""Ingestion-service configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers ingestion-specific knobs on
top. Like the platform settings, this is plain data — importing it cannot fail in
CI without a database or broker.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    """Ingestion-specific settings (env prefix ``EDIS_INGEST_``).

    The shared platform :class:`~edis_platform.settings.Settings` (``EDIS_``
    prefix) still governs ``database_url`` / ``redis_url`` / ``sink_backend`` /
    logging / OTel; this class only adds knobs that are unique to L1. Keeping them
    on a separate prefix means a service can tune ingestion without touching the
    platform-wide config surface.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDIS_INGEST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- service identity ---
    service_name: str = "edis-ingestion"

    # --- idempotency / dedupe ---
    #: Where the dedupe guard lives. ``memory`` needs no infra (used in unit
    #: tests so dedupe is testable without Redis); ``redis`` uses ``SETNX``.
    idempotency_backend: Literal["memory", "redis"] = "memory"
    #: TTL (seconds) for an idempotency key in Redis; bounds replay-window memory.
    idempotency_ttl_seconds: int = 7 * 24 * 3600

    # --- outbox / relay ---
    #: Persist the raw_events outbox at all. When false the service runs in
    #: stateless PUBLISH-ONLY mode (events still reach the bus; nothing is written
    #: to Postgres) -- used by the unit suite, and a valid stateless deployment.
    #: Even when true, the app degrades to publish-only if the DB is unreachable.
    persist: bool = True
    #: When true the pipeline publishes to the bus immediately after landing the
    #: raw_events row ("publish-after-land"); the row stays the durable record and
    #: the reconcile relay republishes any rows still ``published=false``.
    publish_after_land: bool = True

    # --- batch loader ---
    batch_chunk_size: int = 1000

    # --- simulator provenance ---
    default_source_system: str = "simulator"
    default_tenant_id: str = "acme"


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    """Return the process-wide, cached :class:`IngestionSettings`."""

    return IngestionSettings()


# Re-export the platform settings accessor so call sites have one import surface.
get_settings = _get_platform_settings
