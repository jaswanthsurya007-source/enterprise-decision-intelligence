/**
 * ExplainabilityAccordion — the audit/evidence trail for a recommendation. Shows
 * the confidence component breakdown, the impact estimate inputs (the auditable
 * facts the estimator used), and the linked evidence trail entries.
 *
 * Everything is rendered from structured `Recommendation` fields (impact.inputs,
 * confidence.components, evidence_trail) — the optional `narrative` is prose only.
 */
import { useState } from "react";
import { formatMetric } from "../../lib/format";
import type { Recommendation } from "../../schemas/recommendation";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-2xs text-fg-subtle">{label}</span>
      <span className="kpi-figure text-xs text-fg-muted">{value}</span>
    </div>
  );
}

/** Render an evidence_trail entry compactly (it is an open dict per the contract). */
function trailLabel(entry: Record<string, unknown>): string {
  const type = typeof entry.type === "string" ? entry.type : "ref";
  const id =
    typeof entry.id === "string"
      ? entry.id
      : typeof entry.ref === "string"
        ? entry.ref
        : "";
  return id ? `${type}: ${id}` : type;
}

export interface ExplainabilityAccordionProps {
  recommendation: Recommendation;
}

export function ExplainabilityAccordion({
  recommendation: rec,
}: ExplainabilityAccordionProps) {
  const [open, setOpen] = useState(false);
  const components = Object.entries(rec.confidence.components);
  const inputs = Object.entries(rec.impact.inputs);

  return (
    <div className="rounded-md border border-border-subtle">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="focus-ring flex w-full items-center justify-between px-3 py-2 text-left"
        data-testid="explainability-toggle"
      >
        <span className="text-xs font-medium text-fg-muted">
          Why this recommendation?
        </span>
        <span className="text-2xs text-fg-subtle">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div
          className="space-y-3 border-t border-border-subtle p-3"
          data-testid="explainability-body"
        >
          <div>
            <h5 className="mb-1 text-2xs uppercase tracking-wide text-fg-subtle">
              Confidence breakdown
            </h5>
            {components.length === 0 ? (
              <p className="text-2xs text-fg-subtle">No components reported.</p>
            ) : (
              components.map(([k, v]) => (
                <Row key={k} label={k.replace(/_/g, " ")} value={v.toFixed(2)} />
              ))
            )}
            <Row
              label="calibration n"
              value={String(rec.confidence.calibration_n)}
            />
          </div>

          <div>
            <h5 className="mb-1 text-2xs uppercase tracking-wide text-fg-subtle">
              Impact inputs ({rec.impact.method})
            </h5>
            {inputs.length === 0 ? (
              <p className="text-2xs text-fg-subtle">No inputs recorded.</p>
            ) : (
              inputs.map(([k, v]) => (
                <Row key={k} label={k.replace(/_/g, " ")} value={formatMetric(v)} />
              ))
            )}
          </div>

          {rec.evidence_trail.length > 0 && (
            <div>
              <h5 className="mb-1 text-2xs uppercase tracking-wide text-fg-subtle">
                Evidence trail
              </h5>
              <ul className="space-y-1">
                {rec.evidence_trail.map((entry, i) => (
                  <li
                    key={i}
                    className="truncate font-mono text-2xs text-fg-muted"
                  >
                    {trailLabel(entry)}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {rec.narrative && (
            <div>
              <h5 className="mb-1 text-2xs uppercase tracking-wide text-fg-subtle">
                Narrative
              </h5>
              {/* Prose only — never a numeric authority. */}
              <p className="text-2xs leading-relaxed text-fg-muted">
                {rec.narrative}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
