"""``sales.v1`` mapper -- :class:`SalesPayloadV1` -> CanonicalOrder + Customer.

Pure mapping (no I/O). The order id and customer id are derived deterministically
(``uuid5`` under the fixed namespace) so a replayed sales record upserts onto the
same canonical rows. The clean stage (run *after* this mapper) normalizes string
fields and applies FX for non-USD currencies; here we produce a fully-formed
canonical order with ``amount_base = unit_price * qty`` and ``fx_rate = 1.0`` for
the USD base case, plus a single :class:`CanonicalOrderLine` and the customer the
order references (upserted by the repository).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from edis_contracts.canonical import (
    CanonicalCustomer,
    CanonicalOrder,
    CanonicalOrderLine,
    SourceRef,
)
from edis_contracts.ingest import SalesPayloadV1

from edis_integration.mappers.identity import (
    canonical_customer_id,
    canonical_order_id,
    canonical_product_id,
    record_hash,
)
from edis_integration.mappers.registry import MapperResult, register_mapper

_CHANNELS = {"web", "partner", "direct"}


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _norm_channel(channel: str | None) -> str | None:
    """Narrow a free-text channel to the canonical literal, else ``None``.

    A model field typed ``Literal["web","partner","direct"] | None`` cannot be
    constructed with an out-of-range string, so the lightweight narrowing the
    clean stage owns conceptually is applied at map time; the clean stage refines
    the remaining string fields (case/trim/ISO/FX).
    """

    if channel is None:
        return None
    c = channel.strip().lower()
    return c if c in _CHANNELS else None


class SalesV1Mapper:
    """Maps a validated ``sales.v1`` payload to a CanonicalOrder + CanonicalCustomer."""

    domain = "sales"
    schema_ref = "sales.v1"

    def map(
        self,
        payload: SalesPayloadV1,
        *,
        tenant_id: str,
        source_system: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> MapperResult:
        order_ts = _utc(payload.order_ts)

        cust_id = canonical_customer_id(tenant_id, payload.customer_id)
        ord_id = canonical_order_id(tenant_id, source_system, payload.order_id)
        prod_id = canonical_product_id(tenant_id, payload.sku)

        # amount in source currency; the clean stage applies FX -> base. For the
        # USD base case fx_rate is 1.0 and amount_base == amount_src.
        unit_price = Decimal(str(payload.unit_price))
        qty = int(payload.qty)
        amount_src = unit_price * qty
        currency_src = payload.currency

        customer_ref = SourceRef(
            source_system=source_system,
            source_id=payload.customer_id,
            schema_version=1,
            match_confidence=1.0,  # deterministic upsert -- never fuzzy
        )
        order_ref = SourceRef(
            source_system=source_system,
            source_id=payload.order_id,
            schema_version=1,
            match_confidence=1.0,
        )

        customer = CanonicalCustomer(
            canonical_customer_id=cust_id,
            tenant_id=tenant_id,
            legal_name=payload.customer_id,  # display/legal unknown at source; id stands in
            display_name=payload.customer_id,
            primary_email=None,
            country_iso2=None,
            industry=None,
            region=payload.region,
            valid_from=order_ts,
            valid_to=None,
            is_current=True,
            version=1,
            source_refs=[customer_ref],
            dq_score=1.0,
            record_hash=record_hash(tenant_id, payload.customer_id, payload.region),
            created_at=occurred_at,
            updated_at=occurred_at,
        )

        # Line amount mirrors order amount (one line in the MVP); the clean stage
        # rewrites *_base after FX so we seed base with the source values here.
        line = CanonicalOrderLine(
            canonical_product_id=prod_id,
            sku=payload.sku,
            qty=qty,
            unit_price_base=unit_price,
            line_amount_base=amount_src,
        )

        order = CanonicalOrder(
            canonical_order_id=ord_id,
            tenant_id=tenant_id,
            canonical_customer_id=cust_id,
            order_ts=order_ts,
            currency_base="USD",
            amount_base=amount_src,  # FX applied in the clean stage (1.0 for USD)
            amount_src=amount_src,
            currency_src=currency_src,
            fx_rate=Decimal("1.0"),
            region=payload.region,
            channel=_norm_channel(payload.channel),
            line_items=[line],
            source_refs=[order_ref],
            record_hash=record_hash(
                tenant_id, source_system, payload.order_id, str(amount_src), currency_src
            ),
            created_at=occurred_at,
        )

        return MapperResult(order=order, customer=customer)


register_mapper(SalesV1Mapper())
