"""Versioned source mappers + the deterministic-identity namespace.

A :class:`~edis_integration.mappers.registry.SourceMapper` is keyed by
``(domain, schema_ref)`` and turns a validated source payload into canonical
entities. Mapping is **pure** (no I/O) so it is directly unit-testable. The
module-level :data:`NAMESPACE` UUID anchors every ``uuid5``-derived canonical id
so identity is stable and replay-safe across processes and runs.
"""

from __future__ import annotations

from edis_integration.mappers.identity import (
    NAMESPACE,
    canonical_customer_id,
    canonical_ops_event_id,
    canonical_order_id,
)
from edis_integration.mappers.registry import (
    MapperResult,
    SourceMapper,
    UnknownMapperError,
    get_mapper,
    register_mapper,
    registered_mappers,
)

__all__ = [
    "NAMESPACE",
    "canonical_customer_id",
    "canonical_order_id",
    "canonical_ops_event_id",
    "SourceMapper",
    "MapperResult",
    "UnknownMapperError",
    "get_mapper",
    "register_mapper",
    "registered_mappers",
]
