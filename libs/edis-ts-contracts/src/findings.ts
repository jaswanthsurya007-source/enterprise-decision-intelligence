/**
 * Zod mirror of `edis_contracts/findings.py` (intelligence-layer contracts).
 *
 * Every figure on a `Finding` is computed by a detector; the LLM never overrides
 * it. `narrative` is null until grounded narration succeeds.
 */
import { z } from "zod";
import { datetime, schemaVersion, strMap, uuid } from "./common.js";

/** Mirror of `FindingKind(str, Enum)`. */
export const FindingKindSchema = z.enum([
  "point_anomaly",
  "seasonal_break",
  "level_shift",
  "trend_break",
  "forecast_breach",
  "root_cause",
]);
export type FindingKind = z.infer<typeof FindingKindSchema>;

/** Mirror of `CandidateCause` (lag-adjusted RCA correlate). */
export const CandidateCauseSchema = z.object({
  metric_key: z.string(),
  dimensions: strMap().default({}),
  correlation: z.number(),
  lag_minutes: z.number().int(),
  contribution_pct: z.number().nullish(),
  direction: z.enum(["leading", "coincident", "lagging"]),
  observed_delta: z.number(),
});
export type CandidateCause = z.infer<typeof CandidateCauseSchema>;

/** Mirror of `EvidenceItem` (one computed fact the narrator may cite). */
export const EvidenceItemSchema = z.object({
  kind: z.string(),
  metric_key: z.string().nullish(),
  dimensions: strMap().default({}),
  summary: z.string(),
  values: z.record(z.string(), z.number()).default({}),
  ref: z.record(z.string(), z.unknown()).nullish(),
});
export type EvidenceItem = z.infer<typeof EvidenceItemSchema>;

/** Mirror of `EvidenceBundle` -- the only thing the LLM may reason over. */
export const EvidenceBundleSchema = z.object({
  bundle_id: uuid(),
  tenant_id: z.string(),
  finding_id: uuid(),
  created_at: datetime(),
  items: z.array(EvidenceItemSchema).default([]),
  allowed_numbers: z.array(z.number()).default([]),
  schema_version: schemaVersion(),
});
export type EvidenceBundle = z.infer<typeof EvidenceBundleSchema>;

/** Mirror of `Finding` -- payload of `edis.findings.v1`. */
export const FindingSchema = z.object({
  finding_id: uuid(),
  tenant_id: z.string(),
  kind: FindingKindSchema,
  metric_key: z.string(),
  dimensions: strMap().default({}),
  window_start: datetime(),
  window_end: datetime(),
  detector: z.string(),
  detector_version: z.string(),
  observed_value: z.number(),
  expected_value: z.number(),
  deviation: z.number(),
  deviation_pct: z.number(),
  score: z.number(),
  severity: z.number(),
  confidence: z.number(),
  business_impact_input: z.number(),
  candidate_causes: z.array(CandidateCauseSchema).default([]),
  narrative: z.string().nullish(),
  narrative_model: z.string().nullish(),
  evidence_ref: uuid().nullish(),
  status: z
    .enum(["open", "acknowledged", "resolved", "expired"])
    .default("open"),
  created_at: datetime(),
  schema_version: schemaVersion(),
});
export type Finding = z.infer<typeof FindingSchema>;

/** Mirror of `Forecast` -- payload of `edis.forecasts.v1`. */
export const ForecastSchema = z.object({
  forecast_id: uuid(),
  tenant_id: z.string(),
  metric_key: z.string(),
  dimensions: strMap().default({}),
  model: z.string(),
  horizon_days: z.number().int(),
  points: z.array(z.record(z.string(), z.unknown())).default([]),
  generated_at: datetime(),
  schema_version: schemaVersion(),
});
export type Forecast = z.infer<typeof ForecastSchema>;
