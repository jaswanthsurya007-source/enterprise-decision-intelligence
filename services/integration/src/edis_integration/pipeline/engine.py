"""The L2 normalization engine -- the one code path the stream/batch consumers reuse.

:func:`process_envelope` runs the full edge-to-canonical flow for a single
:class:`IngestEnvelope`:

    decode -> validate_source -> map -> clean -> coerce -> dq_check
      -> upsert(repo)  -> derive-metrics(repo)  -> stage outbox events

and returns an :class:`IntegrationResult` carrying the canonical entities, the
events staged for publication, and the terminal :class:`IntegrationOutcome`
(``PERSISTED`` / ``QUARANTINED`` / ``DUPLICATE``). It mirrors
``ingestion.pipeline.engine.ingest_record``: collaborators (the repository) are
injected so the function is unit-testable over an in-memory repo with **no
infra**, and the stream and batch consumers share exactly one implementation.

This module also defines the deterministic-identity re-export, the
:class:`IntegrationRepo` port (Protocol) the engine persists through, the
transactional-outbox contract (:class:`OutboxEvent`, staged inside the same
``repo`` unit of work the canonical rows are written in), and an
:class:`InMemoryIntegrationRepo` fake so the pipeline + outbox semantics are
testable without Postgres. The real async-SQLAlchemy repo (a separate unit) is
exercised under ``@pytest.mark.integration``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID, uuid4

from edis_contracts.canonical import CanonicalCustomer, CanonicalOrder, MetricObservation, OpsEvent
from edis_contracts.events import CanonicalEvent, MetricPoint
from edis_contracts.ingest import IngestEnvelope, QuarantinedRecord
from edis_contracts import topics

from edis_integration.mappers.identity import (  # noqa: F401 - re-exported for callers
    NAMESPACE,
)
from edis_integration.mappers.metrics import derive_order_metrics, derive_ops_metrics
from edis_integration.pipeline.stages import StageContext
from edis_integration.pipeline.stages.clean import clean as clean_stage
from edis_integration.pipeline.stages.coerce import coerce as coerce_stage
from edis_integration.pipeline.stages.dq_check import DqCheckStage
from edis_integration.pipeline.stages.decode import decode as decode_stage
from edis_integration.pipeline.stages.map import map_stage
from edis_integration.pipeline.stages.validate_source import validate_source as validate_stage

if TYPE_CHECKING:
    from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Outcome / result / error types (defined here per the unit spec)
# ---------------------------------------------------------------------------
class IntegrationOutcome(str, enum.Enum):
    """Terminal state of one envelope through the integration pipeline."""

    PERSISTED = "persisted"
    QUARANTINED = "quarantined"
    DUPLICATE = "duplicate"


class NormalizationError(Exception):
    """A structural fault in a stage -> the record is quarantined to the DLQ.

    Raised by ``decode`` / ``validate_source`` / ``map`` for problems that make
    the record un-mappable (bad payload shape, schema drift, missing mapper). The
    engine catches it and emits a :class:`QuarantinedRecord` to
    ``edis.dlq.integration.v1``.
    """

    def __init__(self, *, stage: str, error_type: str, detail: str) -> None:
        self.stage = stage
        self.error_type = error_type
        self.detail = detail
        super().__init__(f"[{stage}] {error_type}: {detail}")


@dataclass
class OutboxEvent:
    """One event staged for publication inside the canonical-write transaction.

    The relay (a separate unit) reads unpublished outbox rows, publishes them via
    ``make_sink``, and marks them published -- so there is no
    persisted-but-not-published gap and replay is idempotent.
    """

    topic: str
    key: str | None
    value: "BaseModel"
    event_id: UUID = field(default_factory=uuid4)


@dataclass
class IntegrationResult:
    """The auditable result of processing one envelope.

    ``outcome`` is the terminal state; ``orders`` / ``customers`` / ``ops_events``
    / ``metrics`` are the canonical entities produced; ``outbox`` is the list of
    events the relay will publish; ``quarantine`` is set on a QUARANTINED outcome.
    """

    outcome: IntegrationOutcome
    idempotency_key: str
    orders: list[CanonicalOrder] = field(default_factory=list)
    customers: list[CanonicalCustomer] = field(default_factory=list)
    ops_events: list[OpsEvent] = field(default_factory=list)
    metrics: list[MetricObservation] = field(default_factory=list)
    outbox: list[OutboxEvent] = field(default_factory=list)
    quarantine: QuarantinedRecord | None = None
    dq_score: float = 1.0

    @property
    def ok(self) -> bool:
        """True if the record was persisted or was a (harmless) duplicate."""

        return self.outcome is not IntegrationOutcome.QUARANTINED


# ---------------------------------------------------------------------------
# Repository port + transactional-outbox unit of work
# ---------------------------------------------------------------------------
@runtime_checkable
class IntegrationRepo(Protocol):
    """Persistence port: deterministic id-keyed upsert + outbox in one txn.

    The engine performs all writes for one envelope inside a single
    :meth:`unit_of_work`; the concrete impl (in-memory fake here, async
    SQLAlchemy elsewhere) commits the canonical rows, the metric rows, and the
    outbox rows atomically. Upserts are ``ON CONFLICT (canonical id) DO UPDATE`` /
    ``DO NOTHING`` so a replayed envelope is idempotent.
    """

    def unit_of_work(self) -> "RepoUnitOfWork":
        """Return an async-context-manager unit of work (atomic on exit)."""
        ...

    async def seen_idempotency_key(self, tenant_id: str, key: str) -> bool:
        """True if this envelope's idempotency key was already processed."""
        ...


