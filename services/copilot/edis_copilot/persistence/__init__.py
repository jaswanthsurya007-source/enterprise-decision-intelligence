"""Copilot persistence: the SQLAlchemy DataPort + the CopilotAnswer store.

* :mod:`edis_copilot.persistence.models` — ORM for the copilot's own ``copilot_conversation`` /
  ``copilot_answer`` tables (P2 persists turns + the grounded answer here).
* :mod:`edis_copilot.persistence.repository` — :class:`SqlAlchemyDataPort`, the real async
  implementation of the read-only :class:`~app.tools.base.DataPort` over the canonical
  metric hypertable (L2) and the findings table (L3) + pgvector; and an answer
  repository. All reads are tenant-scoped (``WHERE tenant_id = :ctx``). Importing this
  module opens no connection; the in-memory fake (in :mod:`edis_copilot.tools.base`) keeps the
  tool layer testable with no DB.
"""

from __future__ import annotations

from edis_copilot.persistence.models import CopilotAnswerRow, CopilotConversationRow
from edis_copilot.persistence.repository import (
    CopilotAnswerRepository,
    InMemoryAnswerRepository,
    SqlAlchemyDataPort,
    make_answer_repository,
    make_data_port,
)

__all__ = [
    "CopilotAnswerRepository",
    "CopilotAnswerRow",
    "CopilotConversationRow",
    "InMemoryAnswerRepository",
    "SqlAlchemyDataPort",
    "make_answer_repository",
    "make_data_port",
]
