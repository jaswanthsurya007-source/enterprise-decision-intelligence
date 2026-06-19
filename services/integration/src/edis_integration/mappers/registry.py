"""Versioned :class:`SourceMapper` registry keyed by ``(domain, schema_ref)``.

A mapper takes the *validated* source payload (a ``SalesPayloadV1`` /
``OpsPayloadV1``) plus envelope provenance and returns a :class:`MapperResult`:
the canonical entities it produced. Mapping is **pure** -- no I/O, no clock reads
beyond what the caller injects -- so every mapper is directly unit-testable.

Pinning the key on ``(domain, schema_ref)`` (e.g. ``("sales", "sales.v1")``) is
the seam for schema evolution: a ``sales.v2`` source lands a new mapper under the
same domain without disturbing the v1 path, and consumers pin a version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from edis_contracts.canonical import (
    CanonicalCustomer,
    CanonicalOrder,
    OpsEvent,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


@dataclass(frozen=True)
class MapperResult:
    """The canonical entities a single source record mapped to.

    A sales record yields an ``order`` and the ``customer`` it references; an ops
    record yields one ``ops_event``. Lists keep the shape uniform and leave room
    for a record that fans out to several canonical rows.
    """

    order: CanonicalOrder | None = None
    customer: CanonicalCustomer | None = None
    ops_events: list[OpsEvent] = field(default_factory=list)

    def entities(self) -> list[BaseModel]:
        """Flat list of every canonical entity produced (for lineage/iteration)."""

        out: list[BaseModel] = []
        if self.customer is not None:
            out.append(self.customer)
        if self.order is not None:
            out.append(self.order)
        out.extend(self.ops_events)
        return out


@runtime_checkable
class SourceMapper(Protocol):
    """Maps one validated source payload to canonical entities. Pure, no I/O."""

    domain: str
    schema_ref: str

    def map(
        self,
        payload: BaseModel,
        *,
        tenant_id: str,
        source_system: str,
        idempotency_key: str,
        occurred_at,
    ) -> MapperResult:
        """Return the canonical entities for ``payload`` (no side effects)."""
        ...


class UnknownMapperError(LookupError):
    """No mapper is registered for the requested ``(domain, schema_ref)``."""


# (domain, schema_ref) -> mapper instance
_REGISTRY: dict[tuple[str, str], SourceMapper] = {}


def register_mapper(mapper: SourceMapper) -> SourceMapper:
    """Register ``mapper`` under ``(mapper.domain, mapper.schema_ref)``."""

    _REGISTRY[(mapper.domain, mapper.schema_ref)] = mapper
    return mapper


def get_mapper(domain: str, schema_ref: str) -> SourceMapper:
    """Look up the mapper for ``(domain, schema_ref)`` or raise.

    Falls back to a domain-only match (any registered ``schema_ref`` for that
    domain) so a slightly drifted ``schema_ref`` still maps in the MVP where one
    version per domain exists.
    """

    mapper = _REGISTRY.get((domain, schema_ref))
    if mapper is not None:
        return mapper
    for (reg_domain, _reg_ref), reg_mapper in _REGISTRY.items():
        if reg_domain == domain:
            return reg_mapper
    raise UnknownMapperError(f"no mapper for domain={domain!r} schema_ref={schema_ref!r}")


def registered_mappers() -> dict[tuple[str, str], SourceMapper]:
    """Return a copy of the registry (introspection / tests)."""

    return dict(_REGISTRY)


def _bootstrap() -> None:
    """Import the built-in mappers so importing the package registers them."""

    # Local imports avoid a circular import at module load (the mapper modules
    # import this registry to call register_mapper at their own import time).
    from edis_integration.mappers import ops_v1, sales_v1  # noqa: F401


_bootstrap()
