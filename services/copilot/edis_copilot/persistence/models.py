"""SQLAlchemy ORM for the copilot's own persistence (conversations + answers).

The copilot READS the canonical metric hypertable (``metric_observations``, owned by
the L2 migration) and the L3 ``findings`` table via raw/Core SQL in the repository — it
does not own ORM models for those (each layer owns its schema). What the copilot *owns*
is its conversation/answer history, modeled here. Every row carries ``tenant_id`` and is
read/written tenant-scoped.

These hang off the shared :class:`edis_platform.db.session.Base` so they share the one
declarative registry across services. Importing this module opens no connection. The
migration that creates these tables is a P2/Phase-6 artifact; this ORM is the typed
access layer the answer repository writes through.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from edis_platform.db.session import Base
from sqlalchemy import DateTime, Index, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


class CopilotConversationRow(Base):
    """A copilot conversation (a thread of turns) for one tenant + user."""

    __tablename__ = "copilot_conversation"
    __table_args__ = (
        PrimaryKeyConstraint("conversation_id", name="pk_copilot_conversation"),
        Index("ix_copilot_conversation_tenant", "tenant_id"),
        Index("ix_copilot_conversation_tenant_created", "tenant_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class CopilotAnswerRow(Base):
    """One persisted grounded copilot answer (the P2 turn output + provenance).

    ``citations`` and ``facts_used`` capture the grounded provenance the dashboard
    renders as authoritative (numbers come only from these). ``tool_trace`` records the
    tools invoked this turn; ``grounding_passed`` / ``confidence`` record the verifier
    outcome. ``answer_model`` is the LLM model when an LLM answer passed the guard, else
    null (the offline rule-driven path reports null).
    """

    __tablename__ = "copilot_answer"
    __table_args__ = (
        PrimaryKeyConstraint("answer_id", name="pk_copilot_answer"),
        Index("ix_copilot_answer_tenant", "tenant_id"),
        Index("ix_copilot_answer_conversation", "conversation_id"),
        Index("ix_copilot_answer_tenant_created", "tenant_id", "created_at"),
    )

    answer_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list] = mapped_column(JSONB, nullable=False)
    facts_used: Mapped[list] = mapped_column(JSONB, nullable=False)
    tool_trace: Mapped[list] = mapped_column(JSONB, nullable=False)
    grounding_passed: Mapped[bool] = mapped_column(nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
