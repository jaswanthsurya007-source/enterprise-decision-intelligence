"""The Playbook Resolver: intent -> typed :class:`ActionTemplate`.

Holds one :class:`ActionTemplate` per :class:`PlaybookIntent`. The MVP wires the
fully-built :class:`OperationalFixTemplate`; every other intent gets a typed *stub*
template (declared with its identity + action_type, but ``built=False`` and
``impact_method="none"`` so the estimator treats it as un-priced).

:meth:`PlaybookRegistry.resolve` binds the matching template to a finding and returns
a :class:`BoundAction`. Construction touches no infrastructure; the registry is a
pure lookup table, safe to share process-wide.
"""

from __future__ import annotations

from edis_contracts.findings import Finding

from decision_engine.synthesis.playbooks.base import (
    ActionTemplate,
    ActionType,
    BoundAction,
    EffortTier,
    PlaybookIntent,
)
from decision_engine.synthesis.playbooks.operational_fix import OperationalFixTemplate


class _StubTemplate(ActionTemplate):
    """A typed, declared-but-unbuilt playbook (seam only).

    Bindable (uses the base :meth:`ActionTemplate.bind`) so synthesis never crashes
    on a stub intent, but ``built=False`` and ``impact_method="none"`` so the
    estimator returns a zero/sentinel impact rather than inventing a number. These
    are the deferred playbooks (pricing_change, inventory_reallocation, ...).
    """

    built = False
    impact_method = "none"

    def __init__(
        self,
        *,
        playbook_id: str,
        action_type: ActionType,
        effort_tier: EffortTier = "m",
    ) -> None:
        self.playbook_id = playbook_id
        self.playbook_version = "0.1"
        self.action_type = action_type
        self.effort_tier = effort_tier


def _default_templates() -> dict[PlaybookIntent, ActionTemplate]:
    """Build the canonical intent -> template table (one built, the rest stubs)."""

    return {
        # --- the one fully-built MVP playbook ---
        PlaybookIntent.OPERATIONAL_FIX: OperationalFixTemplate(),
        # --- typed stubs (deferred; seam only) ---
        PlaybookIntent.PRICING_CHANGE: _StubTemplate(
            playbook_id="pricing_change", action_type="pricing_change", effort_tier="m"
        ),
        PlaybookIntent.INVENTORY_REALLOCATION: _StubTemplate(
            playbook_id="inventory_reallocation",
            action_type="inventory_reallocation",
            effort_tier="l",
        ),
        PlaybookIntent.CUSTOMER_OUTREACH: _StubTemplate(
            playbook_id="customer_outreach", action_type="customer_outreach", effort_tier="s"
        ),
        PlaybookIntent.INVESTIGATE: _StubTemplate(
            playbook_id="investigate", action_type="investigate", effort_tier="s"
        ),
        PlaybookIntent.SCALE_RESOURCE: _StubTemplate(
            playbook_id="scale_resource", action_type="scale_resource", effort_tier="s"
        ),
        PlaybookIntent.NOTIFY: _StubTemplate(
            playbook_id="notify", action_type="notify", effort_tier="xs"
        ),
    }


class PlaybookRegistry:
    """Resolves a :class:`PlaybookIntent` to its :class:`ActionTemplate` and binds it."""

    def __init__(self, templates: dict[PlaybookIntent, ActionTemplate] | None = None) -> None:
        self._templates = templates if templates is not None else _default_templates()
        # Always provide a safe fallback for an unmapped intent.
        self._fallback = self._templates.get(PlaybookIntent.INVESTIGATE) or _StubTemplate(
            playbook_id="investigate", action_type="investigate"
        )

    def template(self, intent: PlaybookIntent) -> ActionTemplate:
        """Return the template for ``intent`` (never raises; falls back to investigate)."""

        return self._templates.get(intent, self._fallback)

    def is_built(self, intent: PlaybookIntent) -> bool:
        """True iff the template for ``intent`` is a fully-built playbook (not a stub)."""

        return bool(self.template(intent).built)

    def resolve(self, intent: PlaybookIntent, finding: Finding) -> BoundAction:
        """Bind the matching template to ``finding`` and return the :class:`BoundAction`."""

        return self.template(intent).bind(finding)
