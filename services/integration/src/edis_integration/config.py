"""Integration-service (L2) configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers L2-specific knobs on top --
chiefly the metric time-bucket granularity used by the ops aggregator. Like the
platform settings this is plain data; importing it cannot fail in CI without a
database or broker.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Coarse time-bucket granularity for ratio/percentile metric derivation.
BucketGranularity = Literal["minute", "hour", "day"]


class IntegrationSettings(BaseSettings):
    """L2-specific settings (env prefix ``EDIS_INTEGRATION_``).

    The shared platform :class:`~edis_platform.settings.Settings` (``EDIS_``
    prefix) still governs ``database_url`` / ``redis_url`` / ``sink_backend`` /
    logging / OTel; this class adds only knobs unique to the integration layer.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDIS_INTEGRATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- service identity ---
    service_name: str = "edis-integration"

    # --- consumer wiring ---
    #: Consumer-group id used when subscribing to the raw topics.
    consumer_group: str = "edis-integration"
    #: Max envelopes a single batch_consumer drain pulls before returning.
    batch_max_records: int = 500

    # --- metric derivation ---
    #: Bucket granularity for ratio/percentile ops metrics (error_rate,
    #: latency_p95). HOURLY by default per the architecture; configurable so a
    #: denser demo can bucket by the minute without touching code.
    metric_bucket: BucketGranularity = "hour"

    # --- persistence / outbox ---
    #: When false the pipeline uses the in-memory repository (publish-only, no
    #: Postgres) -- the default for the unit suite and a valid stateless mode.
    persist: bool = True

    # --- data quality ---
    #: Minimum dq_score below which a record is quarantined rather than upserted.
    dq_min_score: float = 0.5


@lru_cache
def get_integration_settings() -> IntegrationSettings:
    """Return the process-wide, cached :class:`IntegrationSettings`."""

    return IntegrationSettings()


# Re-export the platform settings accessor so call sites have one import surface.
get_settings = _get_platform_settings
