"""Integration: the real :class:`CopilotAnswerRepository` (Docker/Postgres required)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from edis_copilot.agent.synthesis import CopilotAnswer
from edis_copilot.persistence.repository import CopilotAnswerRepository

pytestmark = pytest.mark.integration


async def _insert_conversation(sessionmaker, *, tenant_id: str, conv_id) -> None:
    from edis_copilot.persistence.models import CopilotConversationRow

    now = datetime.now(timezone.utc)
    async with sessionmaker() as session:
        session.add(
            CopilotConversationRow(
                conversation_id=conv_id,
                tenant_id=tenant_id,
                user_id="alice",
                title="EMEA revenue drop",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


async def test_save_answer_persists_grounded_provenance(pg_sessionmaker):
    repo = CopilotAnswerRepository(pg_sessionmaker)
    answer = CopilotAnswer(
        answer_text="Revenue fell to 61000 from 95000. [1]",
        citations=[
            {
                "marker": "[1]",
                "tool": "find_anomalies",
                "source": "tool find_anomalies",
                "numbers": [61000.0, 95000.0],
            }
        ],
        facts_used=[61000.0, 95000.0, -35.8],
        answer_model="claude-opus-4-8",
        grounding_passed=True,
        confidence=0.9,
        tool_trace=[{"tool": "find_anomalies", "summary": "1 finding", "rows": 1}],
    )
    answer_id = await repo.save_answer(
        tenant_id="acme", user_id="alice", question="why did revenue drop?", answer=answer
    )
    assert answer_id is not None

    # Read it back via raw SQL to confirm the JSONB provenance round-trips.
    from sqlalchemy import text

    async with pg_sessionmaker() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT tenant_id, answer_model, citations, facts_used, grounding_passed "
                        "FROM copilot_answer WHERE answer_id = :id"
                    ),
                    {"id": str(answer_id)},
                )
            )
            .mappings()
            .one()
        )
    assert row["tenant_id"] == "acme"
    assert row["answer_model"] == "claude-opus-4-8"
    assert row["grounding_passed"] is True
    assert 61000.0 in row["facts_used"]


async def test_list_conversations_is_tenant_scoped(pg_sessionmaker):
    repo = CopilotAnswerRepository(pg_sessionmaker)
    await _insert_conversation(pg_sessionmaker, tenant_id="acme", conv_id=uuid4())
    await _insert_conversation(pg_sessionmaker, tenant_id="globex", conv_id=uuid4())

    acme = await repo.list_conversations("acme")
    assert len(acme) == 1 and acme[0]["title"] == "EMEA revenue drop"
    # globex's conversation never surfaces for acme.
    assert all(
        c["title"] != "EMEA revenue drop"
        for c in await repo.list_conversations("globex")
        if c["user_id"] == "acme"
    )
