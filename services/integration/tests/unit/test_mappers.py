"""Golden-fixture mapper tests -- a messy source row maps to an EXACT canonical row.

These pin the deterministic mapping + clean/coerce normalization the demo relies
on, against hand-computed golden values:

* a **messy sales row** (currency-as-string from L1 already coerced to float by
  the time it is enveloped, region ``"  emea "``, channel ``"WEB"``) maps to a
  :class:`CanonicalOrder` with the deterministic ``uuid5`` id, ``amount_base =
  unit_price * qty``, normalized ``region="EMEA"`` / ``channel="web"``, USD base,
  ``fx_rate=1.0``, one :class:`CanonicalOrderLine`, and an upserted
  :class:`CanonicalCustomer` keyed by the deterministic customer id;
* a non-USD sales row exercises the clean stage's FX -> base conversion;
* an **ops row** maps to a single :class:`OpsEvent` with the deterministic
  ``uuid5(NS, "ops:{tenant}:{idempotency_key}")`` id and the passed-through
  service/region/level/status/latency fields.

The mapping is run through the real stage pipeline (``normalize_envelope``) so the
golden assertions cover map + clean + coerce together, exactly as production does.
All pure -- no infra.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from edis_contracts.canonical import CanonicalCustomer, CanonicalOrder, OpsEvent
from edis_contracts.ingest import IngestEnvelope

from edis_integration.mappers.identity import (
    NAMESPACE,
    canonical_customer_id,
    canonical_order_id,
    canonical_ops_event_id,
    canonical_product_id,
)
from edis_integration.pipeline.engine import normalize_envelope

_TENANT = "acme"
_SOURCE = "simulator"


def _sales_envelope(payload: dict, *, idempotency_key: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=uuid4(),
        idempotency_key=idempotency_key,
        schema_ref="sales.v1",
        domain="sales",
        tenant_id=_TENANT,
        source_system=_SOURCE,
        ingest_ts=datetime.now(timezone.utc),
        event_ts=(
            payload["order_ts"]
            if isinstance(payload["order_ts"], datetime)
            else datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        ),
        payload=payload,
    )


def _ops_envelope(payload: dict, *, idempotency_key: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=uuid4(),
        idempotency_key=idempotency_key,
        schema_ref="ops.v1",
        domain="ops",
        tenant_id=_TENANT,
        source_system=_SOURCE,
        ingest_ts=datetime.now(timezone.utc),
        event_ts=datetime(2026, 6, 12, 10, 30, tzinfo=timezone.utc),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Sales -> CanonicalOrder (+ CanonicalCustomer)
# ---------------------------------------------------------------------------
def test_messy_sales_row_maps_to_exact_canonical_order() -> None:
    # A messy-but-already-edge-coerced sales payload: region has stray case +
    # whitespace, channel is upper-case; these are normalized by the clean stage.
    payload = {
        "order_id": "SO-100",
        "customer_id": "C1",
        "sku": "SKU-A",
        "qty": 2,
        "unit_price": 129.0,
        "currency": "USD",
        "region": "  emea ",
        "channel": "WEB",
        "order_ts": "2026-06-12T10:00:00Z",
    }
    env = _sales_envelope(payload, idempotency_key="sales:acme:simulator:SO-100")

    ctx = normalize_envelope(env)
    assert not ctx.quarantined
    assert ctx.dq_score == 1.0
    assert ctx.coerced is not None

    order = ctx.coerced.order
    customer = ctx.coerced.customer
    assert isinstance(order, CanonicalOrder)
    assert isinstance(customer, CanonicalCustomer)

    # --- deterministic identity (uuid5 under the fixed namespace) ---
    expected_order_id = canonical_order_id(_TENANT, _SOURCE, "SO-100")
    expected_cust_id = canonical_customer_id(_TENANT, "C1")
    # Pin the literal id too so a namespace regression is caught loudly.
    assert expected_order_id == UUID("1293941b-57b2-510f-85fe-3630d869e9bd")
    assert order.canonical_order_id == expected_order_id
    assert order.canonical_customer_id == expected_cust_id
    assert customer.canonical_customer_id == expected_cust_id

    # --- money: amount_base = unit_price * qty, USD base, fx 1.0 ---
    assert order.amount_base == Decimal("258.0000")
    assert order.amount_src == Decimal("258")
    assert order.currency_base == "USD"
    assert order.currency_src == "USD"
    assert order.fx_rate == Decimal("1.0")

    # --- region/channel normalization (clean stage) ---
    assert order.region == "EMEA"
    assert order.channel == "web"
    assert customer.region == "EMEA"

    # --- order time normalized to tz-aware UTC ---
    assert order.order_ts == datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    assert order.order_ts.tzinfo is not None

    # --- exactly one order line, base = unit_price*qty, sku passthrough ---
    assert len(order.line_items) == 1
    line = order.line_items[0]
    assert line.sku == "SKU-A"
    assert line.qty == 2
    assert line.unit_price_base == Decimal("129.0000")
    assert line.line_amount_base == Decimal("258.0000")
    assert line.canonical_product_id == canonical_product_id(_TENANT, "SKU-A")

    # --- deterministic upsert -> match_confidence is always 1.0 (no fuzzy ER) ---
    assert [r.match_confidence for r in order.source_refs] == [1.0]
    assert order.source_refs[0].source_system == _SOURCE
    assert order.source_refs[0].source_id == "SO-100"
    assert [r.match_confidence for r in customer.source_refs] == [1.0]

    # --- SCD-2 columns are pinned in the MVP (always current, v1) ---
    assert customer.is_current is True
    assert customer.valid_to is None
    assert customer.version == 1


def test_sales_id_is_replay_stable_and_source_scoped() -> None:
    # Same order_id, same source -> same canonical id (idempotent replay).
    p = {
        "order_id": "SO-7",
        "customer_id": "C9",
        "sku": "SKU-Z",
        "qty": 1,
        "unit_price": 10.0,
        "currency": "USD",
        "region": "NA",
        "channel": "web",
        "order_ts": "2026-06-01T00:00:00Z",
    }
    a = normalize_envelope(_sales_envelope(dict(p), idempotency_key="k1")).coerced.order
    b = normalize_envelope(_sales_envelope(dict(p), idempotency_key="k1")).coerced.order
    assert a.canonical_order_id == b.canonical_order_id

    # Customer id is intentionally source-system-independent (tenant + customer_id).
    assert a.canonical_customer_id == canonical_customer_id("acme", "C9")


def test_non_usd_sales_row_converts_to_usd_base_via_fx() -> None:
    # EUR source amount is converted to USD base in the clean stage (fx_rate>1.0),
    # while amount_src stays in the source currency.
    payload = {
        "order_id": "SO-EUR-1",
        "customer_id": "C-EU",
        "sku": "SKU-A",
        "qty": 2,
        "unit_price": 100.0,
        "currency": "eur",  # lower-case on purpose -> upper-cased to ISO-4217
        "region": "EMEA",
        "channel": "web",
        "order_ts": "2026-06-12T10:00:00Z",
    }
    order = normalize_envelope(
        _sales_envelope(payload, idempotency_key="sales:acme:simulator:SO-EUR-1")
    ).coerced.order

    assert order.currency_src == "EUR"
    assert order.currency_base == "USD"
    assert order.amount_src == Decimal("200")  # 100 * 2 in EUR
    assert order.fx_rate == Decimal("1.08")
    # amount_base = amount_src * fx_rate, quantized to 4 places.
    assert order.amount_base == Decimal("216.0000")
    # line base amounts scale by the same rate.
    assert order.line_items[0].unit_price_base == Decimal("108.0000")
    assert order.line_items[0].line_amount_base == Decimal("216.0000")


# ---------------------------------------------------------------------------
# Ops -> OpsEvent
# ---------------------------------------------------------------------------
def test_ops_row_maps_to_exact_ops_event() -> None:
    payload = {
        "service": "checkout-api",
        "region": "  emea ",
        "level": "error",
        "status_code": 503,
        "latency_ms": 1400.0,
        "message": "upstream timeout",
        "event_ts": "2026-06-12T10:30:00Z",
    }
    idem = "0f1e2d3c4b5a69788796a5b4c3d2e1f00112233445566778899aabbccddeeff0"
    env = _ops_envelope(payload, idempotency_key=idem)

    ctx = normalize_envelope(env)
    assert not ctx.quarantined
    assert ctx.coerced is not None
    assert ctx.coerced.order is None  # ops produces no order/customer
    assert ctx.coerced.customer is None

    events = ctx.coerced.ops_events
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, OpsEvent)

    # --- deterministic ops id from the envelope idempotency_key ---
    expected = canonical_ops_event_id(_TENANT, idem)
    assert ev.canonical_ops_event_id == expected
    # equivalently: uuid5(NS, "ops:{tenant}:{idempotency_key}")
    from uuid import uuid5

    assert expected == uuid5(NAMESPACE, f"ops:{_TENANT}:{idem}")

    # --- field passthrough + region normalization (clean stage trims/upper) ---
    assert ev.service == "checkout-api"
    assert ev.region == "EMEA"
    assert ev.level == "error"
    assert ev.status_code == 503
    assert ev.latency_ms == 1400.0
    assert ev.message == "upstream timeout"
    assert ev.event_ts == datetime(2026, 6, 12, 10, 30, tzinfo=timezone.utc)
    assert [r.match_confidence for r in ev.source_refs] == [1.0]
    assert ev.source_refs[0].source_id == idem


def test_ops_id_falls_back_to_field_hash_without_idempotency_key() -> None:
    # canonical_ops_event_id without a key hashes the identifying ops fields, so
    # the id is deterministic either way (and differs from the key-derived id).
    a = canonical_ops_event_id(
        _TENANT,
        None,
        service="checkout-api",
        event_ts_iso="2026-06-12T10:30:00+00:00",
        message="boom",
        status_code=500,
        latency_ms=900.0,
    )
    b = canonical_ops_event_id(
        _TENANT,
        None,
        service="checkout-api",
        event_ts_iso="2026-06-12T10:30:00+00:00",
        message="boom",
        status_code=500,
        latency_ms=900.0,
    )
    assert a == b  # deterministic
    assert a != canonical_ops_event_id(_TENANT, "some-key")
