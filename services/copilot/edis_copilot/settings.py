"""Copilot-service (L5) configuration.

Reuses the shared :class:`edis_platform.settings.Settings` (env-driven, never
connected to a live resource at import) and layers L5-specific copilot knobs on
top — model selection, retrieval/packing budgets, grounding tolerance, and the
per-turn tool-iteration / cost caps the P2 agent loop will read. Like the platform
settings this is plain data; importing it cannot fail in CI without a database,
broker, or API key.

The shared platform ``EDIS_`` settings still govern ``database_url`` /
``redis_url`` / ``sink_backend`` / logging / ``anthropic_api_key`` /
``voyage_api_key``; this class (``EDIS_COPILOT_`` prefix) adds only knobs unique to
the copilot layer. The pure tool/retrieval cores never read settings directly, so
they stay deterministic and unit-testable; callers thread explicit values in.
"""

from __future__ import annotations

from functools import lru_cache

from edis_platform.settings import get_settings as _get_platform_settings
from pydantic_settings import BaseSettings, SettingsConfigDict


class CopilotSettings(BaseSettings):
    """L5-specific settings (env prefix ``EDIS_COPILOT_``)."""

    model_config = SettingsConfigDict(
        env_prefix="EDIS_COPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- service identity ---
    service_name: str = "edis-copilot"

    # --- retrieval / embeddings ---
    #: voyage-3 embedding width — matches the L2 migration's ``vector(1024)`` column
    #: and the L3 embedder (StubEmbedder/VoyageEmbedder EMBEDDING_DIM).
    embedding_dim: int = 1024
    #: Max rows a semantic_search returns (k for the pgvector / in-memory ANN scan).
    semantic_search_k: int = 8
    #: Hard upper bound on any tool's row count (defense against a runaway model arg).
    max_tool_rows: int = 200

    # --- result packing (token budget) ---
    #: Token budget the packer trims combined tool results to before they enter the
    #: model context. A coarse 4-chars/token heuristic is used (no tokenizer needed
    #: offline); P2 may refine with messages.count_tokens when a key is present.
    pack_token_budget: int = 6000
    #: Characters-per-token estimate for the offline packer heuristic.
    chars_per_token: int = 4

    # --- grounding (the P2 verifier reads this; defined here for one config surface) ---
    #: Relative tolerance when matching a numeric claim in an answer against a value
    #: returned by a tool this turn (mirrors L3 grounding_rel_tol).
    grounding_rel_tol: float = 0.02

    # --- agent-loop caps (consumed by P2; defined here so config lives in one place) ---
    #: Hard cap on plan->tool->synthesize iterations in the manual Opus loop.
    max_iterations: int = 6
    #: Streamed max_tokens for the Opus synthesis call (build-spec value ~64000).
    max_output_tokens: int = 64000
    #: Per-tenant daily cost cap (USD) the budget accountant enforces; 0 disables.
    daily_cost_cap_usd: float = 5.0
    #: Per-tool wall-clock timeout (seconds) the dispatcher applies.
    tool_timeout_s: float = 15.0


@lru_cache
def get_copilot_settings() -> CopilotSettings:
    """Return the process-wide, cached :class:`CopilotSettings`."""

    return CopilotSettings()


# Re-export the platform settings accessor so call sites have one import surface.
get_settings = _get_platform_settings
