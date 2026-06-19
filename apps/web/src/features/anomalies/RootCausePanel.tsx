/**
 * RootCausePanel — the drill-in for a selected anomaly. Shows the computed
 * detection facts (observed vs expected, deviation, score) and the ranked RCA
 * `candidate_causes` with correlation, lag, contribution, and direction.
 *
 * Every figure here is computed by L3 (detector + lag-correlation RCA); the
 * narrative, if present, is grounded prose and is shown as such — the panel reads
 * numbers from the structured `Finding` fields, never from `narrative`.
 */
import { formatMetric, formatDeltaPct } from "../../lib/format";
import { formatDateTime } from "../../lib/time";
import type { Anomaly, CandidateCause } from "../../schemas/anomaly";

const DIRECTION_TONE: Record<CandidateCause["direction"], string> = {
  leading: "text-status-critical",
  coincident: "text-status-warn",
  lagging: "text-fg-muted",
};

function dims(d: Record<string, string>): string {
  return Object.entries(d)
    .map(([k, v]) => `${k}=${v}`)
    .join(" · ");
}

function Stat({
  label,
  value,
  tone = "text-fg-default",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs uppercase tracking-wide text-fg-subtle">
        {label}
      </span>
      <span className={`kpi-figure text-sm ${tone}`}>{value}</span>
    </div>
  );
}

function CauseRow({ cause }: { cause: CandidateCause }) {
  return (
    <div className="rounded-md border border-border-subtle bg-surface-inset p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-fg-default">
          {cause.metric_key}
        </span>
        <span
          className={`text-2xs uppercase tracking-wide ${DIRECTION_TONE[cause.direction]}`}
        >
          {cause.direction}
        </span>
      </div>
      {Object.keys(cause.dimensions).length > 0 && (
        <div className="mt-0.5 truncate text-2xs text-fg-subtle">
          {dims(cause.dimensions)}
        </div>
      )}
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Stat label="corr" value={cause.correlation.toFixed(2)} />
        <Stat label="lag" value={`${cause.lag_minutes}m`} />
        <Stat
          label="contrib"
          value={
            cause.contribution_pct === null || cause.contribution_pct === undefined
              ? "—"
              : `${cause.contribution_pct.toFixed(0)}%`
          }
        />
      </div>
    </div>
  );
}

export interface RootCausePanelProps {
  anomaly: Anomaly | null;
}

export function RootCausePanel({ anomaly }: RootCausePanelProps) {
  if (!anomaly) {
    return (
      <div className="card card-pad flex h-full items-center justify-center text-sm text-fg-subtle">
        Select an anomaly to inspect its root-cause analysis.
      </div>
    );
  }

  const devTone =
    anomaly.deviation_pct < 0 ? "text-status-critical" : "text-status-ok";

  return (
    <div className="card card-pad flex h-full flex-col gap-4" data-testid="rca-panel">
      <div>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-fg-default">
            {anomaly.metric_key}
          </h3>
          <span className="rounded border border-border-subtle px-1.5 py-0.5 text-2xs uppercase tracking-wide text-fg-muted">
            {anomaly.kind.replace(/_/g, " ")}
          </span>
        </div>
        <div className="mt-0.5 text-2xs text-fg-subtle">
          {dims(anomaly.dimensions)}
        </div>
        <div className="mt-1 text-2xs text-fg-subtle">
          {formatDateTime(anomaly.window_start)} → {formatDateTime(anomaly.window_end)}
          {" · "}
          {anomaly.detector} v{anomaly.detector_version}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 rounded-md border border-border-subtle bg-surface-inset p-3">
        <Stat label="observed" value={formatMetric(anomaly.observed_value)} />
        <Stat label="expected" value={formatMetric(anomaly.expected_value)} />
        <Stat
          label="deviation"
          value={formatDeltaPct(anomaly.deviation_pct)}
          tone={devTone}
        />
        <Stat label="score (σ)" value={anomaly.score.toFixed(1)} />
      </div>

      <div className="flex-1">
        <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
          Candidate causes (lag-adjusted)
        </h4>
        {anomaly.candidate_causes.length === 0 ? (
          <p className="text-xs text-fg-subtle">No correlated causes identified.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {anomaly.candidate_causes.map((c, i) => (
              <CauseRow key={`${c.metric_key}-${i}`} cause={c} />
            ))}
          </div>
        )}
      </div>

      {anomaly.narrative && (
        <div className="rounded-md border border-border-subtle bg-surface-inset p-3">
          <div className="mb-1 flex items-center gap-1.5 text-2xs uppercase tracking-wide text-fg-subtle">
            <span>Grounded narrative</span>
            {anomaly.narrative_model && (
              <span className="font-mono text-fg-subtle">
                · {anomaly.narrative_model}
              </span>
            )}
          </div>
          {/* Prose only. Numbers above come from structured fields, not this text. */}
          <p className="text-xs leading-relaxed text-fg-muted">
            {anomaly.narrative}
          </p>
        </div>
      )}
    </div>
  );
}
