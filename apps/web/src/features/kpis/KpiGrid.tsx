/**
 * KpiGrid — the live tile grid. Subscribes to the KPI snapshot (patched by
 * realtime metric frames in `useKpis`) and renders loading / error / empty /
 * data states. Critical tiles (the EMEA revenue drop) sort to the front so the
 * alarming KPI is immediately visible.
 */
import { useMemo } from "react";
import { useKpis } from "../../query/useKpis";
import { KpiCard } from "./KpiCard";
import type { KpiList, KpiStatus } from "../../schemas/kpi";

const STATUS_WEIGHT: Record<KpiStatus, number> = {
  critical: 0,
  warn: 1,
  unknown: 2,
  ok: 3,
};

function ordered(tiles: KpiList): KpiList {
  return [...tiles].sort(
    (a, b) => STATUS_WEIGHT[a.status] - STATUS_WEIGHT[b.status],
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="card card-pad h-[112px] animate-pulse"
          aria-hidden
        />
      ))}
    </div>
  );
}

export function KpiGrid() {
  const { data, isLoading, isError, error, refetch } = useKpis();
  const tiles = useMemo(() => (data ? ordered(data) : []), [data]);

  if (isLoading) return <GridSkeleton />;

  if (isError) {
    return (
      <div className="card card-pad text-sm text-status-critical">
        <p>Could not load KPIs.</p>
        <p className="mt-1 text-2xs text-fg-subtle">
          {error instanceof Error ? error.message : "Unknown error."}
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="focus-ring mt-3 rounded-md border border-border-strong px-3 py-1.5 text-xs text-fg-muted hover:bg-surface-overlay hover:text-fg-default"
        >
          Retry
        </button>
      </div>
    );
  }

  if (tiles.length === 0) {
    return (
      <div className="card card-pad text-sm text-fg-muted">
        No KPIs yet. Tiles appear as metrics stream in.
      </div>
    );
  }

  return (
    <div
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
      data-testid="kpi-grid"
    >
      {tiles.map((tile) => (
        <KpiCard
          key={`${tile.metric_key}|${Object.values(tile.dimensions).join(",")}`}
          tile={tile}
        />
      ))}
    </div>
  );
}
