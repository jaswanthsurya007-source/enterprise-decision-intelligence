/**
 * Anomaly schemas. The gateway's anomaly feed is exactly the shared `Finding`
 * payload (`edis.findings.v1`), so we re-export `FindingSchema` rather than
 * duplicate it. `GET /v1/anomalies` returns an array of findings; the SSE
 * `/v1/stream/anomalies` pushes one `Finding` per frame.
 */
import { z } from "zod";
import {
  FindingSchema,
  type Finding,
  CandidateCauseSchema,
  type CandidateCause,
} from "@edis/contracts";

export { FindingSchema, CandidateCauseSchema };
export type { Finding, CandidateCause };

/** Domain-friendly alias used throughout the anomaly feature. */
export const AnomalySchema = FindingSchema;
export type Anomaly = Finding;

/** `GET /v1/anomalies` response. */
export const AnomalyListSchema = z.array(FindingSchema);
export type AnomalyList = z.infer<typeof AnomalyListSchema>;
