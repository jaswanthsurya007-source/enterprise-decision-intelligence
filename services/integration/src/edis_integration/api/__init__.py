"""Ops/admin HTTP surface for the integration (L2) service.

Re-exports the single :data:`router` wired into the FastAPI app: health, outbox
lag, quarantine listing, and the reprocess (replay) lever.
"""

from __future__ import annotations

from edis_integration.api.router import router

__all__ = ["router"]
