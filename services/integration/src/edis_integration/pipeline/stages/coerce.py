"""Stage 5 -- coerce: finalize types and recompute content hashes post-clean.

The clean stage rewrote amounts (FX), region casing, and string fields, so the
``record_hash`` seeded at map time no longer reflects the persisted values. This
stage recomputes ``record_hash`` over the *cleaned* canonical values so the hash
is a faithful content fingerprint for idempotent upsert / change detection, and
guarantees timestamps are tz-aware UTC. Pure: builds new models via
``model_copy``.
"""

from __future__ import annotations

from datetime import timezone

from edis_integration.mappers.identity import record_hash
from edis_integration.pipeline.stages import StageContext


def _utc(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


class CoerceStage:
    name = "coerce"

    def __call__(self, ctx: StageContext) -> StageContext:
        from edis_integration.mappers.registry import MapperResult

        assert ctx.cleaned is not None  # clean ran first
        cleaned = ctx.cleaned

        order = cleaned.order
        if order is not None:
            order = order.model_copy(
                update={
                    "order_ts": _utc(order.order_ts),
                    "created_at": _utc(order.created_at),
                    "record_hash": record_hash(
                        order.tenant_id,
                        order.canonical_order_id,
                        str(order.amount_base),
                        order.currency_base,
                        order.region,
                        order.channel,
                    ),
                }
            )

        customer = cleaned.customer
        if customer is not None:
            customer = customer.model_copy(
                update={
                    "valid_from": _utc(customer.valid_from),
                    "created_at": _utc(customer.created_at),
                    "updated_at": _utc(customer.updated_at),
                    "record_hash": record_hash(
                        customer.tenant_id,
                        customer.canonical_customer_id,
                        customer.display_name,
                        customer.region,
                        customer.country_iso2,
                    ),
                }
            )

        ops_events = [
            ev.model_copy(
                update={
                    "event_ts": _utc(ev.event_ts),
                    "record_hash": record_hash(
                        ev.tenant_id,
                        ev.canonical_ops_event_id,
                        ev.service,
                        ev.region,
                        ev.level,
                        ev.status_code,
                        ev.latency_ms,
                    ),
                }
            )
            for ev in cleaned.ops_events
        ]

        ctx.coerced = MapperResult(order=order, customer=customer, ops_events=ops_events)
        return ctx


coerce = CoerceStage()
