"""The playbook intent enum + the ``ActionTemplate`` base — the typed seam L4 binds.

A **playbook intent** (:class:`PlaybookIntent`) is the small, constrained label the
intent classifier emits (LLM structured output, or the deterministic rule-based
fallback). It maps 1:1 to a :class:`Recommendation.action_type` literal.

An **ActionTemplate** is the typed playbook itself: it knows its ``playbook_id`` /
``playbook_version`` / ``action_type`` / ``effort_tier``, knows which deterministic
**impact method** to use, and renders the action's title and ``action_params`` from a
finding. The MVP ships ONE fully-built template (``operational_fix``); the registry
holds the others as typed *stubs* (declared, not wired into impact estimation).

The template is intentionally a pure data+behavior object that needs no infra:
:meth:`ActionTemplate.bind` takes a :class:`Finding` and produces a
:class:`BoundAction` — title, params, effort, and the impact-method name — which the
deterministic estimator then prices. Nothing here calls an LLM or reads a DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from edis_contracts.findings import Finding

#: The Recommendation.action_type literals (mirrors the contract).
ActionType = Literal[
    "operational_fix",
    "pricing_change",
    "inventory_reallocation",
    "customer_outreach",
    "investigate",
    "scale_resource",
    "notify",
]

#: effort_tier literals (mirrors the contract).
EffortTier = Literal["xs", "s", "m", "l", "xl"]


class PlaybookIntent(str, Enum):
    """The constrained intent the classifier maps a finding to.

    A ``str`` enum so it doubles as the Haiku structured-output value space and as a
    plain string the deterministic classifier returns. Each member equals an
    ``action_type`` literal, so the mapping intent -> action_type is the identity.
    """

    OPERATIONAL_FIX = "operational_fix"
    PRICING_CHANGE = "pricing_change"
    INVENTORY_REALLOCATION = "inventory_reallocation"
    CUSTOMER_OUTREACH = "customer_outreach"
    INVESTIGATE = "investigate"
    SCALE_RESOURCE = "scale_resource"
    NOTIFY = "notify"


@dataclass(frozen=True)
class BoundAction:
    """A template bound to one finding — the concrete action, not yet priced.

    Carries everything the scorer needs that is NOT a computed number: the typed
    identity of the playbook, the rendered ``title`` / ``action_params``, the
    qualitative ``effort_tier``, and the NAME of the deterministic impact method the
    estimator must apply (``impact_method``). The numbers themselves are produced
    downstream by the impact estimator from the fact retriever's inputs.
    """

    playbook_id: str
    playbook_version: str
    action_type: ActionType
    effort_tier: EffortTier
    impact_method: str
    impact_direction: Literal["increase", "decrease", "mitigate"]
    title: str
    action_params: dict[str, Any] = field(default_factory=dict)


class ActionTemplate:
    """Base typed playbook. Subclasses override :meth:`bind` to render the action.

    Subclasses set the class attributes (``playbook_id`` etc.) and implement
    :meth:`bind`. The base provides a sensible default ``bind`` so a *stub* template
    is still bindable (it produces a generic action) without being a fully-built
    playbook. Construction takes no arguments and touches no infrastructure.
    """

    playbook_id: str = "base"
    playbook_version: str = "1.0"
    action_type: ActionType = "investigate"
    effort_tier: EffortTier = "m"
    #: Deterministic impact method the estimator dispatches on (see impact_estimator).
    impact_method: str = "none"
    impact_direction: Literal["increase", "decrease", "mitigate"] = "mitigate"
    #: True only for the one fully-built MVP template; stubs are False.
    built: bool = False

    def bind(self, finding: Finding) -> BoundAction:
        """Bind this template to ``finding``, rendering title + params (default impl).

        The default renders a generic, finding-scoped action so an unbuilt stub still
        yields a coherent :class:`BoundAction`. Built templates override this.
        """

        dims = dict(finding.dimensions)
        return BoundAction(
            playbook_id=self.playbook_id,
            playbook_version=self.playbook_version,
            action_type=self.action_type,
            effort_tier=self.effort_tier,
            impact_method=self.impact_method,
            impact_direction=self.impact_direction,
            title=self._default_title(finding),
            action_params={"metric_key": finding.metric_key, **dims},
        )

    @staticmethod
    def _default_title(finding: Finding) -> str:
        dims = dict(finding.dimensions)
        region = dims.get("region")
        where = f" in {region}" if region else ""
        return f"Investigate {finding.metric_key} anomaly{where}"
