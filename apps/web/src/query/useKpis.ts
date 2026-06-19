/**
 * `useKpis` — the live KPI snapshot, fetched from `GET /v1/kpis` (validated) and
 * patched in place by realtime `metric` frames.
 *
 * The REST snapshot is the source of truth for the derived figures (`value`,
 * `delta_pct`, `baseline`, `status`) — those are computed server-side and the UI
 * never recomputes them (§5.6). A live `MetricPoint` frame only nudges the matching
 * tile's `value`/`as_of` and appends a sparkline sample so the tile feels live
 * between snapshots; the authoritative delta/status refresh on the next snapshot
 * (or on reconnect, when `RealtimeProvider` invalidates the query).
 */
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useApiClient } from "../auth/useAuth";
import { getKpis } from "../api/endpoints";
import { QueryKeys } from "../realtime/events";
import { useSubscription } from "../realtime/useSubscription";
import type { KpiList, KpiTile } from "../schemas/kpi";
import type { MetricPoint } from "../schemas/kpi";

/** Stable identity for a tile: metric_key + its sorted dimensions. */
function tileKey(metric_key: string, dimensions: Record<string, string>): string {
  const dims = Object.keys(dimensions)
    .sort()
    .map((k) => `${k}=${dimensions[k]}`)
    .join(",");
  return `${metric_key}|${dims}`;
}

const SPARK_MAX = 60;

/** Apply a live metric point to the matching tile (immutably). */
function patchTile(tiles: KpiList, point: MetricPoint): KpiList {
  const target = tileKey(point.metric_key, point.dimensions);
  let matched = false;
  const next = tiles.map((tile) => {
    if (tileKey(tile.metric_key, tile.dimensions) !== target) return tile;
    matched = true;
    const spark = [...tile.spark, { ts: point.ts, value: point.value }].slice(
      -SPARK_MAX,
    );
    const updated: KpiTile = {
      ...tile,
      value: point.value,
      unit: point.unit ?? tile.unit,
      as_of: point.ts,
      spark,
    };
    return updated;
  });
  return matched ? next : tiles;
}

export function useKpis() {
  const client = useApiClient();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: QueryKeys.kpis,
    queryFn: ({ signal }) => getKpis(client, signal),
    staleTime: 10_000,
  });

  useSubscription("metrics", (point) => {
    queryClient.setQueryData<KpiList>(QueryKeys.kpis, (prev) =>
      prev ? patchTile(prev, point) : prev,
    );
  });

  // Surface the raw error to the boundary in dev without crashing the grid.
  useEffect(() => {
    if (query.error && import.meta.env.DEV) {
      console.warn("[useKpis] snapshot error", query.error);
    }
  }, [query.error]);

  return query;
}