@runtime_checkable
class RepoUnitOfWork(Protocol):
    """One atomic write scope: canonical upserts + metric rows + outbox rows."""

    async def __aenter__(self) -> "RepoUnitOfWork": ...
    async def __aexit__(self, *exc: object) -> None: ...

    async def upsert_customer(self, customer: CanonicalCustomer) -> bool:
        """Upsert a customer; returns True if newly inserted, False if it existed."""
        ...

    async def upsert_order(self, order: CanonicalOrder) -> bool:
        """Upsert an order; returns True if newly inserted, False if it existed."""
        ...

    async def insert_ops_event(self, event: OpsEvent) -> bool:
        """Insert an ops event (DO NOTHING on conflict); True if newly inserted."""
        ...

    async def insert_metric(self, obs: MetricObservation) -> None:
        """Insert a metric observation row (idempotent on its natural key)."""
        ...

    async def stage_outbox(self, event: OutboxEvent) -> None:
        """Stage an event for the relay to publish, in this same transaction."""
        ...

    async def mark_idempotency_key(self, tenant_id: str, key: str) -> None:
        """Record this envelope's idempotency key as processed."""
        ...


# ---------------------------------------------------------------------------
# Stage pipeline assembly
# ---------------------------------------------------------------------------
def _build_stages(dq_min_score: float):
    """The fixed stage order. ``dq_check`` carries the tenant-tuned threshold."""

    return (
        decode_stage,
        validate_stage,
        map_stage,
        clean_stage,
        coerce_stage,
        DqCheckStage(min_score=dq_min_score),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _schema_ref_for(envelope: IngestEnvelope) -> str:
    """Resolve the mapper key's ``schema_ref`` (e.g. ``"sales.v1"``)."""

    if envelope.schema_ref:
        return envelope.schema_ref
    return f"{envelope.domain}.v1"


def normalize_envelope(
    envelope: IngestEnvelope,
    *,
    dq_min_score: float = 0.5,
) -> StageContext:
    """Run the pure stage pipeline over an envelope (no I/O).

    Returns the terminal :class:`StageContext` (``coerced`` holds the canonical
    entities, ``failure`` is non-empty if quarantined). Raises
    :class:`NormalizationError` for a structural fault. This is the directly
    unit-testable normalization core, separate from persistence.
    """

    ctx = StageContext(
        envelope=envelope,
        tenant_id=envelope.tenant_id,
        source_system=envelope.source_system,
        domain=envelope.domain,
        schema_ref=_schema_ref_for(envelope),
        occurred_at=_utc_now(),
    )
    for stage in _build_stages(dq_min_score):
        ctx = stage(ctx)
    return ctx


# ---------------------------------------------------------------------------
# The consumer entrypoint
# ---------------------------------------------------------------------------
async def process_envelope(
    envelope: IngestEnvelope,
    *,
    repo: IntegrationRepo,
    metric_bucket: str = "hour",
    dq_min_score: float = 0.5,
    run_id: UUID | None = None,
    derive_ops_metrics_inline: bool = True,
) -> IntegrationResult:
    """Decode -> ... -> upsert -> derive-metrics -> stage outbox, transactionally.

    Pure-ish: all side effects go through the injected ``repo``. Returns the
    canonical entities, the events staged for publication, and the outcome
    (``PERSISTED`` / ``QUARANTINED`` / ``DUPLICATE``). Never raises for *data*
    problems -- a structural fault becomes a ``QUARANTINED`` result carrying a
    :class:`QuarantinedRecord` bound for ``edis.dlq.integration.v1``.

    ``derive_ops_metrics_inline`` controls whether the **ratio/percentile** ops
    metrics (``error_rate`` / ``latency_p95``) are derived here, per envelope. Per
    the architecture those metrics are a *pure function over a list of ops events*
    bucketed per ``(service, region)`` window -- a true bucket ``error_rate`` and
    ``latency_p95`` only exist over the *whole* bucket, not one event at a time.
    The reactive stream path leaves this ``True`` (each event contributes a 1-event
    sample; a downstream ``avg`` recovers the bucket ``error_rate`` exactly, while
    ``latency_p95`` is best-effort until the bucket completes). The batch path sets
    it ``False`` and aggregates the full bucket in one pass via
    :func:`~edis_integration.mappers.metrics.derive_ops_metrics` so the goal-check
    series (~0.4%->~9% error_rate, ~180ms->~1400ms p95) are exact. The order
    (additive ``revenue``/``orders``) metrics are always derived inline -- they are
    correctly additive per event.
    """

    key = envelope.idempotency_key
    run_id = run_id or uuid4()

    # Idempotent replay: a key already processed is a harmless duplicate.
    if await repo.seen_idempotency_key(envelope.tenant_id, key):
        return IntegrationResult(outcome=IntegrationOutcome.DUPLICATE, idempotency_key=key)

    # --- normalize (pure) ---
    try:
        ctx = normalize_envelope(envelope, dq_min_score=dq_min_score)
    except NormalizationError as exc:
        return _quarantine(envelope, key, reason=exc.error_type, failures=[exc.detail])

    if ctx.quarantined:
        return _quarantine(
            envelope, key, reason="dq_check_failed", failures=ctx.failure, dq_score=ctx.dq_score
        )

    assert ctx.coerced is not None
    result = ctx.coerced

    orders = [result.order] if result.order is not None else []
    customers = [result.customer] if result.customer is not None else []
    ops_events = list(result.ops_events)

    # --- derive metrics (pure) ---
    metrics: list[MetricObservation] = []
    for order in orders:
        metrics.extend(derive_order_metrics(order))
    if ops_events and derive_ops_metrics_inline:
        metrics.extend(derive_ops_metrics(ops_events, granularity=metric_bucket))

    # --- one transaction: upserts + metric rows + outbox + idempotency ---
    outbox: list[OutboxEvent] = []
    async with repo.unit_of_work() as uow:
        for customer in customers:
            await uow.upsert_customer(customer)
            outbox.append(
                _canonical_event(
                    customer, entity="customer", run_id=run_id, key_attr="canonical_customer_id"
                )
            )
        for order in orders:
            await uow.upsert_order(order)
            outbox.append(
                _canonical_event(
                    order, entity="order", run_id=run_id, key_attr="canonical_order_id"
                )
            )
        for ev in ops_events:
            await uow.insert_ops_event(ev)
        for obs in metrics:
            await uow.insert_metric(obs)
            outbox.append(_metric_point(obs, source=envelope.source_system))

        # lineage edge: raw_event -> canonical + metric outputs, one per run.
        outbox.append(_lineage_event(envelope, orders, customers, ops_events, metrics, run_id))

        for event in outbox:
            await uow.stage_outbox(event)
        await uow.mark_idempotency_key(envelope.tenant_id, key)

    return IntegrationResult(
        outcome=IntegrationOutcome.PERSISTED,
        idempotency_key=key,
        orders=orders,
        customers=customers,
        ops_events=ops_events,
        metrics=metrics,
        outbox=outbox,
        dq_score=ctx.dq_score,
    )


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------
def _canonical_event(entity_model, *, entity: str, run_id: UUID, key_attr: str) -> OutboxEvent:
    canonical_id: UUID = getattr(entity_model, key_attr)
    now = _utc_now()
    occurred = getattr(entity_model, "order_ts", None) or getattr(entity_model, "created_at", now)
    event = CanonicalEvent(
        event_id=uuid4(),
        tenant_id=entity_model.tenant_id,
        entity=entity,  # type: ignore[arg-type]
        op="created",
        occurred_at=occurred,
        emitted_at=now,
        canonical_id=canonical_id,
        before=None,
        after=entity_model.model_dump(mode="json"),
        lineage_run_id=run_id,
    )
    topic = topics.canonical_topic(entity)
    return OutboxEvent(topic=topic, key=str(canonical_id), value=event, event_id=event.event_id)


def _metric_point(obs: MetricObservation, *, source: str) -> OutboxEvent:
    point = MetricPoint(
        tenant_id=obs.tenant_id,
        metric_key=obs.metric_key,
        ts=obs.ts,
        value=obs.value,
        dimensions=obs.dimensions,
        unit=obs.unit,
        source=source,
    )
    dim_hash = "&".join(f"{k}={v}" for k, v in sorted(obs.dimensions.items()))
    key = f"{obs.tenant_id}:{obs.metric_key}:{dim_hash}"
    return OutboxEvent(topic=topics.METRICS_POINTS, key=key, value=point)


def _lineage_event(
    envelope: IngestEnvelope,
    orders: list[CanonicalOrder],
    customers: list[CanonicalCustomer],
    ops_events: list[OpsEvent],
    metrics: list[MetricObservation],
    run_id: UUID,
) -> OutboxEvent:
    # Single edge-construction source of truth (raw_event -> canonical + metric).
    from edis_integration.lineage.emitter import build_integration_lineage

    event = build_integration_lineage(
        envelope,
        run_id=run_id,
        orders=orders,
        customers=customers,
        ops_events=ops_events,
        metrics=metrics,
    )
    return OutboxEvent(topic=topics.LINEAGE, key=envelope.tenant_id, value=event)


def _quarantine(
    envelope: IngestEnvelope,
    key: str,
    *,
    reason: str,
    failures: list[str],
    dq_score: float = 0.0,
) -> IntegrationResult:
    record = QuarantinedRecord(
        quarantine_id=uuid4(),
        tenant_id=envelope.tenant_id,
        stage="integration",
        reason=reason,
        dq_failures=failures,
        raw=envelope.model_dump(mode="json"),
        occurred_at=_utc_now(),
    )
    outbox = [OutboxEvent(topic=topics.DLQ_INTEGRATION, key=envelope.tenant_id, value=record)]
    return IntegrationResult(
        outcome=IntegrationOutcome.QUARANTINED,
        idempotency_key=key,
        quarantine=record,
        outbox=outbox,
        dq_score=dq_score,
    )


# ---------------------------------------------------------------------------
# In-memory repository fake -- pipeline + outbox semantics WITHOUT Postgres
# ---------------------------------------------------------------------------
class _InMemoryUnitOfWork:
    """Buffers writes, committing them to the parent repo atomically on exit.

    Mirrors a DB transaction: every write is staged in local buffers and flushed
    into the repo only on a clean ``__aexit__``; an exception inside the ``async
    with`` discards the buffers (rollback), so the canonical rows, metric rows,
    and outbox rows are all-or-nothing -- the property the transactional outbox
    relies on.
    """

    def __init__(self, repo: "InMemoryIntegrationRepo") -> None:
        self._repo = repo
        self._customers: dict[UUID, CanonicalCustomer] = {}
        self._orders: dict[UUID, CanonicalOrder] = {}
        self._ops: dict[UUID, OpsEvent] = {}
        self._metrics: list[MetricObservation] = []
        self._outbox: list[OutboxEvent] = []
        self._keys: list[tuple[str, str]] = []

    async def __aenter__(self) -> "_InMemoryUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            return  # rollback: drop the buffers
        self._repo.customers.update(self._customers)
        self._repo.orders.update(self._orders)
        self._repo.ops_events.update(self._ops)
        self._repo.metrics.extend(self._metrics)
        self._repo.outbox.extend(self._outbox)
        for tenant_id, key in self._keys:
            self._repo.idempotency_keys.add((tenant_id, key))

    async def upsert_customer(self, customer: CanonicalCustomer) -> bool:
        new = customer.canonical_customer_id not in self._repo.customers
        self._customers[customer.canonical_customer_id] = customer
        return new

    async def upsert_order(self, order: CanonicalOrder) -> bool:
        new = order.canonical_order_id not in self._repo.orders
        self._orders[order.canonical_order_id] = order
        return new

    async def insert_ops_event(self, event: OpsEvent) -> bool:
        new = event.canonical_ops_event_id not in self._repo.ops_events
        if new:
            self._ops[event.canonical_ops_event_id] = event
        return new

    async def insert_metric(self, obs: MetricObservation) -> None:
        self._metrics.append(obs)

    async def stage_outbox(self, event: OutboxEvent) -> None:
        self._outbox.append(event)

    async def mark_idempotency_key(self, tenant_id: str, key: str) -> None:
        self._keys.append((tenant_id, key))


class InMemoryIntegrationRepo:
    """In-memory :class:`IntegrationRepo` for unit tests (no Postgres).

    Satisfies the port structurally; the engine and outbox semantics are fully
    exercisable over it. The real async-SQLAlchemy repo is tested separately under
    ``@pytest.mark.integration``.
    """

    def __init__(self) -> None:
        self.customers: dict[UUID, CanonicalCustomer] = {}
        self.orders: dict[UUID, CanonicalOrder] = {}
        self.ops_events: dict[UUID, OpsEvent] = {}
        self.metrics: list[MetricObservation] = []
        self.outbox: list[OutboxEvent] = []
        self.idempotency_keys: set[tuple[str, str]] = set()

    def unit_of_work(self) -> _InMemoryUnitOfWork:
        return _InMemoryUnitOfWork(self)

    async def seen_idempotency_key(self, tenant_id: str, key: str) -> bool:
        return (tenant_id, key) in self.idempotency_keys

    def rollup_daily(self) -> list[dict]:
        """Compute the daily rollup from the in-memory metric rows (Timescale parity)."""

        from edis_integration.mappers.metrics import rollup_daily

        return rollup_daily(self.metrics)
