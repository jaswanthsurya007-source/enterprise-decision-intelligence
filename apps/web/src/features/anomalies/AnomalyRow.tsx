/**
 * AnomalyRow — one entry in the anomaly feed. Compact, scannable: severity dot,
 * metric + dimensions, kind, deviation, and age. Clicking selects it for the
 * RootCausePanel drill-in.
 */
import { formatDeltaPct } from "../../lib/format";
import { formatAge } from "../../lib/time";
import type { Anomaly } from "../../schemas/anomaly";

/** Map normalized severity (0..1) to a semantic dot tone. */
function severityTone(severity: number): string {
  if (severity >= 0.66) return "bg-status-critical";
  if (severity >= 0.33) return "bg-status-warn";
  return "bg-status-ok";
}

function dimsLabel(d: Record<string, string>): string {
  return Object.values(d).join(" · ");
}

export interface AnomalyRowProps {
  anomaly: Anomaly;
  selected: boolean;
  onSelect: (anomaly: Anomaly) => void;
}

export function AnomalyRow({ anomaly, selected, onSelect }: AnomalyRowProps) {
  const devTone =
    anomaly.deviation_pct < 0 ? "text-status-critical" : "text-status-ok";

  return (
    <button
      type="button"
      onClick={() => onSelect(anomaly)}
      aria-pressed={selected}
      data-testid="anomaly-row"
      className={[
        "focus-ring flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
        selected
          ? "border-accent/50 bg-accent-muted/20"
          : "border-transparent hover:border-border-subtle hover:bg-surface-overlay",
      ].join(" ")}
    >
      <span
        className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${severityTone(anomaly.severity)}`}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-xs font-medium text-fg-default">
            {anomaly.metric_key}
          </span>
          <span className="shrink-0 text-2xs uppercase tracking-wide text-fg-subtle">
            {anomaly.kind.replace(/_/g, " ")}
          </span>
        </div>
        <span className="block truncate text-2xs text-fg-subtle">
          {dimsLabel(anomaly.dimensions)}
        </span>
      </div>
      <div className="flex shrink-0 flex-col items-end">
        <span className={`kpi-figure text-xs ${devTone}`}>
          {formatDeltaPct(anomaly.deviation_pct)}
        </span>
        <span className="text-2xs text-fg-subtle">
          {formatAge(anomaly.created_at)}
        </span>
      </div>
    </button>
  );
}
