/**
 * KPI schemas. Thin derivations over the shared `@edis/contracts` `MetricPoint`
 * (the payload of `edis.metrics.points.v1`, which the gateway bridges to the
 * browser over SSE). Re-exported here so feature code imports from one place and
 * the shared schema stays the single source of truth.
 *
 * The gateway's `GET /v1/kpis` REST snapshot returns a small rollup per metric
 * (latest value + a WoW delta + a sparkline window) computed server-side — the
 * UI never computes a KPI from raw points. That snapshot shape is defined here as
 * `KpiTile`; its live points reuse `MetricPointSchema` verbatim.
 */
import { z } from "zod";
import { MetricPointSchema, type MetricPoint } from "@edis/contracts";

export { MetricPointSchema };
export type { MetricPoint };

/** Status the gateway derives for a tile (drives the semantic color). */
export const KpiStatusSchema = z.enum(["ok", "warn", "critical", "unknown"]);
export type KpiStatus = z.infer<typeof KpiStatusSchema>;

/** A single sparkline sample (server-rolled-up; UI does not aggregate). */
export const KpiSparkPointSchema = z.object({
  ts: z.string().datetime({ offset: true }),
  value: z.number(),
});
export type KpiSparkPoint = z.infer<typeof KpiSparkPointSchema>;

/**
 * `GET /v1/kpis` row — one live tile. All figures (`value`, `delta_pct`,
 * `baseline`) are computed by the gateway/decision layers; the UI renders them
 * as authoritative without recomputation.
 */
export const KpiTileSchema = z.object({
  metric_key: z.string(),
  label: z.string().nullish(),
  dimensions: z.record(z.string(), z.string()).default({}),
  value: z.number(),
  unit: z.string().nullish(),
  baseline: z.number().nullish(),
  delta_pct: z.number().nullish(),
  delta_window: z.string().nullish(),
  status: KpiStatusSchema.default("unknown"),
  spark: z.array(KpiSparkPointSchema).default([]),
  as_of: z.string().datetime({ offset: true }),
});
export type KpiTile = z.infer<typeof KpiTileSchema>;

/** `GET /v1/kpis` response — an array of tiles. */
export const KpiListSchema = z.array(KpiTileSchema);
export type KpiList = z.infer<typeof KpiListSchema>;
