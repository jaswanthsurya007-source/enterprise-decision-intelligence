"""The real :class:`SimulatorController` (I3) the control routes drive.

Implements the I2 :class:`~ingestion.api.routes_control.SimulatorController`
protocol over the deterministic simulator + the shared pipeline core. It replaces
the ``NoopSimulatorController`` on ``app.state.simulator_controller`` so the
control API (start/stop/inject/seed/status) actually produces data downstream.

Design:

* **Tenant-scoped, non-blocking.** ``start`` launches a background ``asyncio``
  task per tenant that streams generated records through
  :func:`ingestion.pipeline.engine.ingest_record` (with a small inter-record
  delay so it reads as a live feed). CPU-bound day generation happens in the pure
  generator; the loop only awaits IO, so it never blocks.
* **inject** mutates the *running* stream's anomaly schedule live (so an anomaly
  "appears" downstream with ground-truth labels) and, if the tenant is not
  streaming, runs a one-shot injection over the affected day window.
* **seed** loads N days of history through the pipeline as fast as possible and
  returns a load tally.
* Idempotent where sensible: ``start`` on an already-running tenant is a no-op
  reporting ``running=True``; ``stop`` cancels the task cleanly.

Collaborators (publisher, idempotency store, writer, settings) are injected, so
the controller is unit-testable with the in-proc sink + in-memory store and no
infra.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ingestion.api.routes_control import AnomalyProfile, SimulatorStatus
from ingestion.connectors.base import SimulatorConnector
from ingestion.pipeline.engine import ingest_record
from ingestion.simulator.anomalies import AnomalyState, make_anomaly
from ingestion.simulator.generator import SimConfig, generate_day
from ingestion.simulator.scenarios import get_scenario

if TYPE_CHECKING:
    from ingestion.config import IngestionSettings
    from ingestion.pipeline.idempotency import IdempotencyStore
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.storage.raw_writer import RawWriter


@dataclass
class _TenantStream:
    """Live-stream bookkeeping for one tenant."""

    task: asyncio.Task
    cfg: SimConfig
    scenario: str | None
    anomalies: list[AnomalyState] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SimulatorController:
    """Concrete I3 controller satisfying the I2 ``SimulatorController`` protocol."""

    def __init__(
        self,
        publisher: "IngestPublisher",
        idem: "IdempotencyStore",
        *,
        writer: "RawWriter | None" = None,
        settings: "IngestionSettings | None" = None,
        stream_record_delay: float = 0.02,
        stream_window_days: int = 1,
    ) -> None:
        self._publisher = publisher
        self._idem = idem
        self._writer = writer
        self._settings = settings
        self._delay = stream_record_delay
        self._window_days = stream_window_days
        self._streams: dict[str, _TenantStream] = {}
        self._lock = asyncio.Lock()

    def set_writer(self, writer: "RawWriter | None") -> None:
        """Swap the outbox writer.

        The app lifespan calls this with ``None`` when the database is
        unreachable, so the controller runs in publish-only mode (events still
        reach the bus; raw_events simply aren't persisted).
        """

        self._writer = writer

    # --- config helpers --------------------------------------------------------

    def _cfg_for(self, tenant_id: str, seed: int | None) -> SimConfig:
        source = self._settings.default_source_system if self._settings else "simulator"
        return SimConfig(
            seed=42 if seed is None else seed,
            tenant_id=tenant_id,
            source_system=source,
        )

    @property
    def _publish_after_land(self) -> bool:
        return self._settings.publish_after_land if self._settings else True

    # --- one-shot ingestion of a set of pre-generated days ---------------------

    async def _ingest_day(
        self, day: datetime, cfg: SimConfig, anomalies: list[AnomalyState]
    ) -> dict[str, int]:
        """Generate and ingest one day; return per-outcome counts."""

        data = generate_day(day, cfg, anomalies)
        counts = {"landed": 0, "duplicate": 0, "dlq": 0}
        for domain, raws in (("sales", data.sales), ("ops", data.ops)):
            for raw in raws:
                label = raw.get("anomaly_label")
                clean = {k: v for k, v in raw.items() if k != "anomaly_label"}
                res = await ingest_record(
                    domain,  # type: ignore[arg-type]
                    clean,
                    tenant_id=cfg.tenant_id,
                    source_system=cfg.source_system,
                    ctx_sink=self._publisher,
                    idem=self._idem,
                    writer=self._writer,
                    anomaly_label=label,
                    publish_after_land=self._publish_after_land,
                )
                counts[res.outcome.value] += 1
        return counts

    # --- live stream task ------------------------------------------------------

    async def _run_stream(self, tenant_id: str, cfg: SimConfig) -> None:
        """Background task: trickle a rolling window of generated records forever.

        Re-reads the tenant's anomaly schedule each loop so a live ``inject``
        takes effect on the next window without restarting the stream.
        """

        try:
            while True:
                stream = self._streams.get(tenant_id)
                anomalies = list(stream.anomalies) if stream else []
                today = datetime.now(timezone.utc)
                start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
                connector = SimulatorConnector(
                    cfg=cfg,
                    start=start,
                    n_days=self._window_days,
                    anomalies=anomalies,
                    record_delay=self._delay,
                    order="chronological",
                )
                async for rec in connector.iter_records():
                    await ingest_record(
                        rec.domain,
                        rec.raw,
                        tenant_id=cfg.tenant_id,
                        source_system=rec.source_system or cfg.source_system,
                        ctx_sink=self._publisher,
                        idem=self._idem,
                        writer=self._writer,
                        anomaly_label=rec.anomaly_label,
                        publish_after_land=self._publish_after_land,
                    )
                # Loop again to keep the stream "live" (idempotency dedupes repeats).
                await asyncio.sleep(self._delay or 0.0)
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise

    # --- SimulatorController protocol -----------------------------------------

    async def start(
        self, tenant_id: str, *, scenario: str | None = None, seed: int | None = None
    ) -> SimulatorStatus:
        async with self._lock:
            existing = self._streams.get(tenant_id)
            if existing and not existing.task.done():
                return SimulatorStatus(
                    running=True,
                    tenant_id=tenant_id,
                    scenario=existing.scenario,
                    detail={"impl": "simulator", "already_running": True},
                )

            cfg = self._cfg_for(tenant_id, seed)
            anomalies: list[AnomalyState] = []
            if scenario:
                anomalies = get_scenario(scenario)(date.today())

            task = asyncio.create_task(self._run_stream(tenant_id, cfg))
            self._streams[tenant_id] = _TenantStream(
                task=task, cfg=cfg, scenario=scenario, anomalies=anomalies
            )
            return SimulatorStatus(
                running=True,
                tenant_id=tenant_id,
                scenario=scenario,
                detail={"impl": "simulator", "seed": cfg.seed},
            )

    async def stop(self, tenant_id: str) -> SimulatorStatus:
        async with self._lock:
            stream = self._streams.pop(tenant_id, None)
        if stream is not None:
            stream.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream.task
        return SimulatorStatus(running=False, tenant_id=tenant_id, detail={"impl": "simulator"})

    async def inject(
        self,
        tenant_id: str,
        *,
        profile: AnomalyProfile | None = None,
        scenario: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        anchor_day = date.today()
        duration = int(params.get("duration_days", 5))

        if scenario:
            anomalies = get_scenario(scenario)(anchor_day, duration)
        elif profile:
            anomalies = [
                make_anomaly(
                    profile,
                    start_day=anchor_day,
                    duration_days=duration,
                    region=params.get("region"),
                    channel=params.get("channel"),
                    service=params.get("service"),
                    magnitude=params.get("magnitude"),
                )
            ]
        else:  # pragma: no cover - route enforces exactly-one
            raise ValueError("inject requires a profile or a scenario")

        stream = self._streams.get(tenant_id)
        if stream is not None and not stream.task.done():
            # Live: graft onto the running stream's schedule (takes effect next window).
            stream.anomalies.extend(anomalies)
            if scenario:
                stream.scenario = scenario
            return {
                "impl": "simulator",
                "tenant_id": tenant_id,
                "profile": profile,
                "scenario": scenario,
                "mode": "live",
                "anomalies": len(anomalies),
                "injected": True,
            }

        # Not streaming: run a one-shot ingest over the affected day window.
        cfg = self._cfg_for(tenant_id, params.get("seed"))
        start_day = min(a.start_day for a in anomalies)
        end_day = max(a.end_day for a in anomalies)
        total = {"landed": 0, "duplicate": 0, "dlq": 0}
        cursor = start_day
        while cursor <= end_day:
            day = datetime(cursor.year, cursor.month, cursor.day, tzinfo=timezone.utc)
            counts = await self._ingest_day(day, cfg, anomalies)
            for k, v in counts.items():
                total[k] += v
            cursor += timedelta(days=1)
        return {
            "impl": "simulator",
            "tenant_id": tenant_id,
            "profile": profile,
            "scenario": scenario,
            "mode": "one_shot",
            "days": (end_day - start_day).days + 1,
            "counts": total,
            "injected": True,
        }

    async def seed(
        self,
        tenant_id: str,
        *,
        days: int,
        seed: int = 42,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._cfg_for(tenant_id, seed)
        # Seed the N days ending today so a scenario anchored "7 days ago" lands
        # inside the history (matches the demo's "starting 7 days ago").
        today = datetime.now(timezone.utc)
        end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        start = end - timedelta(days=days - 1)

        anomalies: list[AnomalyState] = []
        if scenario:
            # Anchor the incident 7 days before the end of the seeded history.
            anchor = (end - timedelta(days=7)).date()
            anomalies = get_scenario(scenario)(anchor)

        total = {"landed": 0, "duplicate": 0, "dlq": 0}
        for i in range(days):
            day = start + timedelta(days=i)
            counts = await self._ingest_day(day, cfg, anomalies)
            for k, v in counts.items():
                total[k] += v

        return {
            "impl": "simulator",
            "tenant_id": tenant_id,
            "days": days,
            "seed": seed,
            "scenario": scenario,
            "records": total["landed"] + total["duplicate"] + total["dlq"],
            "counts": total,
        }

    async def status(self, tenant_id: str) -> SimulatorStatus:
        stream = self._streams.get(tenant_id)
        running = stream is not None and not stream.task.done()
        return SimulatorStatus(
            running=running,
            tenant_id=tenant_id,
            scenario=stream.scenario if stream else None,
            detail={
                "impl": "simulator",
                "anomalies": len(stream.anomalies) if stream else 0,
            },
        )

    async def shutdown(self) -> None:
        """Cancel every running stream (called from the app lifespan on shutdown)."""

        tenants = list(self._streams)
        for tenant_id in tenants:
            await self.stop(tenant_id)
