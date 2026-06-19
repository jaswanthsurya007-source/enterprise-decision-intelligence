"""FastAPI dependencies for the decision REST API.

These resolve the two collaborators the routes need -- a tenant-scoped
:class:`~decision_engine.persistence.repository.RecommendationRepository` and a
:class:`~decision_engine.lifecycle.manager.LifecycleManager` -- plus the verified
:class:`~edis_contracts.security.SecurityContext` and the RBAC gate.

They are intentionally thin and override-friendly: :func:`get_repository` and
:func:`get_lifecycle_manager` are the seams tests replace via
``app.dependency_overrides`` so the whole API runs over an in-memory repo + fake bus with
NO Postgres, NO broker, and NO API key. In a real deployment they build a repository over
the request-scoped :func:`edis_platform.db.session.get_session` and a manager wired to the
app's sink + event producer.
"""

from __future__ import annotations

from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.deps import get_security_context
from edis_platform.authz.rbac import evaluate
from edis_platform.errors import ForbiddenError
from starlette.requests import Request


async def get_repository(request: Request):
    """Yield a :class:`RecommendationRepository` bound to a request-scoped session.

    Default (production) wiring: open a session via the platform sessionmaker and hand a
    repository over it, committing on success / rolling back on error. Tests override this
    dependency with an in-memory repo, so this code path never runs without a DB in CI.
    """

    from edis_platform.db.session import get_sessionmaker

    from decision_engine.persistence.repository import RecommendationRepository

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield RecommendationRepository(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_lifecycle_manager(request: Request):
    """Build a :class:`LifecycleManager` over a request-scoped repo + the app sink.

    Like :func:`get_repository`, this is the production seam; tests override it with a
    manager wired to an in-memory repo and a fake sink. The session is committed after the
    transition so persist + publish stay together.
    """

    from edis_platform.db.session import get_sessionmaker

    from decision_engine.events.producer import DecisionEventProducer
    from decision_engine.lifecycle.manager import LifecycleManager
    from decision_engine.persistence.repository import RecommendationRepository

    sink = request.app.state.sink
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        repo = RecommendationRepository(session)
        manager = LifecycleManager(repo, DecisionEventProducer(sink), sink)
        try:
            yield manager
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_recommendation(action: str):
    """Dependency factory: RBAC-gate ``action`` on the ``recommendation`` resource.

    Resolves the verified principal, then evaluates the pure static RBAC function. Raises
    :class:`~edis_platform.errors.ForbiddenError` (HTTP 403) when no role grants the
    action -- e.g. a ``viewer`` may ``DATA_READ`` recommendations but not ``accept`` them.
    A missing/invalid token raises :class:`~edis_platform.errors.AuthError` (HTTP 401)
    upstream in :func:`get_security_context`.
    """

    async def _dep(request: Request) -> SecurityContext:
        ctx = await get_security_context(request)
        resource = ResourceRef(type="recommendation")
        if not evaluate(ctx, action, resource):
            raise ForbiddenError(f"Principal lacks permission to '{action}' a recommendation.")
        return ctx

    return _dep
