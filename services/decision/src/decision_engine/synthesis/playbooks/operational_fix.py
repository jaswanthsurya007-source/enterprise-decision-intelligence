"""The one fully-built MVP playbook: ``operational_fix`` (roll back / mitigate).

Maps the demo's EMEA revenue level-shift — driven by a ``checkout-api`` latency /
error-rate regression — to a "mitigate the failing service" action. The template:

* binds to the **service** named in the finding's strongest candidate cause (or in
  the finding's own ``dimensions`` as a fallback), plus the affected ``region``;
* renders the title + ``action_params`` the dashboard/copilot surface;
* declares the deterministic impact method ``recovery_flat`` — the estimator prices
  it as ``daily_loss * affected_days_remaining`` (see ``impact_estimator``).

``effort_tier="s"`` (a small, well-understood operational action: roll back a deploy
or fail over). All of that is qualitative/structural — NO numbers are produced here;
the impact value comes from the deterministic estimator over the fact retriever's
inputs, never from this template and never from the LLM.
"""

from __future__ import annotations

from edis_contracts.findings import Finding

from decision_engine.synthesis.playbooks.base import ActionTemplate, BoundAction

#: The impact method name the estimator dispatches on for this playbook.
RECOVERY_FLAT = "recovery_flat"


class OperationalFixTemplate(ActionTemplate):
    """Built playbook: mitigate a failing service to recover lost revenue."""

    playbook_id = "operational_fix"
    playbook_version = "1.0"
    action_type = "operational_fix"
    effort_tier = "s"
    impact_method = RECOVERY_FLAT
    #: Mitigating the regression *recovers* revenue -> impact increases revenue.
    impact_direction = "increase"
    built = True

    def bind(self, finding: Finding) -> BoundAction:
        """Bind to the failing service + region; render the mitigation action."""

        service = self._failing_service(finding)
        region = dict(finding.dimensions).get("region")

        params: dict = {}
        if service is not None:
            params["service"] = service
        if region is not None:
            params["region"] = region
        # Always record the source metric for the explainability trail.
        params["metric_key"] = finding.metric_key

        return BoundAction(
            playbook_id=self.playbook_id,
            playbook_version=self.playbook_version,
            action_type=self.action_type,
            effort_tier=self.effort_tier,
            impact_method=self.impact_method,
            impact_direction=self.impact_direction,
            title=self._title(service, region),
            action_params=params,
        )

    @staticmethod
    def _failing_service(finding: Finding) -> str | None:
        """The service to mitigate: the strongest leading cause's service, else the
        finding's own ``service`` dimension.

        Picks the candidate cause with the largest absolute correlation that names a
        ``service`` dimension (the lag-aware RCA leader), which on the demo finding is
        ``checkout-api``. Falls back to the finding's dimensions.
        """

        best: str | None = None
        best_corr = -1.0
        for cause in finding.candidate_causes:
            svc = cause.dimensions.get("service")
            if svc is None:
                continue
            mag = abs(cause.correlation)
            if mag > best_corr:
                best_corr = mag
                best = svc
        if best is not None:
            return best
        return dict(finding.dimensions).get("service")

    @staticmethod
    def _title(service: str | None, region: str | None) -> str:
        svc = service or "the failing service"
        where = f" in {region}" if region else ""
        return f"Mitigate {svc} latency{where} (likely deploy regression)"
