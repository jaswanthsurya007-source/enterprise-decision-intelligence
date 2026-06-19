"""Per-domain validation at the edge.

After :mod:`ingestion.pipeline.coerce` has fixed source-formatting quirks, the
record is validated against the strict per-domain model
(``SalesPayloadV1`` / ``OpsPayloadV1`` / ``CustomerPayloadV1`` — all
``extra="forbid"``). A failure here is *expected* for bad source data and is the
signal to route the record to the DLQ; it never raises out of the pipeline.
"""

from __future__ import annotations

from typing import Any

from edis_contracts.ingest import (
    CustomerPayloadV1,
    OpsPayloadV1,
    SalesPayloadV1,
)
from pydantic import BaseModel, ValidationError

#: Maps a domain to its validated per-domain payload model.
DOMAIN_MODEL: dict[str, type[BaseModel]] = {
    "sales": SalesPayloadV1,
    "ops": OpsPayloadV1,
    "customer": CustomerPayloadV1,
}


class UnknownDomainError(ValueError):
    """Raised when a record is submitted for a domain with no payload model."""


def model_for(domain: str) -> type[BaseModel]:
    """Return the per-domain payload model, or raise :class:`UnknownDomainError`."""

    model = DOMAIN_MODEL.get(domain)
    if model is None:
        raise UnknownDomainError(f"no payload model for domain {domain!r}")
    return model


def validate(domain: str, coerced: dict[str, Any]) -> BaseModel:
    """Validate a coerced record into its per-domain model.

    Raises :class:`pydantic.ValidationError` on bad data (caught by the engine and
    routed to the DLQ) or :class:`UnknownDomainError` for an unknown domain.
    """

    return model_for(domain).model_validate(coerced)


def format_validation_error(exc: ValidationError) -> str:
    """Render a :class:`ValidationError` as a compact, auditable detail string."""

    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)
