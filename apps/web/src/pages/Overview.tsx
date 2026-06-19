/**
 * Overview — the operations cockpit landing page. Composes the live KpiGrid, the
 * AnomalyFeed (with RCA drill-in), the rank-1 RecommendationCard, and the
 * ForecastChart.
 *
 * Together these tell the `revenue_drop_emea` story (§9): the red EMEA revenue
 * tile (-8.3% WoW), the level-shift anomaly + its candidate causes, the rank-1
 * `operational_fix` recommendation with its 0.84 confidence gauge, and the
 * forecast band the EMEA-web revenue actual diverges below.
 */
import { useMemo } from "react";
import { KpiGrid } from "../features/kpis/KpiGrid";
import { AnomalyFeed } from "../features/anomalies/AnomalyFeed";
import { RecommendationCard } from "../features/recommendations/RecommendationCard";
import { ForecastChart } from "../features/forecast/ForecastChart";
import { useKpis } from "../query/useKpis";
import { useRecommendations } from "../query/useRecommendations";
import { useForecast, selectForecast } from "../query/useForecast";
import type { KpiTile } from "../schemas/kpi";

/** The series the forecast story focuses on (the EMEA-web revenue divergence). */
const FOCUS_METRIC = "revenue";
const FOCUS_DIMS = { region: "EMEA", channel: "web" };

function dimsMatch(have: Record<string, string>, want: Record<string, string>) {
  return Object.entries(want).every(([k, v]) => have[k] === v);
}

function pickFocusTile(tiles: KpiTile[] | undefined): KpiTile | null {
  if (!tiles) return null;
  return (
    tiles.find(
      (t) => t.metric_key === FOCUS_METRIC && dimsMatch(t.dimensions, FOCUS_DIMS),
    ) ??
    tiles.find((t) => t.metric_key === FOCUS_METRIC) ??
    null
  );
}

export function OverviewPage() {
  const { data: recommendations } = useRecommendations();
  const { data: forecasts } = useForecast();
  const { data: kpis } = useKpis();

  // Rank-1 (best/lowest priority_rank). The list is already priority-sorted.
  const topRec = useMemo(() => {
    if (!recommendations || recommendations.length === 0) return null;
    return [...recommendations].sort(
      (a, b) => a.priority_rank - b.priority_rank,
    )[0]!;
  }, [recommendations]);

  const focusTile = useMemo(() => pickFocusTile(kpis), [kpis]);
  const forecast = useMemo(
    () => selectForecast(forecasts, FOCUS_METRIC, FOCUS_DIMS),
    [forecasts],
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold tracking-tight text-fg-default">
          Overview
        </h1>
        <span className="text-2xs text-fg-subtle">live · SSE</span>
      </div>

      <KpiGrid />

      <AnomalyFeed />

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_1fr]">
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-fg-default">
            Recommended action
          </h2>
          {topRec ? (
            <RecommendationCard recommendation={topRec} />
          ) : (
            <div className="card card-pad text-sm text-fg-muted">
              No recommendations yet. The rank-1 action appears when the decision
              engine emits one.
            </div>
          )}
        </div>

        <ForecastChart
          forecast={forecast}
          actuals={focusTile?.spark ?? []}
          unit={focusTile?.unit ?? "USD"}
          title="EMEA web revenue — forecast band"
        />
      </div>
    </div>
  );
}
