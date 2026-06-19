"""The synthesis entrypoint: a Finding -> a fully-scored Recommendation.

:func:`synthesize` is the pure orchestration of the whole L4 core:

    classify intent -> bind a typed playbook -> retrieve numeric facts ->
    compute a deterministic ImpactEstimate + ConfidenceScore -> compute priority ->
    assemble a Recommendation (status="proposed").

It is ``async`` only because intent classification *may* call Haiku; with the
rule-based classifier (the default with no key) it is fully synchronous in spirit and
deterministic. **Every number on the returned Recommendation comes from the scoring
core, never from the LLM** -- the classifier picks only a playbook label, and prose is
not attached here at all (the optional Opus narrative is a C2 concern, post-validated
against ``impact.inputs``).

The function takes its collaborators by keyword so tests can inject infra-free fakes:
a rule-based classifier, a fact retriever, the impact estimator, the confidence scorer,
and the prioritizer -- all constructible with no DB and no API key. On the demo EMEA
finding this yields ``action_type="operational_fix"``, ``impact.value`` in the
$120K-$200K band, ``confidence.value`` ~0.8-0.9, and ``priority_rank=1``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from edis_contracts.decisions import Recommendation
from edis_contracts.findings import Finding

from decision_engine.scoring.confidence_scorer import ConfidenceScorer
from decision_engine.scoring.fact_retriever import FactRetriever
from decision_engine.scoring.impact_estimator import ImpactEstimator
from decision_engine.scoring.prioritizer import Prioritizer
from decision_engine.synthesis.intent_classifier import Classifier, RuleBasedIntentClassifier
from decision_engine.synthesis.playbook_registry import PlaybookRegistry

#: Default TTL applied to a freshly proposed recommendation (mirrors DecisionSettings).
DEFAULT_TTL_HOURS = 72


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_evidence_trail(finding: Finding, action_params: dict) -> list[dict]:
    """Assemble the explainability link list (finding + causes + playbook rule).

    Pure structural provenance -- the references the dashboard's explainability
    accordion and the governance Decision record fan out from. No numbers are
    *authored* here; figures live on ``impact.inputs`` and the finding itself.
    """

    trail: list[dict] = [
        {
            "type": "finding",
            "id": str(finding.finding_id),
            "metric_key": finding.metric_key,
            "dimensions": dict(finding.dimensions),
        }
    ]
    for cause in finding.candidate_causes[:3]:
        trail.append(
            {
                "type": "root_cause",
                "metric_key": cause.metric_key,
                "dimensions": dict(cause.dimensions),
                "correlation": cause.correlation,
                "contribution_pct": cause.contribution_pct,
            }
        )
    if finding.evidence_ref is not None:
        trail.append({"type": "evidence_bundle", "id": str(finding.evidence_ref)})
    trail.append({"type": "playbook_rule", "params": dict(action_params)})
    return trail


def _explanation_summary(finding: Finding, action_title: str, impact) -> str:
    """A short, grounded one-liner for the recommendation card.

    Composed only from computed figures already present in ``impact.inputs`` /
    the finding, so it is grounded by construction (never LLM prose).
    """

    return (
        f"{action_title}. Estimated {impact.direction} of ~{impact.value:,.0f} {impact.unit} "
        f"over {impact.horizon_days} day(s), from a {finding.deviation_pct:.1f}% deviation in "
        f"{finding.metric_key}."
    )


async def synthesize(
    finding: Finding,
    *,
    classifier: Classifier | None = None,
    registry: PlaybookRegistry | None = None,
    retriever: FactRetriever | None = None,
    estimator: ImpactEstimator | None = None,
    scorer: ConfidenceScorer | None = None,
    prioritizer: Prioritizer | None = None,
    now: datetime | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    recommendation_id: UUID | None = None,
) -> Recommendation:
    """Synthesize one :class:`Recommendation` from one :class:`Finding` (status=proposed).

    Collaborators default to infra-free, key-free implementations (rule-based
    classifier, static-prior scorer), so calling ``synthesize(finding)`` with no extra
    arguments works in CI and in the no-key demo and is deterministic. All impact /
    confidence / priority numbers come from the scoring core.
    """

    classifier = classifier or RuleBasedIntentClassifier()
    registry = registry or PlaybookRegistry()
    retriever = retriever or FactRetriever()
    estimator = estimator or ImpactEstimator()
    scorer = scorer or ConfidenceScorer()
    prioritizer = prioritizer or Prioritizer()
    now = now or _utc_now()

    # 1. classify intent (LLM optional; rule-based fallback) -> typed playbook.
    intent = await classifier.classify(finding)
    action = registry.resolve(intent, finding)

    # 2. retrieve numeric facts deterministically from the finding.
    facts = retriever.retrieve(finding, now=now)

    # 3. deterministic impact + confidence.
    impact = estimator.estimate(action, facts)
    confidence = scorer.score(finding, playbook_id=action.playbook_id)

    # 4. deterministic priority (single-candidate => rank 1).
    priority_score = prioritizer.priority_score(impact, confidence, action.effort_tier)
    priority_rank = 1

    rec_id = recommendation_id or uuid4()
    return Recommendation(
        recommendation_id=rec_id,
        tenant_id=finding.tenant_id,
        source_finding_id=finding.finding_id,
        playbook_id=action.playbook_id,
        playbook_version=action.playbook_version,
        title=action.title,
        action_type=action.action_type,
        action_params=dict(action.action_params),
        impact=impact,
        effort_tier=action.effort_tier,
        confidence=confidence,
        priority_score=priority_score,
        priority_rank=priority_rank,
        explanation_summary=_explanation_summary(finding, action.title, impact),
        evidence_trail=_build_evidence_trail(finding, action.action_params),
        narrative=None,  # optional Opus prose is attached later (C2), never numeric authority
        status="proposed",
        expires_at=now + timedelta(hours=ttl_hours),
        created_at=now,
    )
