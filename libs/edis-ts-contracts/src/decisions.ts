/**
 * Zod mirror of `edis_contracts/decisions.py` (decision-layer contracts).
 *
 * All numbers (impact, confidence, priority) come from unit-tested Python code,
 * never the LLM. `ConfidenceScore.calibration_n` is 0 in the MVP (static prior).
 */
import { z } from "zod";
import { anyMap, datetime, floatMap, schemaVersion, uuid } from "./common.js";

/** Mirror of `ImpactEstimate`. */
export const ImpactEstimateSchema = z.object({
  value: z.number(),
  value_low: z.number(),
  value_high: z.number(),
  unit: z.string(),
  direction: z.enum(["increase", "decrease", "mitigate"]),
  horizon_days: z.number().int(),
  inputs: floatMap().default({}),
  method: z.string(),
});
export type ImpactEstimate = z.infer<typeof ImpactEstimateSchema>;

/** Mirror of `ConfidenceScore` (components: insight/evidence/historical_calibration). */
export const ConfidenceScoreSchema = z.object({
  value: z.number(),
  components: floatMap().default({}),
  calibration_n: z.number().int().default(0),
});
export type ConfidenceScore = z.infer<typeof ConfidenceScoreSchema>;

/** Mirror of `Recommendation` -- payload of `edis.decisions.recommendations.v1`. */
export const RecommendationSchema = z.object({
  recommendation_id: uuid(),
  tenant_id: z.string(),
  source_finding_id: uuid(),
  playbook_id: z.string(),
  playbook_version: z.string(),
  title: z.string(),
  action_type: z.enum([
    "operational_fix",
    "pricing_change",
    "inventory_reallocation",
    "customer_outreach",
    "investigate",
    "scale_resource",
    "notify",
  ]),
  action_params: anyMap().default({}),
  impact: ImpactEstimateSchema,
  effort_tier: z.enum(["xs", "s", "m", "l", "xl"]),
  confidence: ConfidenceScoreSchema,
  priority_score: z.number(),
  priority_rank: z.number().int(),
  explanation_summary: z.string(),
  evidence_trail: z.array(z.record(z.string(), z.unknown())).default([]),
  narrative: z.string().nullish(),
  status: z
    .enum([
      "proposed",
      "accepted",
      "rejected",
      "expired",
      "in_progress",
      "outcome_recorded",
    ])
    .default("proposed"),
  expires_at: datetime(),
  created_at: datetime(),
  schema_version: schemaVersion(),
});
export type Recommendation = z.infer<typeof RecommendationSchema>;

/** Mirror of `RecommendationLifecycleEvent` -- payload of `edis.decisions.lifecycle.v1`. */
export const RecommendationLifecycleEventSchema = z.object({
  event_id: uuid(),
  tenant_id: z.string(),
  recommendation_id: uuid(),
  from_status: z.string().nullish(),
  to_status: z.string(),
  actor: anyMap().default({}),
  occurred_at: datetime(),
  schema_version: schemaVersion(),
});
export type RecommendationLifecycleEvent = z.infer<
  typeof RecommendationLifecycleEventSchema
>;

/** Mirror of `OutcomeReport` -- payload of `edis.feedback.outcomes.v1` (seam). */
export const OutcomeReportSchema = z.object({
  outcome_id: uuid(),
  tenant_id: z.string(),
  recommendation_id: uuid(),
  source: z.enum(["human", "system", "copilot"]),
  accepted: z.boolean(),
  realized_value: z.number().nullish(),
  realized_unit: z.string().nullish(),
  notes: z.string().nullish(),
  occurred_at: datetime(),
  schema_version: schemaVersion(),
});
export type OutcomeReport = z.infer<typeof OutcomeReportSchema>;
