"""The canonical unified data model -- the system of record everything trusts.

MVP keying is deterministic: ``source_id`` maps 1:1 to a ``canonical_*_id`` via a
stable upsert. The SCD-2 columns (``valid_from``/``valid_to``/``is_current``/
``version``) are present in the contract but the MVP always writes
``valid_to=None``, ``is_current=True``, ``version=1``. Every row carries
``tenant_id``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr


class SourceRef(BaseModel):
    """Provenance back-link from a canonical row to a source record."""

    source_system: str
    source_id: str
    schema_version: int
    match_confidence: float = 1.0  # 1.0 under deterministic upsert; <1.0 once ER is built


class CanonicalCustomer(BaseModel):
    """SCD-2-shaped customer dimension (history is designed-future)."""

    canonical_customer_id: UUID
    tenant_id: str
    legal_name: str
    display_name: str
    primary_email: EmailStr | None = None
    country_iso2: str | None = None  # normalized ISO-3166-1 alpha-2
    industry: str | None = None
    region: str | None = None  # NA | EMEA | APAC | LATAM
    valid_from: datetime
    valid_to: datetime | None = None  # None = current (always None in MVP)
    is_current: bool = True
    version: int = 1
    source_refs: list[SourceRef]
    dq_score: float  # 0..1
    record_hash: str  # content hash for idempotent upsert
    created_at: datetime
    updated_at: datetime


class CanonicalProduct(BaseModel):
    """SCD-2-shaped product dimension (history is designed-future)."""

    canonical_product_id: UUID
    tenant_id: str
    sku: str
    name: str
    category: str | None = None
    uom: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    is_current: bool = True
    version: int = 1
    source_refs: list[SourceRef]
    record_hash: str


class CanonicalOrderLine(BaseModel):
    canonical_product_id: UUID
    sku: str
    qty: int
    unit_price_base: Decimal
    line_amount_base: Decimal


class CanonicalOrder(BaseModel):
    """Immutable sales fact, normalized to a single base currency."""

    canonical_order_id: UUID
    tenant_id: str
    canonical_customer_id: UUID
    order_ts: datetime  # UTC event-time
    currency_base: Literal["USD"] = "USD"
    amount_base: Decimal  # normalized to base currency
    amount_src: Decimal
    currency_src: str
    fx_rate: Decimal
    region: str | None = None
    channel: Literal["web", "partner", "direct"] | None = None
    line_items: list[CanonicalOrderLine]
    source_refs: list[SourceRef]
    record_hash: str
    created_at: datetime


class OpsEvent(BaseModel):
    """Immutable operations fact -- feeds error_rate / latency_p95 metrics."""

    canonical_ops_event_id: UUID
    tenant_id: str
    service: str  # e.g. "checkout-api"
    region: str | None = None
    level: Literal["info", "warn", "error"]
    status_code: int | None = None
    latency_ms: float | None = None
    message: str | None = None
    event_ts: datetime
    source_refs: list[SourceRef]
    record_hash: str


class CustomerActivity(BaseModel):
    """Immutable customer-activity fact -- feeds page_views / sessions metrics."""

    canonical_activity_id: UUID
    tenant_id: str
    canonical_customer_id: UUID | None = None
    session_id: str
    event: str  # "page_view" | "add_to_cart" | "checkout_start"
    region: str | None = None
    channel: Literal["web", "partner", "direct"] | None = None
    props: dict[str, str]
    event_ts: datetime
    source_refs: list[SourceRef]
    record_hash: str


class MetricObservation(BaseModel):
    """A single point written to the TimescaleDB ``metric_observations`` hypertable."""

    tenant_id: str
    metric_key: str  # "revenue" | "orders" | "error_rate" | "latency_p95" | "page_views"
    ts: datetime  # hypertable time column (event-time)
    dimensions: dict[str, str]  # {"region": "EMEA", "channel": "web"}
    value: float
    unit: str | None = None  # "USD" | "count" | "ms" | "pct"
    source_refs: list[SourceRef]
