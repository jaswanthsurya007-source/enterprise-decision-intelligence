/**
 * `@edis/ts-contracts` -- the Zod mirror of the EDIS Pydantic canonical
 * contracts (`libs/edis-contracts`). Every boundary payload the web app and BFF
 * exchange is validated against these schemas (runtime validation at the edge),
 * and CI drift-checks them against the Python JSON-Schema goldens (see
 * `src/__drift__.ts`).
 *
 * Import the `*Schema` for runtime parsing and the inferred `type` for static
 * typing, e.g.:
 *
 *   import { FindingSchema, type Finding } from "@edis/ts-contracts";
 *   const finding: Finding = FindingSchema.parse(payload);
 */
export * from "./common.js";
export * from "./ingest.js";
export * from "./canonical.js";
export * from "./findings.js";
export * from "./decisions.js";
export * from "./governance.js";

import { z } from "zod";
import { IngestEnvelopeSchema } from "./ingest.js";
import { CanonicalEventSchema, MetricPointSchema } from "./canonical.js";
import { FindingSchema, ForecastSchema } from "./findings.js";
import {
  OutcomeReportSchema,
  RecommendationLifecycleEventSchema,
  RecommendationSchema,
} from "./decisions.js";
import { AuditEventSchema, LineageEventSchema } from "./governance.js";

/**
 * Canonical event-topic names. Mirrors `edis_contracts/topics.py` constants;
 * the versioned `.v1` suffix is mandatory.
 */
export const Topics = {
  RAW_SALES: "edis.raw.sales.v1",
  RAW_OPS: "edis.raw.ops.v1",
  RAW_CUSTOMER: "edis.raw.customer.v1",
  CANONICAL_ORDER: "edis.canonical.order.v1",
  CANONICAL_CUSTOMER: "edis.canonical.customer.v1",
  CANONICAL_PRODUCT: "edis.canonical.product.v1",
  METRICS_POINTS: "edis.metrics.points.v1",
  FINDINGS: "edis.findings.v1",
  FORECASTS: "edis.forecasts.v1",
  RECOMMENDATIONS: "edis.decisions.recommendations.v1",
  DECISIONS_LIFECYCLE: "edis.decisions.lifecycle.v1",
  FEEDBACK_OUTCOMES: "edis.feedback.outcomes.v1",
  AUDIT: "edis.governance.audit.v1",
  LINEAGE: "edis.governance.lineage.v1",
  DLQ_INGEST: "edis.dlq.ingest.v1",
  DLQ_INTEGRATION: "edis.dlq.integration.v1",
} as const;

export type TopicName = (typeof Topics)[keyof typeof Topics];

/**
 * Maps every topic carrying a typed payload to its Zod schema -- the TS twin of
 * `edis_contracts.topics.TOPIC_MODEL`. Lets a generic SSE/bus consumer validate
 * any topic's payload by name.
 */
export const TOPIC_SCHEMA: Record<string, z.ZodTypeAny> = {
  [Topics.RAW_SALES]: IngestEnvelopeSchema,
  [Topics.RAW_OPS]: IngestEnvelopeSchema,
  [Topics.RAW_CUSTOMER]: IngestEnvelopeSchema,
  [Topics.CANONICAL_ORDER]: CanonicalEventSchema,
  [Topics.CANONICAL_CUSTOMER]: CanonicalEventSchema,
  [Topics.CANONICAL_PRODUCT]: CanonicalEventSchema,
  [Topics.METRICS_POINTS]: MetricPointSchema,
  [Topics.FINDINGS]: FindingSchema,
  [Topics.FORECASTS]: ForecastSchema,
  [Topics.RECOMMENDATIONS]: RecommendationSchema,
  [Topics.DECISIONS_LIFECYCLE]: RecommendationLifecycleEventSchema,
  [Topics.FEEDBACK_OUTCOMES]: OutcomeReportSchema,
  [Topics.AUDIT]: AuditEventSchema,
  [Topics.LINEAGE]: LineageEventSchema,
};
