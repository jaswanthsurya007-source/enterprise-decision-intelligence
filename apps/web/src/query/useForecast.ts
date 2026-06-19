/**
 * `useForecast` — the forecast snapshots (`GET /v1/forecasts`). There is no live
 * SSE channel for forecasts in the MVP (they regenerate on the L3 schedule), so
 * this is a polled snapshot only; on reconnect the metrics/anomaly channels close
 * any gap, and a periodic refetch keeps the band current.
 *
 * `useForecastFor` narrows the list to a single metric+dimensions series (the
 * Overview draws the EMEA-web revenue divergence band).
 */
import { useQuery } from "@tanstack/react-query";
import { useApiClient } from "../auth/useAuth";
import { getForecasts } from "../api/endpoints";
import { QueryKeys } from "../realtime/events";
import type { Forecast, ForecastList } from "../schemas/forecast";

export function useForecast() {
  const client = useApiClient();
  return useQuery({
    queryKey: QueryKeys.forecasts,
    queryFn: ({ signal }) => getForecasts(client, signal),
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });
}

/** True when every dimension in `want` matches the forecast's dimensions. */
function dimsMatch(
  have: Record<string, string>,
  want: Record<string, string>,
): boolean {
  return Object.entries(want).every(([k, v]) => have[k] === v);
}

/**
 * Pick the most recent forecast for a metric (optionally constrained to a
 * dimension subset). Returns null when none match.
 */
export function selectForecast(
  list: ForecastList | undefined,
  metricKey: string,
  dimensions: Record<string, string> = {},
): Forecast | null {
  if (!list) return null;
  const matches = list
    .filter((f) => f.metric_key === metricKey && dimsMatch(f.dimensions, dimensions))
    .sort(
      (a, b) =>
        new Date(b.generated_at).getTime() - new Date(a.generated_at).getTime(),
    );
  return matches[0] ?? null;
}
