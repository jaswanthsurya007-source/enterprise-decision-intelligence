/**
 * CitationChip — renders a `[n]` citation as a chip and, on hover/expand, the
 * STRUCTURED source it points at: a tool name, a cited `Finding`, or a
 * `Recommendation`. Any number shown here is read from the validated structured
 * fields of the embedded object — NEVER parsed from narrative text (pin §5.6).
 */
import { useState } from "react";
import { formatMetric, formatDeltaPct } from "../../lib/format";
import type { CopilotCitation } from "../../schemas/copilot";

function FindingDetail({
  finding,
}: {
  finding: NonNullable<CopilotCitation["finding"]>;
}) {
  return (
    <div className="space-y-1">
      <div className="font-medium text-fg-default">
        {finding.metric_key}{" "}
        <span className="text-fg-subtle">
          ({finding.kind.replace(/_/g, " ")})
        </span>
      </div>
      <div className="flex gap-3 text-fg-muted">
        <span>
          obs{" "}
          <span className="kpi-figure">
            {formatMetric(finding.observed_value)}
          </span>
        </span>
        <span>
          exp{" "}
          <span className="kpi-figure">
            {formatMetric(finding.expected_value)}
          </span>
        </span>
        <span className="kpi-figure text-status-critical">
          {formatDeltaPct(finding.deviation_pct)}
        </span>
      </div>
    </div>
  );
}

function RecommendationDetail({
  recommendation,
}: {
  recommendation: NonNullable<CopilotCitation["recommendation"]>;
}) {
  return (
    <div className="space-y-1">
      <div className="font-medium text-fg-default">{recommendation.title}</div>
      <div className="flex gap-3 text-fg-muted">
        <span>
          impact{" "}
          <span className="kpi-figure text-status-ok">
            {formatMetric(recommendation.impact.value, recommendation.impact.unit)}
          </span>
        </span>
        <span>
          conf{" "}
          <span className="kpi-figure">
            {recommendation.confidence.value.toFixed(2)}
          </span>
        </span>
      </div>
    </div>
  );
}

export interface CitationChipProps {
  citation: CopilotCitation;
}

export function CitationChip({ citation }: CitationChipProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="focus-ring rounded border border-accent/40 bg-accent-muted/20 px-1.5 py-0.5 text-2xs font-medium text-accent hover:bg-accent-muted/40"
        data-testid="citation-chip"
      >
        [{citation.index}] {citation.label}
      </button>
      {open && (
        <div className="mt-1 rounded-md border border-border-subtle bg-surface-inset p-2 text-2xs">
          {citation.source_tool && (
            <div className="mb-1 font-mono text-fg-subtle">
              tool: {citation.source_tool}
            </div>
          )}
          {citation.finding ? (
            <FindingDetail finding={citation.finding} />
          ) : citation.recommendation ? (
            <RecommendationDetail recommendation={citation.recommendation} />
          ) : (
            <span className="text-fg-subtle">
              Source: {citation.label}
              {citation.fact_ids.length > 0 &&
                ` · facts ${citation.fact_ids.join(", ")}`}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
