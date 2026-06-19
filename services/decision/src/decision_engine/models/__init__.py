"""SQLAlchemy ORM for the L4 store -- mirrors ``alembic/versions/0001_decision.py``.

The three L4-owned tables hang off the shared :class:`edis_platform.db.session.Base`;
the migration owns the schema and these typed rows are the access layer the repositories
(C2) write through. Importing this package opens no database connection.

The ``calibration_prior`` control-plane table is **owned by the L7 governance service**
(it carries an FK to ``tenant.id`` and is created by the governance migration). The
decision engine reads its static prior through the in-memory
:class:`~decision_engine.scoring.confidence_scorer.CalibrationPriorProvider` port, so it
deliberately does NOT re-declare that table on the shared ``Base`` (doing so collided with
the governance ORM on the shared metadata).
"""

from __future__ import annotations

from decision_engine.models.recommendation import (
    OutcomeReportRow,
    RecommendationLifecycleRow,
    RecommendationRow,
)

__all__ = [
    "RecommendationRow",
    "RecommendationLifecycleRow",
    "OutcomeReportRow",
]
