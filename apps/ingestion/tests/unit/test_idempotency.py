"""Idempotency tests: deterministic key derivation (arch §4.1) + dedupe guard.

Two concerns, both infra-free:

1. ``derive_idempotency_key`` produces the *exact* keys architecture §4.1
   specifies, deterministically (same input -> same key, every run):

   * sales    -> ``f"sales:{tenant}:{source}:{order_id}"``
   * ops      -> sha256 hex of ``f"{tenant}|{service}|{event_ts}|{message}|{trace}"``
   * customer -> ``f"customer:{tenant}:{session}:{event}:{event_ts.timestamp()}"``
   plus a content-hash fallback when the natural id is null.

2. The :class:`InMemoryIdempotencyStore` suppresses a duplicate: ``seen(key)`` is
   a check-and-set returning ``True`` only on first sighting — so a replayed
   record through :func:`ingest_record` lands once and dedupes thereafter, with no
   Redis.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from ingestion.pipeline.engine import IngestOutcome, ingest_record
from ingestion.pipeline.idempotency import (
    InMemoryIdempotencyStore,
    derive_idempotency_key,
)

_TS = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- key derivation: exact arch §4.1 formulas --------------------------------


def test_sales_key_exact_format():
    payload = {"order_id": "SO-123", "order_ts": _TS}
    key = derive_idempotency_key("sales", "acme", "simulator", payload)
    assert key == "sales:acme:simulator:SO-123"


def test_ops_key_is_sha256_of_pipe_joined_fields():
    payload = {"service": "checkout-api", "event_ts": _TS, "message": "boom"}
    key = derive_idempotency_key("ops", "acme", "erp", payload, trace_id="trace-abc")
    expected = _sha(f"acme|checkout-api|{_TS.isoformat()}|boom|trace-abc")
    assert key == expected
    assert len(key) == 64  # sha256 hex


def test_ops_key_null_message_and_trace_use_empty_string():
    payload = {"service": "catalog-api", "event_ts": _TS, "message": None}
    key = derive_idempotency_key("ops", "acme", "erp", payload)
    expected = _sha(f"acme|catalog-api|{_TS.isoformat()}||")
    assert key == expected


def test_customer_key_uses_event_ts_timestamp():
    payload = {"session_id": "S-1", "event": "page_view", "event_ts": _TS}
    key = derive_idempotency_key("customer", "acme", "web", payload)
    assert key == f"customer:acme:S-1:page_view:{_TS.timestamp()}"


def test_key_is_deterministic_across_calls():
    payload = {"order_id": "SO-9", "order_ts": _TS}
    k1 = derive_idempotency_key("sales", "acme", "simulator", payload)
    k2 = derive_idempotency_key("sales", "acme", "simulator", dict(payload))
    assert k1 == k2


def test_key_changes_with_tenant_and_source():
    p = {"order_id": "SO-1", "order_ts": _TS}
    base = derive_idempotency_key("sales", "acme", "simulator", p)
    assert derive_idempotency_key("sales", "other", "simulator", p) != base
    assert derive_idempotency_key("sales", "acme", "crm", p) != base


def test_content_hash_fallback_when_id_null():
    # sales with no order_id falls back to a deterministic content hash.
    p = {"order_id": None, "sku": "SKU-A", "order_ts": _TS}
    k = derive_idempotency_key("sales", "acme", "simulator", p)
    assert k.startswith("sales:acme:contenthash:")
    # deterministic for identical content
    assert k == derive_idempotency_key("sales", "acme", "simulator", dict(p))


# --- the in-memory guard: check-and-set --------------------------------------


@pytest.mark.asyncio
async def test_in_memory_store_first_seen_then_suppresses():
    store = InMemoryIdempotencyStore()
    assert await store.seen("k1") is True  # first sighting
    assert await store.seen("k1") is False  # duplicate suppressed
    assert await store.seen("k2") is True  # different key still passes


@pytest.mark.asyncio
async def test_in_memory_store_reset():
    store = InMemoryIdempotencyStore()
    await store.seen("k1")
    store.reset()
    assert await store.seen("k1") is True  # cleared -> first sighting again


# --- end-to-end dedupe through the engine (no infra) -------------------------


@pytest.mark.asyncio
async def test_duplicate_record_suppressed_by_store(publisher, idem):
    raw = {
        "order_id": "SO-DUP",
        "customer_id": "C1",
        "sku": "SKU-A",
        "qty": 1,
        "unit_price": "10.00",
        "region": "NA",
        "channel": "web",
        "order_ts": "2026-06-12T00:00:00Z",
    }
    common = dict(
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    first = await ingest_record("sales", dict(raw), **common)
    second = await ingest_record("sales", dict(raw), **common)

    assert first.outcome is IngestOutcome.LANDED
    assert second.outcome is IngestOutcome.DUPLICATE
    # both derived the identical key (arch §4.1).
    assert first.idempotency_key == second.idempotency_key == "sales:acme:simulator:SO-DUP"
    # the duplicate did not produce a new envelope/publish.
    assert second.envelope is None
    assert second.published is False
