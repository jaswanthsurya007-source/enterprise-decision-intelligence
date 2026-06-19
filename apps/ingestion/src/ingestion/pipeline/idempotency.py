"""Idempotency: deterministic key derivation + a first-seen guard.

Two concerns live here:

1. **Key derivation** (``derive_idempotency_key``) — the *exact* rule from
   architecture §4.1, replay-safe and deterministic:

   * sales    -> ``f"sales:{tenant_id}:{source_system}:{order_id}"``
   * ops      -> sha256 hex of ``f"{tenant_id}|{service}|{event_ts}|{message}|{trace_id}"``
   * customer -> ``f"customer:{tenant_id}:{session_id}:{event}:{event_ts.timestamp()}"``

   A content-hash fallback is used when the natural id is null, so a record is
   never un-keyable.

2. **The guard** (:class:`IdempotencyStore`) — a "have I seen this key before?"
   abstraction with a Redis ``SETNX`` backend (production) and an in-memory
   backend (unit tests, so dedupe is testable without Redis). ``seen`` is a
   *check-and-set*: it returns ``True`` only the first time a key is presented,
   making at-least-once delivery effectively exactly-once at the landing step.
"""

from __future__ import annotations

import abc
import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ingestion.config import IngestionSettings


# --- key derivation -----------------------------------------------------------


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _content_hash(domain: str, tenant_id: str, payload: dict[str, Any]) -> str:
    """Deterministic fallback key from the record content (ids null)."""

    items = "|".join(f"{k}={payload[k]!r}" for k in sorted(payload))
    return f"{domain}:{tenant_id}:contenthash:" + _sha256_hex(items)


def _ts_repr(value: Any) -> str:
    """Stable string for a timestamp field used in a key (tz-aware ISO)."""

    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def derive_idempotency_key(
    domain: str,
    tenant_id: str,
    source_system: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> str:
    """Derive the deterministic, replay-safe idempotency key (arch §4.1).

    ``payload`` is the *validated* per-domain payload as a dict (so timestamps are
    real ``datetime`` objects). Falls back to a content hash when the natural id
    is missing, so no record is ever un-keyable.
    """

    if domain == "sales":
        order_id = payload.get("order_id")
        if order_id:
            return f"sales:{tenant_id}:{source_system}:{order_id}"
        return _content_hash(domain, tenant_id, payload)

    if domain == "ops":
        service = payload.get("service")
        if service:
            event_ts = _ts_repr(payload.get("event_ts"))
            message = payload.get("message") or ""
            tid = trace_id or ""
            return _sha256_hex(f"{tenant_id}|{service}|{event_ts}|{message}|{tid}")
        return _content_hash(domain, tenant_id, payload)

    if domain == "customer":
        session_id = payload.get("session_id")
        event = payload.get("event")
        event_ts = payload.get("event_ts")
        if session_id and event and isinstance(event_ts, datetime):
            return f"customer:{tenant_id}:{session_id}:{event}:{event_ts.timestamp()}"
        return _content_hash(domain, tenant_id, payload)

    return _content_hash(domain, tenant_id, payload)


# --- the guard ----------------------------------------------------------------


class IdempotencyStore(abc.ABC):
    """First-seen guard: ``seen(key)`` is a check-and-set, ``True`` only once."""

    @abc.abstractmethod
    async def seen(self, key: str) -> bool:
        """Return ``True`` if ``key`` is being seen for the first time.

        Atomic: a concurrent second call with the same key returns ``False``.
        """

    async def start(self) -> None:  # pragma: no cover - default no-op
        """Open any backing connection (lazy; never at import)."""

    async def stop(self) -> None:  # pragma: no cover - default no-op
        """Close any backing connection."""


class InMemoryIdempotencyStore(IdempotencyStore):
    """In-process set of seen keys — no infra, used in unit tests.

    Not shared across processes and unbounded; perfect for tests and the
    single-process laptop run, never for a real multi-replica deployment.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    async def seen(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def reset(self) -> None:
        """Clear the seen set (test hook)."""

        self._seen.clear()


class RedisIdempotencyStore(IdempotencyStore):
    """``SETNX``-backed guard — atomic and shared across replicas.

    Uses ``SET key 1 NX EX <ttl>``: the first writer wins (``True``), everyone
    else sees the key already set (``False``). The TTL bounds the replay window so
    the key space cannot grow without limit. The Redis client is created lazily in
    :meth:`start`, so importing this class needs no Redis.
    """

    def __init__(
        self, redis_url: str, ttl_seconds: int, *, namespace: str = "edis:ingest:idem"
    ) -> None:
        self._url = redis_url
        self._ttl = ttl_seconds
        self._ns = namespace
        self._client: Any = None

    async def start(self) -> None:
        if self._client is None:
            import redis.asyncio as redis  # lazy: no Redis needed to import

            self._client = redis.from_url(self._url, encoding="utf-8", decode_responses=True)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def seen(self, key: str) -> bool:
        if self._client is None:
            await self.start()
        # SET NX returns True only if the key did not exist -> first sighting.
        was_set = await self._client.set(f"{self._ns}:{key}", "1", nx=True, ex=self._ttl)
        return bool(was_set)


def make_idempotency_store(
    settings: "IngestionSettings", platform_settings: Any = None
) -> IdempotencyStore:
    """Construct the store selected by ``settings.idempotency_backend``.

    ``memory`` (default, unit tests) needs no infra; ``redis`` reads the Redis URL
    from the platform settings.
    """

    if settings.idempotency_backend == "memory":
        return InMemoryIdempotencyStore()
    if settings.idempotency_backend == "redis":
        from edis_platform.settings import get_settings

        ps = platform_settings or get_settings()
        return RedisIdempotencyStore(ps.redis_url, settings.idempotency_ttl_seconds)
    raise ValueError(f"unknown idempotency_backend: {settings.idempotency_backend!r}")
