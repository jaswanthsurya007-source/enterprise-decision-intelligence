"""Governance contracts: audit events and the explainability store.

Every data access and AI decision emits an :class:`AuditEvent`. Every AI decision
links to a :class:`Decision` + :class:`Evidence` record whose snapshots are
immutable -- so a narrative is reproducible even if the source data later mutates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Payload of ``edis.governance.audit.v1`` -- emitted by every layer.

    ``audit_id`` is the idempotency key (the audit consumer dedupes on it).
    """

    audit_id: UUID
    occurred_at: datetime
    tenant_id: str
    actor: dict = Field(default_factory=dict)  # {type, id, roles}
    action: Literal[
        "DATA_READ",
        "DATA_WRITE",
        "AI_DECISION",
        "AI_QUERY",
        "AUTH_DENY",
        "RBAC_CHANGE",
        "EXPORT",
    ]
    resource: dict = Field(default_factory=dict)  # {type, id, columns?}
    outcome: Literal["ALLOW", "DENY", "ERROR"]
    reason: str | None = None
    decision_id: UUID | None = None  # link to explainability for AI_* actions
    trace_id: str | None = None
    schema_version: int = 1


class Evidence(BaseModel):
    """An immutable value snapshot plus a live pointer back to the source."""

    evidence_id: UUID
    kind: str  # "metric" | "finding" | "recommendation" | "tool_result"
    summary: str
    snapshot: dict = Field(default_factory=dict)  # frozen values (reproducible)
    ref: dict | None = None  # live pointer
    schema_version: int = 1


class Decision(BaseModel):
    """An explainability record linking an AI decision to its evidence trail."""

    decision_id: UUID
    tenant_id: str
    decision_type: Literal["finding_narrative", "recommendation", "copilot_answer"]
    subject_id: UUID  # finding_id / recommendation_id / answer_id
    actor: dict = Field(default_factory=dict)
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    created_at: datetime
    schema_version: int = 1
