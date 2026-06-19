"""Decision-service (L4) configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers L4-specific decision knobs on
top. Like the platform settings this is plain data; importing it cannot fail in
CI without a database, broker, or API key.

The scoring knobs here are the *defaults* for the deterministic synthesis core —
every estimator / scorer / prioritizer may always be constructed with explicit
overrides, and the pure cores never read settings directly, so they stay
deterministic and unit-testable. ``ttl_hours`` governs how long a freshly
proposed recommendation stays live before the lifecycle TTL sweeper (C2) expires
it. The static calibration prior defaults give ``ConfidenceScore.components`` a
believable ``historical_calibration`` value with ``calibration_n=0`` (the live
feedback loop is deferred — see §5.4 / §6 "feedback loop").
"""

from __future__ import annotations

from functools import lru_cache

from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionSettings(BaseSettings):
    """L4-specific settings (env prefix ``EDIS_DECISION_``).

    The shared platform :class:`~edis_platform.settings.Settings` (``EDIS_``
    prefix) still governs ``database_url`` / ``redis_url`` / ``sink_backend`` /
    logging / OTel / ``anthropic_api_key``; this class adds only knobs unique to
    the decision layer.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDIS_DECISION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- service identity ---
    service_name: str = "edis-decision"

    # --- consumer wiring ---
    #: Consumer-group id used when subscribing to edis.findings.v1.
    consumer_group: str = "edis-decision"
    #: Consumer-group id for the (no-op) outcome recorder on edis.feedback.outcomes.v1.
    outcome_consumer_group: str = "edis-decision-outcomes"

    # --- recommendation lifecycle ---
    #: Hours a freshly proposed recommendation stays live before the TTL sweeper
    #: (C2) expires it. ``expires_at = created_at + ttl_hours``.
    ttl_hours: int = 72

    # --- classification (Haiku 4.5 structured output; rule-based fallback) ---
    #: Haiku model for the OPTIONAL intent classification. Built lazily and only
    #: if an API key is set; otherwise the deterministic rule-based classifier is
    #: used. Haiku does NOT accept the ``effort`` parameter.
    classifier_model: str = "claude-haiku-4-5"
    #: When False the LLM classifier is never attempted even if a key is present
    #: (forces the rule-based path; handy for fully-deterministic deployments).
    use_llm_classifier: bool = True

    # --- confidence: static per-(tenant,playbook) calibration prior ---
    #: Default historical_calibration prior used when no row exists in the
    #: calibration_prior table (calibration_n=0). A believable mid value so the
    #: confidence breakdown is plausible without a live feedback loop.
    default_calibration_prior: float = 0.74
    #: Blend weights for ConfidenceScore = w_insight*insight + w_evidence*evidence
    #: + w_calibration*historical_calibration (normalized; sum need not be 1.0).
    confidence_weight_insight: float = 0.45
    confidence_weight_evidence: float = 0.30
    confidence_weight_calibration: float = 0.25

    # --- impact band ---
    #: Fractional half-width of the low/high band around ImpactEstimate.value for
    #: closed-form estimators that don't carry their own interval (e.g. +/-30%).
    impact_band_frac: float = 0.30

    # --- prioritization ---
    #: A small floor added to effort_units so priority_score never divides by
    #: zero and the xs tier stays finite.
    priority_effort_floor: float = 0.5
    #: Reference impact used to normalize priority_score into a readable ~0..1 (the
    #: raw score a single small-effort recommendation of this impact would earn maps to
    #: ~0.5). Anchored so the demo's ~$170K / 0.84 / effort=s recommendation reads ~0.93
    #: on its card. Purely cosmetic: the transform is monotone, so RANKING is unaffected.
    priority_norm_anchor: float = 10000.0


@lru_cache
def get_decision_settings() -> DecisionSettings:
    """Return the process-wide, cached :class:`DecisionSettings`."""

    return DecisionSettings()


# Re-export the platform settings accessor so call sites have one import surface.
get_settings = _get_platform_settings
