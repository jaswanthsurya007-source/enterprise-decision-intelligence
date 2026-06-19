"""Deterministic canonical identity — fixed-namespace ``uuid5`` derivation.

MVP keying is *deterministic and reproducible*: there is **no fuzzy entity
resolution**. A source record's stable keys hash, via ``uuid5`` under a single
fixed module-level :data:`NAMESPACE`, to a canonical id. The same source record
always yields the same canonical id -- in any process, on any run -- which is
exactly what makes the ``ON CONFLICT`` upsert idempotent under replay and what
lets two ingress paths (real-time / batch) converge on identical rows.

The namespace UUID is a hard-coded constant (never regenerated): regenerating it
would silently re-key the entire canonical store.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

#: Fixed namespace for every canonical id in the EDIS integration layer.
#: DO NOT regenerate -- changing this re-keys the entire canonical store.
NAMESPACE: UUID = UUID("6f3b1d6e-2c2a-5e7a-9b4f-0a1c2d3e4f50")


def canonical_order_id(tenant_id: str, source_system: str, order_id: str) -> UUID:
    """Stable canonical order id: ``uuid5(NS, "order:{tenant}:{src}:{order_id}")``."""

    return uuid5(NAMESPACE, f"order:{tenant_id}:{source_system}:{order_id}")


def canonical_customer_id(tenant_id: str, customer_id: str) -> UUID:
    """Stable canonical customer id: ``uuid5(NS, "customer:{tenant}:{customer_id}")``.

    Customer identity is intentionally *source-system independent* (only tenant +
    customer_id) so the same customer arriving via different source systems
    deduplicates to one canonical id under the deterministic upsert.
    """

    return uuid5(NAMESPACE, f"customer:{tenant_id}:{customer_id}")


def canonical_product_id(tenant_id: str, sku: str) -> UUID:
    """Stable canonical product id: ``uuid5(NS, "product:{tenant}:{sku}")``."""

    return uuid5(NAMESPACE, f"product:{tenant_id}:{sku}")


def canonical_ops_event_id(
    tenant_id: str,
    idempotency_key: str | None = None,
    *,
    service: str | None = None,
    event_ts_iso: str | None = None,
    message: str | None = None,
    status_code: int | None = None,
    latency_ms: float | None = None,
) -> UUID:
    """Stable canonical ops-event id.

    Prefers the envelope ``idempotency_key`` (already a deterministic content
    hash for ops at L1); falls back to a hash of the identifying ops fields when
    no key is available, so the id is deterministic either way.
    """

    if idempotency_key:
        return uuid5(NAMESPACE, f"ops:{tenant_id}:{idempotency_key}")
    digest = hashlib.sha256(
        "|".join(
            [
                tenant_id,
                service or "",
                event_ts_iso or "",
                message or "",
                "" if status_code is None else str(status_code),
                "" if latency_ms is None else repr(latency_ms),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return uuid5(NAMESPACE, f"ops:{tenant_id}:{digest}")


def record_hash(*parts: object) -> str:
    """Content hash for idempotent upsert / change detection (sha256 hex)."""

    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
