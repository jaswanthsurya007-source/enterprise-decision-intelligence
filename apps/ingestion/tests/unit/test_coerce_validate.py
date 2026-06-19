"""Edge-of-trust coercion + validation tests.

The pipeline's first two stages turn an untrusted, messy source row into a clean,
strictly-validated per-domain payload — or, when the row is genuinely bad, route
it to the DLQ. These tests assert both halves directly against the real
:mod:`ingestion.pipeline.coerce` / :mod:`ingestion.pipeline.validator` and against
the engine (which produces the :class:`DLQRecord`), with no infra.

Covered:

* currency-as-string (``"$1,299.00"``) -> ``float``.
* timestamps: epoch-ms, epoch-seconds, ``mm/dd/yyyy`` and ISO-with-``Z`` -> all
  tz-aware UTC ``datetime``.
* blank optional fields -> ``None``; field aliases (``ts`` -> ``order_ts``).
* a clean coerced row validates into ``SalesPayloadV1`` / ``OpsPayloadV1``.
* genuine drift (extra field, unparseable qty, missing required) -> a DLQ record
  carrying the full error context, never raising, never blocking.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from edis_contracts.ingest import OpsPayloadV1, SalesPayloadV1
from pydantic import ValidationError

from ingestion.pipeline import coerce as C
from ingestion.pipeline import validator as V
from ingestion.pipeline.engine import IngestOutcome, ingest_record

# --- pure coercion -----------------------------------------------------------


def test_coerce_float_currency_string():
    assert C.coerce_float("$1,299.00") == 1299.0
    assert C.coerce_float("129.00") == 129.0
    assert C.coerce_float("  89.5 ") == 89.5
    assert C.coerce_float(42) == 42.0
    # uncoercible -> left in place so the validator raises a precise error.
    assert C.coerce_float("not-a-number") == "not-a-number"
    # blank -> None (optional-field friendly).
    assert C.coerce_float("") is None


def test_coerce_int_numeric_string_and_whole_float():
    assert C.coerce_int("3") == 3
    assert C.coerce_int("1,000") == 1000
    assert C.coerce_int(2.0) == 2
    # non-whole float is preserved (not silently truncated).
    assert C.coerce_int(2.5) == 2.5
    assert C.coerce_int("bad") == "bad"


def test_coerce_timestamp_epoch_ms_to_utc():
    # 2026-06-12T00:00:00Z == 1781222400 s == 1781222400000 ms
    ms = 1781222400000
    dt = C.coerce_timestamp(ms)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None and dt.utcoffset() == timezone.utc.utcoffset(dt)
    assert dt == datetime(2026, 6, 12, tzinfo=timezone.utc)


def test_coerce_timestamp_epoch_seconds_to_utc():
    secs = 1781222400
    dt = C.coerce_timestamp(secs)
    assert dt == datetime(2026, 6, 12, tzinfo=timezone.utc)


def test_coerce_timestamp_mm_dd_yyyy_and_iso_z():
    d1 = C.coerce_timestamp("06/12/2026")
    assert d1 == datetime(2026, 6, 12, tzinfo=timezone.utc)
    d2 = C.coerce_timestamp("2026-06-12T10:30:00Z")
    assert d2 == datetime(2026, 6, 12, 10, 30, tzinfo=timezone.utc)
    assert d2.tzinfo is not None


def test_coerce_timestamp_naive_assumed_utc_and_unparseable_passthrough():
    naive = C.coerce_timestamp("2026-06-12 10:00:00")
    assert naive == datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    # genuinely unparseable -> returned untouched (validator surfaces it).
    assert C.coerce_timestamp("never") == "never"


def test_coerce_sales_aliases_and_normalizes():
    raw = {
        "order_id": "SO-1",
        "customer_id": "C1",
        "sku": "SKU-A",
        "qty": "2",
        "unit_price": "$129.00",
        "currency": "USD",
        "region": "EMEA",
        "channel": "  ",  # blank -> None
        "ts": "06/12/2026",  # alias -> order_ts
    }
    out = C.coerce_sales(raw)
    assert out["unit_price"] == 129.0
    assert out["qty"] == 2
    assert out["order_ts"] == datetime(2026, 6, 12, tzinfo=timezone.utc)
    assert out["channel"] is None
    assert "ts" not in out  # alias consumed


def test_coerce_does_not_invent_or_drop_unknown_keys():
    # Unknown key is preserved (so extra="forbid" can flag drift, not coercion).
    out = C.coerce_sales({"order_id": "SO-1", "weird_extra": "x"})
    assert out["weird_extra"] == "x"


# --- validation (the strict per-domain model) --------------------------------


def test_validate_sales_clean_row():
    coerced = C.coerce_sales(
        {
            "order_id": "SO-1",
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": "2",
            "unit_price": "$129.00",
            "region": "EMEA",
            "channel": "web",
            "ts": "06/12/2026",
        }
    )
    model = V.validate("sales", coerced)
    assert isinstance(model, SalesPayloadV1)
    assert model.unit_price == 129.0
    assert model.qty == 2
    assert model.order_ts == datetime(2026, 6, 12, tzinfo=timezone.utc)


def test_validate_ops_clean_row():
    coerced = C.coerce_ops(
        {
            "service": "checkout-api",
            "region": "EMEA",
            "level": "ERROR",  # case normalized by coercion
            "status_code": "503",
            "latency_ms": "1399.5",
            "message": "upstream timeout",
            "ts": 1781222400000,
        }
    )
    model = V.validate("ops", coerced)
    assert isinstance(model, OpsPayloadV1)
    assert model.level == "error"
    assert model.status_code == 503
    assert model.latency_ms == 1399.5
    assert model.event_ts == datetime(2026, 6, 12, tzinfo=timezone.utc)


def test_validate_rejects_extra_field():
    coerced = C.coerce_sales(
        {
            "order_id": "SO-1",
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": 1,
            "unit_price": 10.0,
            "order_ts": "2026-06-12",
            "rogue": "drift",
        }
    )
    with pytest.raises(ValidationError):
        V.validate("sales", coerced)


def test_validate_rejects_unparseable_qty():
    coerced = C.coerce_sales(
        {
            "order_id": "SO-1",
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": "two",  # stays a string, fails int validation
            "unit_price": 10.0,
            "order_ts": "2026-06-12",
        }
    )
    with pytest.raises(ValidationError):
        V.validate("sales", coerced)


def test_unknown_domain_raises():
    with pytest.raises(V.UnknownDomainError):
        V.validate("widgets", {})


def test_format_validation_error_is_compact():
    try:
        V.validate("sales", {"order_id": "SO-1"})  # missing many required fields
    except ValidationError as exc:
        detail = V.format_validation_error(exc)
    assert "customer_id" in detail and ":" in detail


# --- bad rows become DLQ records (engine integration, no infra) --------------


@pytest.mark.asyncio
async def test_bad_record_routes_to_dlq_with_context(publisher, idem):
    bad = {
        "order_id": "SO-9",
        "customer_id": "C9",
        "sku": "SKU-Z",
        "qty": "not-an-int",  # uncoercible -> validation error
        "unit_price": "10.00",
        "order_ts": "2026-06-12",
    }
    res = await ingest_record(
        "sales",
        bad,
        tenant_id="acme",
        source_system="test",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    assert res.outcome is IngestOutcome.DLQ
    assert res.dlq_id is not None
    assert res.error and "qty" in res.error  # full error context preserved
    assert res.envelope is None


@pytest.mark.asyncio
async def test_unknown_domain_record_routes_to_dlq(publisher, idem):
    res = await ingest_record(
        "widgets",  # type: ignore[arg-type]
        {"anything": 1},
        tenant_id="acme",
        source_system="test",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    assert res.outcome is IngestOutcome.DLQ
    assert res.error and "widgets" in res.error


@pytest.mark.asyncio
async def test_dlq_published_to_dlq_topic(sink, publisher, idem, source):
    from edis_contracts import topics

    stream = source.subscribe([topics.DLQ_INGEST], group="dlq-test")

    await ingest_record(
        "sales",
        {"order_id": "SO-1", "qty": "bad"},  # invalid
        tenant_id="acme",
        source_system="test",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )

    msg = await stream.__anext__()
    assert msg.topic == topics.DLQ_INGEST
    assert msg.key == "acme"
    assert msg.value["stage"] == "ingest"
    assert msg.value["error_type"] == "validation_error"
    # the original bad record is preserved for replay/audit.
    assert msg.value["raw"]["order_id"] == "SO-1"
