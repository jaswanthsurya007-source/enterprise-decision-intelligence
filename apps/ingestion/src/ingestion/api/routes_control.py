"""Control-plane routes — the demo's remote control.

``POST /v1/control/simulator/{start,stop,inject}`` and ``POST /v1/control/seed``
drive the simulator / batch-seed layer that **I3** builds. To keep I2 buildable and
unit-testable *now*, this module defines the thin :class:`SimulatorController`
interface those routes depend on, plus a :class:`NoopSimulatorController` stub the
app factory attaches until I3 provides the real implementation. I3 implements the
same protocol over its generator/scenarios/CLI and swaps it onto
``app.state.simulator_controller`` with no route changes.

Every control action is mutating, so it requires the ``operator`` role (or
``admin``) via :func:`edis_platform.authz.deps.require_role`, is scoped to the
verified token's ``tenant_id`` (never the body), and emits an ``AI_DECISION`` /
``DATA_WRITE``-class audit event through the shared publisher's sink. Anomaly
injection stamps ``anomaly_label`` ground truth downstream (I3 owns that), so an
injected anomaly is later evaluable.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from edis_contracts.security import SecurityContext
from edis_gov_sdk.audit import AuditEmitter
from edis_platform.errors import EdisError, NotFoundError, ValidationProblem
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from starlette.requests import Request

from ingestion.api.deps import (
    get_publisher,
    get_security_context,
    get_simulator_controller,
    require_role,
)

router = APIRouter(prefix="/v1/control", tags=["control"])

AnomalyProfile = Literal["spike", "drop", "drift", "outage"]


# --- the interface I3 implements ---------------------------------------------


class SimulatorStatus(BaseModel):
    """Reported state of the live simulator for a tenant."""

    running: bool
    tenant_id: str
    scenario: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class SimulatorController(Protocol):
    """Thin async control surface over the simulator/seed layer (I3 implements).

    All methods are tenant-scoped (the verified token's tenant is passed in by the
    route, never trusted from the body) and idempotent where it makes sense:
    ``start`` on an already-running tenant is a no-op that reports ``running=True``.
    Implementations must not block the event loop (run any CPU-heavy generation in
    a task/executor) and must never raise for ordinary control flow — raise
    :class:`~edis_platform.errors.EdisError` subclasses for real failures so they
    render as RFC 9457.
    """

    async def start(
        self, tenant_id: str, *, scenario: str | None = None, seed: int | None = None
    ) -> SimulatorStatus:
        """Start the live stream simulator for ``tenant_id`` (optionally a scenario)."""
        ...

    async def stop(self, tenant_id: str) -> SimulatorStatus:
        """Stop the live stream simulator for ``tenant_id``."""
        ...

    async def inject(
        self,
        tenant_id: str,
        *,
        profile: AnomalyProfile | None = None,
        scenario: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inject an anomaly profile or a named scenario; returns an injection summary."""
        ...

    async def seed(
        self,
        tenant_id: str,
        *,
        days: int,
        seed: int = 42,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        """Load ``days`` of history through the pipeline; returns a load summary."""
        ...

    async def status(self, tenant_id: str) -> SimulatorStatus:
        """Report the current simulator state for ``tenant_id``."""
        ...


class NoopSimulatorController:
    """Buildable-now stub: records intent, generates no data.

    Attached by the app factory so the control routes work end-to-end (auth, audit,
    validation, response shape) before I3 exists. It satisfies
    :class:`SimulatorController` structurally. Replace by setting
    ``app.state.simulator_controller`` to the real I3 controller.
    """

    def __init__(self) -> None:
        self._running: dict[str, str | None] = {}

    async def start(
        self, tenant_id: str, *, scenario: str | None = None, seed: int | None = None
    ) -> SimulatorStatus:
        self._running[tenant_id] = scenario
        return SimulatorStatus(
            running=True,
            tenant_id=tenant_id,
            scenario=scenario,
            detail={"impl": "noop", "seed": seed},
        )

    async def stop(self, tenant_id: str) -> SimulatorStatus:
        self._running.pop(tenant_id, None)
        return SimulatorStatus(running=False, tenant_id=tenant_id, detail={"impl": "noop"})

    async def inject(
        self,
        tenant_id: str,
        *,
        profile: AnomalyProfile | None = None,
        scenario: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "impl": "noop",
            "tenant_id": tenant_id,
            "profile": profile,
            "scenario": scenario,
            "params": params or {},
            "injected": False,
        }

    async def seed(
        self,
        tenant_id: str,
        *,
        days: int,
        seed: int = 42,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        return {
            "impl": "noop",
            "tenant_id": tenant_id,
            "days": days,
            "seed": seed,
            "scenario": scenario,
            "records": 0,
        }

    async def status(self, tenant_id: str) -> SimulatorStatus:
        running = tenant_id in self._running
        return SimulatorStatus(
            running=running,
            tenant_id=tenant_id,
            scenario=self._running.get(tenant_id),
            detail={"impl": "noop"},
        )


# --- request bodies ----------------------------------------------------------


class StartRequest(BaseModel):
    """Body for ``POST /v1/control/simulator/start`` (all optional)."""

    scenario: str | None = None
    seed: int | None = None


class InjectRequest(BaseModel):
    """Body for ``POST /v1/control/simulator/inject``.

    Exactly one of ``profile`` (a raw anomaly profile) or ``scenario`` (a named
    scenario like ``revenue_drop_emea``) must be supplied.
    """

    profile: AnomalyProfile | None = None
    scenario: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SeedRequest(BaseModel):
    """Body for ``POST /v1/control/seed`` — load N days of history."""

    days: int = Field(default=90, ge=1, le=3650)
    seed: int = 42
    scenario: str | None = None


# --- helpers -----------------------------------------------------------------


def _require_controller(request: Request) -> SimulatorController:
    controller = get_simulator_controller(request)
    if controller is None:  # pragma: no cover - app factory always attaches one
        raise NotFoundError("Simulator controller is not configured on this service.")
    return controller


async def _audit_control(
    request: Request, ctx: SecurityContext, action_id: str, detail: dict[str, Any]
) -> None:
    """Emit a governance audit event for a control action (best-effort, same sink)."""

    publisher = get_publisher(request)
    emitter = AuditEmitter(publisher.sink)
    await emitter.emit(
        ctx=ctx,
        action="DATA_WRITE",
        resource={"type": "simulator_control", "id": action_id, **detail},
        outcome="ALLOW",
        tenant_id=ctx.tenant_id,
    )


# --- routes ------------------------------------------------------------------


@router.post("/simulator/start", summary="Start the live simulator (tenant-scoped)")
async def start_simulator(
    request: Request,
    body: StartRequest | None = None,
    ctx: SecurityContext = Depends(require_role("operator")),
) -> SimulatorStatus:
    controller = _require_controller(request)
    body = body or StartRequest()
    status = await controller.start(ctx.tenant_id, scenario=body.scenario, seed=body.seed)
    await _audit_control(
        request, ctx, "simulator.start", {"scenario": body.scenario, "seed": body.seed}
    )
    return status


@router.post("/simulator/stop", summary="Stop the live simulator (tenant-scoped)")
async def stop_simulator(
    request: Request,
    ctx: SecurityContext = Depends(require_role("operator")),
) -> SimulatorStatus:
    controller = _require_controller(request)
    status = await controller.stop(ctx.tenant_id)
    await _audit_control(request, ctx, "simulator.stop", {})
    return status


@router.post("/simulator/inject", summary="Inject an anomaly profile or named scenario")
async def inject_anomaly(
    request: Request,
    body: InjectRequest,
    ctx: SecurityContext = Depends(require_role("operator")),
) -> dict[str, Any]:
    if (body.profile is None) == (body.scenario is None):
        raise ValidationProblem(
            "Provide exactly one of 'profile' or 'scenario'.",
            errors=[{"loc": ["body"], "msg": "exactly one of profile|scenario required"}],
        )
    controller = _require_controller(request)
    result = await controller.inject(
        ctx.tenant_id,
        profile=body.profile,
        scenario=body.scenario,
        params=body.params,
    )
    await _audit_control(
        request, ctx, "simulator.inject", {"profile": body.profile, "scenario": body.scenario}
    )
    return result


@router.post("/seed", summary="Load N days of history through the pipeline")
async def seed_history(
    request: Request,
    body: SeedRequest | None = None,
    ctx: SecurityContext = Depends(require_role("operator")),
) -> dict[str, Any]:
    controller = _require_controller(request)
    body = body or SeedRequest()
    result = await controller.seed(
        ctx.tenant_id, days=body.days, seed=body.seed, scenario=body.scenario
    )
    await _audit_control(
        request, ctx, "seed", {"days": body.days, "seed": body.seed, "scenario": body.scenario}
    )
    return result


@router.get("/simulator/status", summary="Report current simulator state (tenant-scoped)")
async def simulator_status(
    request: Request,
    ctx: SecurityContext = Depends(get_security_context),
) -> SimulatorStatus:
    controller = _require_controller(request)
    try:
        return await controller.status(ctx.tenant_id)
    except EdisError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise NotFoundError(f"Simulator status unavailable: {exc}") from exc
