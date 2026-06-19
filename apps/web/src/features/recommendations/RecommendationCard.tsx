/**
 * RecommendationCard — the rank-1 prioritized action. Surfaces the title, the
 * computed impact estimate (value + low/high range), an effort tier, the
 * confidence gauge, the explainability accordion, and accept/reject buttons that
 * POST a lifecycle transition through the gateway (`useRecommendationAction`).
 *
 * All figures are computed by L4; the card renders them verbatim. The accept/
 * reject buttons are gated to operator/admin roles (UX-only — the gateway is the
 * authoritative boundary).
 */
import { useRecommendationAction } from "../../query/useRecommendations";
import { useAuth } from "../../auth/useAuth";
import { ConfidenceGauge } from "./ConfidenceGauge";
import { ExplainabilityAccordion } from "./ExplainabilityAccordion";
import { formatMetric } from "../../lib/format";
import type { Recommendation } from "../../schemas/recommendation";

const EFFORT_LABEL: Record<Recommendation["effort_tier"], string> = {
  xs: "XS",
  s: "S",
  m: "M",
  l: "L",
  xl: "XL",
};

const ACTABLE_STATUSES: Recommendation["status"][] = ["proposed"];
const WRITE_ROLES = ["operator", "admin"];

function StatusBadge({ status }: { status: Recommendation["status"] }) {
  const tone =
    status === "accepted"
      ? "border-status-ok/50 text-status-ok"
      : status === "rejected"
        ? "border-status-critical/50 text-status-critical"
        : status === "expired"
          ? "border-border-strong text-fg-subtle"
          : "border-border-strong text-fg-muted";
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-2xs uppercase tracking-wide ${tone}`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

export interface RecommendationCardProps {
  recommendation: Recommendation;
}

export function RecommendationCard({
  recommendation: rec,
}: RecommendationCardProps) {
  const { hasAnyRole } = useAuth();
  const action = useRecommendationAction();
  const canWrite = hasAnyRole(WRITE_ROLES);
  const isActable = ACTABLE_STATUSES.includes(rec.status);
  const pending = action.isPending;

  const impactSign = rec.impact.direction === "decrease" ? "−" : "+";

  return (
    <div className="card card-pad flex flex-col gap-4" data-testid="recommendation-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded bg-accent-muted/40 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-accent">
              #{rec.priority_rank}
            </span>
            <span className="text-2xs uppercase tracking-wide text-fg-subtle">
              {rec.action_type.replace(/_/g, " ")}
            </span>
            <StatusBadge status={rec.status} />
          </div>
          <h3 className="mt-1.5 text-sm font-semibold leading-snug text-fg-default">
            {rec.title}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">
            {rec.explanation_summary}
          </p>
        </div>
        <ConfidenceGauge value={rec.confidence.value} />
      </div>

      <div className="grid grid-cols-3 gap-3 rounded-md border border-border-subtle bg-surface-inset p-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-2xs uppercase tracking-wide text-fg-subtle">
            Est. impact
          </span>
          <span className="kpi-figure text-base text-status-ok">
            {impactSign}
            {formatMetric(rec.impact.value, rec.impact.unit)}
          </span>
          <span className="text-2xs text-fg-subtle">
            {formatMetric(rec.impact.value_low, rec.impact.unit)} –{" "}
            {formatMetric(rec.impact.value_high, rec.impact.unit)}
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-2xs uppercase tracking-wide text-fg-subtle">
            Horizon
          </span>
          <span className="kpi-figure text-base text-fg-default">
            {rec.impact.horizon_days}d
          </span>
        </div>
        <div className="flex flex-col gap-0.5">
          <span className="text-2xs uppercase tracking-wide text-fg-subtle">
            Effort
          </span>
          <span className="kpi-figure text-base text-fg-default">
            {EFFORT_LABEL[rec.effort_tier]}
          </span>
          <span className="text-2xs text-fg-subtle">
            priority {rec.priority_score.toFixed(2)}
          </span>
        </div>
      </div>

      <ExplainabilityAccordion recommendation={rec} />

      {action.isError && (
        <p className="text-2xs text-status-critical">
          Action failed:{" "}
          {action.error instanceof Error ? action.error.message : "unknown error"}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!canWrite || !isActable || pending}
          onClick={() =>
            action.mutate({ id: rec.recommendation_id, action: "accept" })
          }
          className="focus-ring flex-1 rounded-md bg-accent px-3 py-2 text-xs font-semibold text-accent-fg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          data-testid="accept-btn"
        >
          {pending ? "…" : "Accept"}
        </button>
        <button
          type="button"
          disabled={!canWrite || !isActable || pending}
          onClick={() =>
            action.mutate({ id: rec.recommendation_id, action: "reject" })
          }
          className="focus-ring flex-1 rounded-md border border-border-strong px-3 py-2 text-xs font-semibold text-fg-muted transition-colors hover:bg-surface-overlay hover:text-fg-default disabled:cursor-not-allowed disabled:opacity-40"
          data-testid="reject-btn"
        >
          Reject
        </button>
      </div>
      {!canWrite && (
        <p className="text-2xs text-fg-subtle">
          Acting on recommendations requires the operator or admin role.
        </p>
      )}
    </div>
  );
}
