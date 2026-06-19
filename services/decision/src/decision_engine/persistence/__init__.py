"""Async persistence for the L4 store (C2).

The :class:`~decision_engine.persistence.repository.RecommendationRepository` is the
typed access layer the lifecycle manager, the REST API, the finding consumer, and the
no-op outcome recorder all write through. It is tenant-scoped on every read/write (MVP
isolation is application-level filtering; no RLS FORCE) and opens no connection at
import time.
"""

from __future__ import annotations

from decision_engine.persistence.repository import RecommendationRepository

__all__ = ["RecommendationRepository"]
