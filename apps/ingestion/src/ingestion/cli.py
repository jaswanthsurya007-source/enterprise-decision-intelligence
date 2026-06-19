"""``dil`` — the Data Ingestion CLI (I3).

Four commands drive the same pipeline core the REST/control API uses:

* ``dil seed``        — load N days of deterministic history through the pipeline.
* ``dil stream``      — run the live simulator (trickled records) until Ctrl-C.
* ``dil inject``      — inject an anomaly profile or a named scenario.
* ``dil replay-dlq``  — re-run persisted dead-letter records through the pipeline.

Built on Typer. By default everything runs with **no infra**: the in-proc event
sink and in-memory idempotency store are selected (``--backend inproc``) so the
CLI is runnable and importable without Postgres/Redpanda/Redis. Point it at real
infra with ``--backend kafka`` (or set ``EDIS_SINK_BACKEND``) and
``--idempotency redis`` plus a reachable ``--database-url`` for the outbox/DLQ
store.

The entry point is :func:`main` (wired as ``[project.scripts] dil``); ``app`` is
the underlying Typer object for embedding/testing.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

app = typer.Typer(
    name="dil",
    help="EDIS Data Ingestion CLI — seed / stream / inject / replay-dlq.",
    add_completion=False,
    no_args_is_help=True,
)


# --- shared wiring -----------------------------------------------------------


def _build_runtime(
    *,
    backend: str,
    idempotency: str,
    database_url: Optional[str],
    use_db: bool,
):
    """Construct (settings, sink, publisher, idem store, writer) for a run.

    Nothing connects until ``start()`` is awaited. ``writer`` is ``None`` unless
    ``use_db`` is set *and* a sessionmaker can be built, so the default run needs
    no database.
    """

    from edis_platform.bus.base import make_sink
    from edis_platform.settings import Settings

    from ingestion.config import IngestionSettings
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.pipeline.idempotency import make_idempotency_store

    platform_kwargs = {"sink_backend": backend}
    if database_url:
        platform_kwargs["database_url"] = database_url
    platform_settings = Settings(**platform_kwargs)
    ingestion_settings = IngestionSettings(idempotency_backend=idempotency)

    sink = make_sink(platform_settings)
    publisher = IngestPublisher(sink)
    idem = make_idempotency_store(ingestion_settings, platform_settings)

    writer = None
    if use_db:
        from edis_platform.db.session import make_engine, make_sessionmaker

        from ingestion.storage.raw_writer import RawWriter

        engine = make_engine(platform_settings)
        writer = RawWriter(make_sessionmaker(engine))

    return platform_settings, ingestion_settings, sink, publisher, idem, writer


async def _with_runtime(coro_factory, *, backend, idempotency, database_url, use_db):
    """Start sink + idempotency, run ``coro_factory(rt)``, then stop them."""

    rt = _build_runtime(
        backend=backend,
        idempotency=idempotency,
        database_url=database_url,
        use_db=use_db,
    )
    _, _, sink, _, idem, _ = rt
    await sink.start()
    await idem.start()
    try:
        return await coro_factory(rt)
    finally:
        await idem.stop()
        await sink.stop()


# --- commands ----------------------------------------------------------------


@app.command()
def seed(
    tenant: str = typer.Option("acme", help="Tenant id to stamp on every record."),
    days: int = typer.Option(90, min=1, max=3650, help="Days of history to load."),
    seed: int = typer.Option(42, help="RNG seed (same seed -> identical output)."),
    scenario: Optional[str] = typer.Option(
        None, help="Named scenario to inject into the history (e.g. revenue_drop_emea)."
    ),
    backend: str = typer.Option("inproc", help="Event sink backend: inproc|redis|kafka."),
    idempotency: str = typer.Option("memory", help="Dedupe store: memory|redis."),
    database_url: Optional[str] = typer.Option(None, help="Postgres URL for the outbox/DLQ store."),
    use_db: bool = typer.Option(False, "--use-db/--no-db", help="Persist to raw_events/DLQ."),
) -> None:
    """Load N days of deterministic history through the pipeline."""

    async def run(rt):
        from ingestion.sim_control import SimulatorController

        _, settings, _, publisher, idem, writer = rt
        ctrl = SimulatorController(publisher, idem, writer=writer, settings=settings)
        return await ctrl.seed(tenant, days=days, seed=seed, scenario=scenario)

    result = asyncio.run(
        _with_runtime(
            run, backend=backend, idempotency=idempotency, database_url=database_url, use_db=use_db
        )
    )
    typer.echo(_fmt(result))


@app.command()
def stream(
    tenant: str = typer.Option("acme", help="Tenant id to stamp on every record."),
    seed: int = typer.Option(42, help="RNG seed."),
    scenario: Optional[str] = typer.Option(None, help="Optional scenario to run live."),
    delay: float = typer.Option(0.02, help="Inter-record delay (seconds) for a live feed."),
    duration: Optional[float] = typer.Option(
        None, help="Stop after this many seconds (default: run until Ctrl-C)."
    ),
    backend: str = typer.Option("inproc", help="Event sink backend: inproc|redis|kafka."),
    idempotency: str = typer.Option("memory", help="Dedupe store: memory|redis."),
    database_url: Optional[str] = typer.Option(None, help="Postgres URL for the outbox/DLQ store."),
    use_db: bool = typer.Option(False, "--use-db/--no-db", help="Persist to raw_events/DLQ."),
) -> None:
    """Run the live simulator until interrupted (or for ``--duration`` seconds)."""

    async def run(rt):
        from ingestion.sim_control import SimulatorController

        _, settings, _, publisher, idem, writer = rt
        ctrl = SimulatorController(
            publisher, idem, writer=writer, settings=settings, stream_record_delay=delay
        )
        await ctrl.start(tenant, scenario=scenario, seed=seed)
        typer.echo(f"streaming for tenant={tenant} (scenario={scenario}); Ctrl-C to stop")
        try:
            if duration is not None:
                await asyncio.sleep(duration)
            else:
                while True:
                    await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):  # pragma: no cover
            pass
        finally:
            await ctrl.shutdown()
        return {"streamed": True, "tenant": tenant, "scenario": scenario}

    try:
        result = asyncio.run(
            _with_runtime(
                run,
                backend=backend,
                idempotency=idempotency,
                database_url=database_url,
                use_db=use_db,
            )
        )
        typer.echo(_fmt(result))
    except KeyboardInterrupt:  # pragma: no cover
        typer.echo("stopped")


@app.command()
def inject(
    tenant: str = typer.Option("acme", help="Tenant id."),
    profile: Optional[str] = typer.Option(None, help="Anomaly profile: spike|drop|drift|outage."),
    scenario: Optional[str] = typer.Option(None, help="Named scenario (e.g. revenue_drop_emea)."),
    region: Optional[str] = typer.Option(None, help="Scope: region (NA|EMEA|APAC|LATAM)."),
    channel: Optional[str] = typer.Option(None, help="Scope: channel (web|partner|direct)."),
    service: Optional[str] = typer.Option(None, help="Scope: service (for outage)."),
    duration: int = typer.Option(5, help="Duration in days."),
    seed: int = typer.Option(42, help="RNG seed (one-shot path)."),
    backend: str = typer.Option("inproc", help="Event sink backend: inproc|redis|kafka."),
    idempotency: str = typer.Option("memory", help="Dedupe store: memory|redis."),
    database_url: Optional[str] = typer.Option(None, help="Postgres URL for the outbox/DLQ store."),
    use_db: bool = typer.Option(False, "--use-db/--no-db", help="Persist to raw_events/DLQ."),
) -> None:
    """Inject an anomaly profile or a named scenario (one-shot over its window)."""

    if (profile is None) == (scenario is None):
        raise typer.BadParameter("provide exactly one of --profile or --scenario")

    async def run(rt):
        from ingestion.sim_control import SimulatorController

        _, settings, _, publisher, idem, writer = rt
        ctrl = SimulatorController(publisher, idem, writer=writer, settings=settings)
        params = {
            "region": region,
            "channel": channel,
            "service": service,
            "duration_days": duration,
            "seed": seed,
        }
        return await ctrl.inject(tenant, profile=profile, scenario=scenario, params=params)

    result = asyncio.run(
        _with_runtime(
            run, backend=backend, idempotency=idempotency, database_url=database_url, use_db=use_db
        )
    )
    typer.echo(_fmt(result))


@app.command(name="replay-dlq")
def replay_dlq(
    limit: int = typer.Option(500, help="Max DLQ rows to replay."),
    backend: str = typer.Option("inproc", help="Event sink backend: inproc|redis|kafka."),
    idempotency: str = typer.Option("memory", help="Dedupe store: memory|redis."),
    database_url: Optional[str] = typer.Option(None, help="Postgres URL for the DLQ store."),
) -> None:
    """Re-run persisted dead-letter records through the pipeline (requires a DB)."""

    async def run(rt):
        _, settings, _, publisher, idem, writer = rt
        if writer is None:
            return {"error": "replay-dlq requires a database (pass --database-url)."}
        return await _replay_dlq(writer, publisher, idem, settings, limit=limit)

    # replay-dlq always needs the DB store for persisted DLQ rows.
    result = asyncio.run(
        _with_runtime(
            run,
            backend=backend,
            idempotency=idempotency,
            database_url=database_url,
            use_db=database_url is not None,
        )
    )
    typer.echo(_fmt(result))


async def _replay_dlq(writer, publisher, idem, settings, *, limit: int) -> dict:
    """Reprocess persisted DLQ rows through the same pipeline core."""

    from ingestion.pipeline.engine import ingest_record

    rows = await writer.fetch_dlq(limit=limit)
    counts = {"landed": 0, "duplicate": 0, "dlq": 0, "skipped": 0}
    for row in rows:
        if not row.domain or not isinstance(row.raw, dict) or row.tenant_id is None:
            counts["skipped"] += 1
            continue
        raw = row.raw.get("value") if set(row.raw.keys()) == {"value"} else row.raw
        if not isinstance(raw, dict):
            counts["skipped"] += 1
            continue
        res = await ingest_record(
            row.domain,  # type: ignore[arg-type]
            raw,
            tenant_id=row.tenant_id,
            source_system=row.source_system or "replay",
            ctx_sink=publisher,
            idem=idem,
            writer=writer,
            publish_after_land=settings.publish_after_land,
        )
        counts[res.outcome.value] += 1
        if res.ok:
            await writer.mark_dlq_replayed(row.dlq_id)
    return {"replayed": len(rows), "counts": counts}


# --- helpers -----------------------------------------------------------------


def _fmt(result: object) -> str:
    import json

    return json.dumps(result, default=str, indent=2)


def main() -> None:
    """Console-script entry point (``[project.scripts] dil``)."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
