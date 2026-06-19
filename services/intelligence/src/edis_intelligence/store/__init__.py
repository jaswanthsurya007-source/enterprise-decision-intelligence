"""L3 persistence + publish (the IO layer).

* :mod:`~edis_intelligence.store.models` — SQLAlchemy ORM mirroring the
  ``0001_intelligence`` migration (findings / forecasts / evidence_bundle), with the
  pgvector ``embedding`` column handled so the ORM imports on a plain Postgres too.
* :mod:`~edis_intelligence.store.repositories` — async repos that persist
  Finding / EvidenceBundle / Forecast and read them back (tenant-scoped, paginated),
  plus an in-memory fake so the read API and pipeline are unit-testable.
* :mod:`~edis_intelligence.store.publisher` — emits ``edis.findings.v1`` +
  ``edis.forecasts.v1`` via ``make_sink`` and a lineage event via the governance SDK.
"""

from __future__ import annotations

from edis_intelligence.store.publisher import IntelligencePublisher
from edis_intelligence.store.repositories import (
    InMemoryIntelligenceRepo,
    IntelligenceRepo,
    SqlAlchemyIntelligenceRepo,
    StoredFinding,
    StoredForecast,
    make_repo,
)

__all__ = [
    "IntelligencePublisher",
    "IntelligenceRepo",
    "InMemoryIntelligenceRepo",
    "SqlAlchemyIntelligenceRepo",
    "StoredFinding",
    "StoredForecast",
    "make_repo",
]
