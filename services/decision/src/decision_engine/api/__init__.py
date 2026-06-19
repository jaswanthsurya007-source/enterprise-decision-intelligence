"""REST API for the decision (L4) service: recommendations + lifecycle + playbooks.

The router is mounted by :func:`decision_engine.main.create_app`. Every route is JWT +
tenant scoped (the verified :class:`~edis_contracts.security.SecurityContext` is the only
source of ``tenant_id``) and RBAC-gated. The data access goes through a repository port
and a :class:`~decision_engine.lifecycle.manager.LifecycleManager`, both resolved via
FastAPI dependencies so tests can inject infra-free fakes with ``dependency_overrides`` --
no Postgres, no broker, no API key required to exercise the API.
"""

from __future__ import annotations

from decision_engine.api.routes_recommendations import router as recommendations_router

__all__ = ["recommendations_router"]
