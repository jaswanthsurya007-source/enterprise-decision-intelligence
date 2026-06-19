"""FastAPI app factory for the decision (L4) service.

Wires the cross-cutting platform machinery (JSON logging, OTel bootstrap, RFC 9457
error handlers) and stashes the shared collaborators on ``app.state``. Building the app
connects to **nothing** -- no Postgres / Redpanda / Redis, and no Anthropic API key is
required -- so the service imports cleanly in CI.

The synthesis + scoring core (C1) is pure and needs no app at all. This factory hangs the
IO layer off the app: the sink (``make_sink``), the intent classifier (lazy Haiku client;
deterministic rule-based fallback when no key), the playbook registry, the deterministic
scoring collaborators, and the finding consumer (built but started by the runner/CLI, not
the web app). With no ``ANTHROPIC_API_KEY`` the classifier runs rule-only and the engine
works fully.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from edis_platform.bus.base import make_sink
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi

from decision_engine.config import get_decision_settings, get_settings
from decision_engine.scoring.confidence_scorer import ConfidenceScorer
from decision_engine.scoring.fact_retriever import FactRetriever
from decision_engine.scoring.impact_estimator import ImpactEstimator
from decision_engine.scoring.prioritizer import Prioritizer
from decision_engine.synthesis.intent_classifier import make_intent_classifier
from decision_engine.synthesis.playbook_registry import PlaybookRegistry

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Start the sink on boot; stop on shutdown (and close the lazy Claude client)."""

    state = app.state
    await state.sink.start()
    _log.info(
        "decision service started",
        extra={
            "sink_backend": state.platform_settings.sink_backend,
            "ttl_hours": state.decision_settings.ttl_hours,
            "llm_classifier_enabled": state.llm_classifier_enabled,
            "default_calibration_prior": state.decision_settings.default_calibration_prior,
        },
    )
    try:
        yield
    finally:
        await state.sink.stop()
        try:
            await state.classifier.aclose()
        except Exception:  # noqa: BLE001 - shutdown best-effort
            pass
        _log.info("decision service stopped")


def create_app() -> "FastAPI":
    """Build the decision FastAPI app. Connects to nothing at construction time."""

    from fastapi import FastAPI

    platform_settings = get_settings()
    decision_settings = get_decision_settings()

    configure_logging(decision_settings.service_name, platform_settings.log_level)
    init_telemetry(platform_settings)

    app = FastAPI(
        title="EDIS Decision (L4)",
        version="0.1.0",
        description=(
            "Consume edis.findings.v1 -> classify intent (Haiku 4.5 structured output, "
            "rule-based fallback) -> bind a typed playbook -> retrieve numeric facts -> "
            "compute a DETERMINISTIC ImpactEstimate + ConfidenceScore + priority -> "
            "persist + publish a Recommendation + manage its lifecycle. All numbers come "
            "from unit-tested code, never the LLM."
        ),
        lifespan=_lifespan,
    )

    # Shared, process-wide collaborators (lazy; started in lifespan).
    sink = make_sink(platform_settings)

    # Intent classifier: a lazy Haiku client (None when no key -> rule path) wrapped in
    # the composite that always falls back to the deterministic rule.
    classifier = make_intent_classifier(
        platform_settings, use_llm=decision_settings.use_llm_classifier
    )

    # Deterministic scoring core (pure; no infra, no key).
    registry = PlaybookRegistry()
    retriever = FactRetriever()
    estimator = ImpactEstimator(band_frac=decision_settings.impact_band_frac)
    scorer = ConfidenceScorer(
        w_insight=decision_settings.confidence_weight_insight,
        w_evidence=decision_settings.confidence_weight_evidence,
        w_calibration=decision_settings.confidence_weight_calibration,
    )
    prioritizer = Prioritizer(
        effort_floor=decision_settings.priority_effort_floor,
        norm_anchor=decision_settings.priority_norm_anchor,
    )

    app.state.platform_settings = platform_settings
    app.state.decision_settings = decision_settings
    app.state.sink = sink
    app.state.classifier = classifier
    app.state.llm_classifier_enabled = (
        classifier is not None and getattr(classifier, "_llm", None) is not None
    )
    app.state.registry = registry
    app.state.retriever = retriever
    app.state.estimator = estimator
    app.state.scorer = scorer
    app.state.prioritizer = prioritizer

    install_exception_handlers(app)
    instrument_fastapi(app)

    # REST API: recommendations + lifecycle (JWT + tenant scoped, RBAC-gated). The data
    # access deps (get_repository / get_lifecycle_manager) are overridable seams so the
    # whole API is testable over an in-memory repo + fake bus with no infra/key.
    from decision_engine.api.routes_recommendations import router as recommendations_router

    app.include_router(recommendations_router)

    @app.get("/health/live", tags=["health"])
    async def _live() -> dict[str, str]:  # pragma: no cover - trivial
        return {"status": "ok", "service": decision_settings.service_name}

    return app
