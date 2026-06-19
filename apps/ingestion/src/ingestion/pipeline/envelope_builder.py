"""Build the :class:`~edis_contracts.ingest.IngestEnvelope`.

The envelope is the stable boundary between *untrusted source reality* and *the
platform*. By the time we build it the payload is already coerced, validated and
keyed; this module just stamps server-side provenance/observability fields
(``event_id``, ``ingest_ts``, W3C ``trace_context``) and freezes the result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from edis_contracts.ingest import Domain, IngestEnvelope
from pydantic import BaseModel

#: ``domain`` -> ``schema_ref`` carried on the envelope (e.g. ``"sales.v1"``).
_SCHEMA_REF = {"sales": "sales.v1", "ops": "ops.v1", "customer": "customer.v1"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_ts_of(domain: str, payload: dict[str, Any]) -> datetime:
    """Pull the business event-time out of the validated payload."""

    if domain == "sales":
        return payload["order_ts"]
    # ops + customer both expose ``event_ts``.
    return payload["event_ts"]


def build_envelope(
    domain: Domain,
    validated: BaseModel,
    *,
    tenant_id: str,
    source_system: str,
    idempotency_key: str,
    trace_context: dict[str, str] | None = None,
    is_synthetic: bool = True,
    anomaly_label: str | None = None,
    producer: str = "ingestion",
) -> IngestEnvelope:
    """Assemble a frozen :class:`IngestEnvelope` around a validated payload.

    ``validated`` is the per-domain pydantic model; it is serialized to a JSON-safe
    dict (``mode="json"``) for the envelope ``payload`` so the bus and ``raw_events``
    store a stable, source-typed body. ``event_ts`` is lifted from the payload
    (already tz-aware UTC after coercion).
    """

    payload_dict = validated.model_dump(mode="json")
    # Read event_ts from the *model* (real datetime) before json-dumping loses type.
    event_ts = _event_ts_of(domain, validated.model_dump())

    return IngestEnvelope(
        event_id=uuid4(),
        idempotency_key=idempotency_key,
        schema_ref=_SCHEMA_REF.get(domain, f"{domain}.v1"),
        domain=domain,
        tenant_id=tenant_id,
        source_system=source_system,
        ingest_ts=_utc_now(),
        event_ts=event_ts,
        producer=producer,
        trace_context=trace_context or {},
        is_synthetic=is_synthetic,
        anomaly_label=anomaly_label,
        payload=payload_dict,
    )
