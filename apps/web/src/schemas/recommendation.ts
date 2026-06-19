/**
 * Recommendation schemas. Re-exports the shared `Recommendation` payload
 * (`edis.decisions.recommendations.v1`). `GET /v1/recommendations` returns an
 * array; SSE `/v1/stream/recommendations` pushes one per frame.
 *
 * The accept/reject write surface posts a lifecycle transition back through the
 * gateway; the request body shape is defined here (`LifecycleActionSchema`).
 */
import { z } from "zod";
import {
  RecommendationSchema,
  type Recommendation,
  ImpactEstimateSchema,
  type ImpactEstimate,
  ConfidenceScoreSchema,
  type ConfidenceScore,
} from "@edis/contracts";

export { RecommendationSchema, ImpactEstimateSchema, ConfidenceScoreSchema };
export type { Recommendation, ImpactEstimate, ConfidenceScore };

/** `GET /v1/recommendations` response. */
export const RecommendationListSchema = z.array(RecommendationSchema);
export type RecommendationList = z.infer<typeof RecommendationListSchema>;

/** Body for `POST /v1/recommendations/{id}/{accept|reject}` (write surface). */
export const LifecycleActionSchema = z.object({
  action: z.enum(["accept", "reject"]),
  notes: z.string().optional(),
});
export type LifecycleAction = z.infer<typeof LifecycleActionSchema>;
