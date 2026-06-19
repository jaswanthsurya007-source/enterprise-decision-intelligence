"""Gateway-service (W1) configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers gateway-specific knobs on top
(SSE heartbeat cadence, the copilot upstream URL, per-stream consumer-group
prefix). Like the platform settings this is plain data; importing it cannot fail
in CI without a database, broker, or API key.
"""

from __future__ import annotations

from functools import lru_cache

from edis_platform.settings import Settings
from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Gateway-specific settings (env prefix ``EDIS_GATEWAY_``).

    The shared platform :class:`~edis_platform.settings.Settings` (``EDIS_``
    prefix) still governs ``database_url`` / ``sink_backend`` /
    ``kafka_bootstrap_servers`` / logging / OTel / JWT; this class adds only knobs
    unique to the edge.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDIS_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "edis-gateway"

    # --- SSE ---
    #: Seconds between SSE heartbeat comments when no event is flowing. Keeps the
    #: connection (and any intervening proxy) alive and lets the browser detect a
    #: dead stream for backoff/reconnect.
    sse_heartbeat_seconds: float = 15.0
    #: Consumer-group prefix for the per-connection bridge subscriptions. Each SSE
    #: connection appends a unique suffix so streams never share partitions.
    sse_group_prefix: str = "edis-gateway-sse"

    # --- copilot proxy ---
    #: Base URL of the L5 copilot service the gateway proxies the chat SSE to.
    copilot_base_url: str = "http://localhost:8085"
    #: Path on the copilot service for the streaming chat endpoint.
    copilot_chat_path: str = "/v1/copilot/chat"
    #: Total timeout (seconds) for the upstream copilot connection. The stream
    #: itself is long-lived; this bounds connect/initial-response latency.
    copilot_connect_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    """Return the shared, cached platform :class:`Settings`."""

    return _get_platform_settings()


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    """Return the process-wide, cached :class:`GatewaySettings`."""

    return GatewaySettings()
