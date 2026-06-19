"""Persistence layer for the L2 canonical system-of-record.

Re-exports the public persistence surface:

* The ORM models (``edis_integration.persistence.models``) mirror the
  ``0001_canonical`` + ``0002_integration_outbox`` migrations exactly.
* :class:`SqlAlchemyIntegrationRepo` -- the real async-SQLAlchemy
  :class:`~edis_integration.pipeline.engine.IntegrationRepo` (deterministic
  ``ON CONFLICT`` upserts + transactional outbox), tested under
  ``@pytest.mark.integration``.
* :class:`InMemoryIntegrationRepo` -- the no-infra fake (defined in the engine),
  re-exported here so unit tests + the in-proc service have one import surface.
* :class:`TimeseriesRepo` -- metric read/write + a Timescale-agnostic
  :meth:`~edis_integration.persistence.timeseries_repo.TimeseriesRepo.daily_rollup`.

Importing this package opens no database connection.
"""

from __future__ import annotations

from edis_integration.persistence.repositories import (
    InMemoryIntegrationRepo,
    SqlAlchemyIntegrationRepo,
    SqlAlchemyQuarantineRepo,
    SqlAlchemyUnitOfWork,
    make_repo,
)
from edis_integration.persistence.timeseries_repo import TimeseriesRepo

__all__ = [
    "InMemoryIntegrationRepo",
    "SqlAlchemyIntegrationRepo",
    "SqlAlchemyQuarantineRepo",
    "SqlAlchemyUnitOfWork",
    "TimeseriesRepo",
    "make_repo",
]
