"""Intelligence-service (L3) configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers L3-specific detection /
forecasting knobs on top. Like the platform settings this is plain data;
importing it cannot fail in CI without a database, broker, or API key.

The detection knobs here are the *defaults* for the detector registry — a
detector may always be constructed with explicit overrides (the pure cores never
read settings directly, so they stay deterministic and unit-testable).
"""

from __future__ import annotations

from functools import lru_cache

from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntelligenceSettings(BaseSettings):
    """L3-specific settings (env prefix ``EDIS_INTELLIGENCE_``).

    The shared platform :class:`~edis_platform.settings.Settings` (``EDIS_``
    prefix) still governs ``database_url`` / ``redis_url`` / ``sink_backend`` /
    logging / OTel / ``anthropic_api_key`` / ``voyage_api_key``; this class adds
    only knobs unique to the intelligence layer.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDIS_INTELLIGENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- service identity ---
    service_name: str = "edis-intelligence"

    # --- consumer wiring ---
    #: Consumer-group id used when subscribing to edis.metrics.points.v1.
    consumer_group: str = "edis-intelligence"

    # --- detection: robust z-score (point anomalies) ---
    #: Robust z-score magnitude above which a single point is flagged. The robust
    #: z is computed as 0.6745 * (x - median) / MAD (MAD scaled to ~sigma), so a
    #: threshold of 3.5 is the classic Iglewicz/Hoaglin outlier cut.
    z_threshold: float = 3.5

    # --- detection: STL seasonal/level-shift ---
    #: Seasonal period (in samples) for STL. Daily series with weekly seasonality
    #: -> 7. The detector requires >= 2 full periods of data to run.
    stl_period: int = 7
    #: A sustained shift in the STL trend is flagged as a LEVEL_SHIFT when its
    #: magnitude exceeds ``level_shift_k`` * MAD(residual). Tuned so the demo's
    #: ~36% revenue drop trips it with a comfortable margin.
    level_shift_k: float = 3.5
    #: Minimum number of consecutive trailing points that must remain shifted for
    #: the change to count as *sustained* (a level shift, not a transient spike).
    min_shift_run: int = 3

    # --- baselines / windows ---
    #: Days of history the detectors read to form the seasonal expectation /
    #: robust baseline before the candidate window.
    baseline_days: int = 28
    #: Days at the tail of the series treated as the candidate (incident) window.
    eval_window_days: int = 7

    # --- forecasting (X2 wires the model; band knobs live here) ---
    #: Days ahead the single ETS forecast projects (dashboard band).
    forecast_horizon_days: int = 7
    #: Prediction-interval coverage for the forecast band (0..1).
    forecast_interval: float = 0.95

    # --- narration / embeddings (X3 wires the client; knobs live here) ---
    #: Relative tolerance the grounding guard allows when matching a numeric token
    #: in the narrative against a value in EvidenceBundle.allowed_numbers.
    grounding_rel_tol: float = 0.02
    #: Voyage embedding model + dimensionality for copilot retrieval (matches the
    #: pgvector column width in the migration).
    embedding_model: str = "voyage-3"
    embedding_dim: int = 1024


@lru_cache
def get_intelligence_settings() -> IntelligenceSettings:
    """Return the process-wide, cached :class:`IntelligenceSettings`."""

    return IntelligenceSettings()


# Re-export the platform settings accessor so call sites have one import surface.
get_settings = _get_platform_settings
