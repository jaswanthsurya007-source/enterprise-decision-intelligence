/**
 * ForecastChart — actual series vs the AutoETS prediction band (§5.3/§9). Draws
 * the prediction interval as a shaded area (yhat_lower..yhat_upper), the central
 * `yhat` as a dashed line, and the actual observed values as a solid line. Where
 * actual diverges below the band, the divergence is visible (the EMEA-web revenue
 * story).
 *
 * Forecast band points are validated via `parseForecastPoints` (the strict
 * `{ts, yhat, yhat_lower, yhat_upper}` shape) so a malformed point can't reach
 * Recharts. Actuals come from the KPI sparkline window (server-rolled-up).
 */
import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { parseForecastPoints, type Forecast } from "../../schemas/forecast";
import type { KpiSparkPoint } from "../../schemas/kpi";
import { formatMetric } from "../../lib/format";
import { formatDateTime } from "../../lib/time";

interface ChartRow {
  ts: string;
  actual: number | null;
  yhat: number | null;
  /** Recharts stacked-area trick: lower offset (invisible) + band height. */
  bandLow: number | null;
  bandHeight: number | null;
}

export interface ForecastChartProps {
  forecast: Forecast | null;
  /** Actual observed series (server-rolled-up KPI sparkline window). */
  actuals?: KpiSparkPoint[];
  unit?: string | null;
  title?: string;
  height?: number;
}

function buildRows(
  forecast: Forecast | null,
  actuals: KpiSparkPoint[],
): ChartRow[] {
  const byTs = new Map<string, ChartRow>();

  const ensure = (ts: string): ChartRow => {
    let row = byTs.get(ts);
    if (!row) {
      row = { ts, actual: null, yhat: null, bandLow: null, bandHeight: null };
      byTs.set(ts, row);
    }
    return row;
  };

  for (const a of actuals) ensure(a.ts).actual = a.value;

  if (forecast) {
    for (const p of parseForecastPoints(forecast)) {
      const row = ensure(p.ts);
      row.yhat = p.yhat;
      row.bandLow = p.yhat_lower;
      row.bandHeight = Math.max(0, p.yhat_upper - p.yhat_lower);
    }
  }

  return [...byTs.values()].sort(
    (x, y) => new Date(x.ts).getTime() - new Date(y.ts).getTime(),
  );
}

export function ForecastChart({
  forecast,
  actuals = [],
  unit,
  title = "Forecast vs actual",
  height = 240,
}: ForecastChartProps) {
  const resolvedUnit = unit ?? forecast?.dimensions?.unit ?? null;
  const rows = useMemo(
    () => buildRows(forecast, actuals),
    [forecast, actuals],
  );

  const hasData = rows.length > 0;

  return (
    <div className="card card-pad" data-testid="forecast-chart">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-fg-default">{title}</h2>
        {forecast && (
          <span className="text-2xs text-fg-subtle">
            {forecast.model} · {forecast.horizon_days}d horizon
          </span>
        )}
      </div>

      {!hasData ? (
        <div className="flex h-[180px] items-center justify-center text-xs text-fg-subtle">
          No forecast available yet.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="rgb(var(--border-subtle))" vertical={false} />
            <XAxis
              dataKey="ts"
              tickFormatter={(v: string) => formatDateTime(v)}
              tick={{ fill: "rgb(var(--fg-subtle))", fontSize: 10 }}
              stroke="rgb(var(--border-strong))"
              minTickGap={40}
            />
            <YAxis
              tickFormatter={(v: number) => formatMetric(v, resolvedUnit)}
              tick={{ fill: "rgb(var(--fg-subtle))", fontSize: 10 }}
              stroke="rgb(var(--border-strong))"
              width={56}
            />
            <Tooltip
              contentStyle={{
                background: "rgb(var(--surface-overlay))",
                border: "1px solid rgb(var(--border-strong))",
                borderRadius: 8,
                fontSize: 12,
              }}
              labelStyle={{ color: "rgb(var(--fg-muted))" }}
              labelFormatter={(v) => formatDateTime(String(v))}
              formatter={(value: number | string, name: string) => {
                if (name === "bandLow" || name === "bandHeight") return [];
                const num = typeof value === "number" ? value : Number(value);
                const label = name === "actual" ? "Actual" : "Forecast";
                return [formatMetric(num, resolvedUnit), label];
              }}
            />
            {/* Prediction band: invisible lower offset + visible band height (stacked). */}
            <Area
              type="monotone"
              dataKey="bandLow"
              stackId="band"
              stroke="none"
              fill="transparent"
              isAnimationActive={false}
              connectNulls
            />
            <Area
              type="monotone"
              dataKey="bandHeight"
              stackId="band"
              stroke="none"
              fill="rgb(var(--accent))"
              fillOpacity={0.12}
              isAnimationActive={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="yhat"
              stroke="rgb(var(--accent))"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="actual"
              stroke="rgb(var(--fg-default))"
              strokeWidth={1.75}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
