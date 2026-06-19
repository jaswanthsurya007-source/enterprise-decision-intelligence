/**
 * KpiCard — one live metric tile. Renders the server-computed `value`, the
 * week-over-week `delta_pct`, and a sparkline. The figure uses a tabular/mono
 * treatment; the tile border + delta turn red when the gateway marks the tile
 * `critical` (the EMEA revenue tile in the demo).
 *
 * All numbers are authoritative (computed upstream); the card never recomputes.
 */
import { Sparkline } from "./Sparkline";
import { formatMetric, formatDeltaPct } from "../../lib/format";
import { formatAge } from "../../lib/time";
import type { KpiStatus, KpiTile } from "../../schemas/kpi";

const STATUS_RING: Record<KpiStatus, string> = {
  ok: "border-border-subtle",
  warn: "border-status-warn/50",
  critical: "border-status-critical/60",
  unknown: "border-border-subtle",
};

const STATUS_DOT: Record<KpiStatus, string> = {
  ok: "bg-status-ok",
  warn: "bg-status-warn",
  critical: "bg-status-critical",
  unknown: "bg-fg-subtle",
};

const STATUS_SPARK: Record<KpiStatus, string> = {
  ok: "text-accent",
  warn: "text-status-warn",
  critical: "text-status-critical",
  unknown: "text-fg-subtle",
};

/** Delta color: negative deltas on a critical tile are the alarming case. */
function deltaTone(status: KpiStatus, delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return "text-fg-subtle";
  if (status === "critical") return "text-status-critical";
  if (delta > 0) return "text-status-ok";
  if (delta < 0) return "text-status-warn";
  return "text-fg-muted";
}

function dimLabel(dimensions: Record<string, string>): string | null {
  const entries = Object.entries(dimensions);
  if (entries.length === 0) return null;
  return entries.map(([, v]) => v).join(" · ");
}

export interface KpiCardProps {
  tile: KpiTile;
}

export function KpiCard({ tile }: KpiCardProps) {
  const status = tile.status;
  const dims = dimLabel(tile.dimensions);
  const label = tile.label ?? tile.metric_key;

  return (
    <div
      className={`card card-pad flex flex-col gap-3 border ${STATUS_RING[status]}`}
      data-testid="kpi-card"
      data-metric={tile.metric_key}
      data-status={status}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${STATUS_DOT[status]}`}
              aria-hidden
            />
            <span className="truncate text-xs font-medium text-fg-muted">
              {label}
            </span>
          </div>
          {dims && (
            <span className="mt-0.5 block truncate text-2xs text-fg-subtle">
              {dims}
            </span>
          )}
        </div>
        <span className="shrink-0 text-2xs text-fg-subtle" title={tile.as_of}>
          {formatAge(tile.as_of)}
        </span>
      </div>

      <div className="flex items-end justify-between gap-2">
        <div className="kpi-figure text-2xl text-fg-default">
          {formatMetric(tile.value, tile.unit)}
        </div>
        <div className={STATUS_SPARK[status]}>
          <Sparkline points={tile.spark} />
        </div>
      </div>

      <div className="flex items-center justify-between text-2xs">
        <span className={`kpi-figure ${deltaTone(status, tile.delta_pct)}`}>
          {formatDeltaPct(tile.delta_pct)}
          {tile.delta_window ? (
            <span className="ml-1 text-fg-subtle">{tile.delta_window}</span>
          ) : null}
        </span>
        {tile.baseline !== null && tile.baseline !== undefined ? (
          <span className="text-fg-subtle">
            base{" "}
            <span className="kpi-figure text-fg-muted">
              {formatMetric(tile.baseline, tile.unit)}
            </span>
          </span>
        ) : null}
      </div>
    </div>
  );
}
