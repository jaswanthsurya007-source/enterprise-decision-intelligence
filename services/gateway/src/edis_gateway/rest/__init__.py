"""Tenant-scoped REST snapshot routes (W1).

Four read-only snapshots over the shared Postgres, each behind the
:class:`~edis_gateway.repository.GatewayRepo` port (in-memory fake for unit
tests, SQLAlchemy reader for integration):

* :mod:`edis_gateway.rest.kpis`            -> ``/v1/kpis``           (L2 daily rollup)
* :mod:`edis_gateway.rest.anomalies`       -> ``/v1/anomalies``      (L3 findings)
* :mod:`edis_gateway.rest.recommendations` -> ``/v1/recommendations`` (L4, by priority)
* :mod:`edis_gateway.rest.forecasts`       -> ``/v1/forecasts``      (L3 forecasts)

:data:`rest_router` aggregates all four for the app factory to include.
"""

from __future__ import annotations

from fastapi import APIRouter

from edis_gateway.rest.anomalies import router as anomalies_router
from edis_gateway.rest.forecasts import router as forecasts_router
from edis_gateway.rest.kpis import router as kpis_router
from edis_gateway.rest.recommendations import router as recommendations_router

rest_router = APIRouter()
rest_router.include_router(kpis_router)
rest_router.include_router(anomalies_router)
rest_router.include_router(recommendations_router)
rest_router.include_router(forecasts_router)

__all__ = ["rest_router"]
