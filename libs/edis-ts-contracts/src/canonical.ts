/**
 * Zod mirror of `edis_contracts/canonical.py` and the canonical event payloads
 * from `edis_contracts/events.py` (`CanonicalEvent`, `MetricPoint`).
 *
 * MVP keying is deterministic; the SCD-2 columns are present but the MVP always
 * writes `valid_to = null`, `is_current = true`, `version = 1`. Every model
 * carries `tenant_id`. Canonical entity models are NOT `extra="forbid"` in
 * Pydantic, so these objects are non-strict (extra keys tolerated), matching.
 */
import { z } from "zod";
import { datetime, decimal, schemaVersion, strMap, uuid } from "./common.js";

/** Mirror of `SourceRef` -- provenance back-link from a canonical row. */
export const SourceRefSchema = z.object({
  source_system: z.string(),
  source_id: z.string(),
  schema_version: z.number().int(),
  match_confidence: z.number().default(1.0),
});
export type SourceRef = z.infer<typeof SourceRefSchema>;

/** Mirror of `CanonicalCustomer` (SCD-2-shaped dimension; history future). */
export const CanonicalCustomerSchema = z.object({
  canonical_customer_id: uuid(),
  tenant_id: z.string(),
  legal_name: z.string(),
  display_name: z.string(),
  primary_email: z.string().email().nullish(),
  country_iso2: z.string().nullish(),
  industry: z.string().nullish(),
  region: z.string().nullish(),
  valid_from: datetime(),
  valid_to: datetime().nullish(),
  is_current: z.boolean().default(true),
  version: z.number().int().default(1),
  source_refs: z.array(SourceRefSchema),
  dq_score: z.number(),
  record_hash: z.string(),
  created_at: datetime(),
  updated_at: datetime(),
});
export type CanonicalCustomer = z.infer<typeof CanonicalCustomerSchema>;

/** Mirror of `CanonicalProduct`. */
export const CanonicalProductSchema = z.object({
  canonical_product_id: uuid(),
  tenant_id: z.string(),
  sku: z.string(),
  name: z.string(),
  category: z.string().nullish(),
  uom: z.string().nullish(),
  valid_from: datetime(),
  valid_to: datetime().nullish(),
  is_current: z.boolean().default(true),
  version: z.number().int().default(1),
  source_refs: z.array(SourceRefSchema),
  record_hash: z.string(),
});
export type CanonicalProduct = z.infer<typeof CanonicalProductSchema>;

/** Mirror of `CanonicalOrderLine`. */
export const CanonicalOrderLineSchema = z.object({
  canonical_product_id: uuid(),
  sku: z.string(),
  qty: z.number().int(),
  unit_price_base: decimal(),
  line_amount_base: decimal(),
});
export type CanonicalOrderLine = z.infer<typeof CanonicalOrderLineSchema>;

/** Mirror of `CanonicalOrder` (immutable sales fact). */
export const CanonicalOrderSchema = z.object({
  canonical_order_id: uuid(),
  tenant_id: z.string(),
  canonical_customer_id: uuid(),
  order_ts: datetime(),
  currency_base: z.literal("USD").default("USD"),
  amount_base: decimal(),
  amount_src: decimal(),
  currency_src: z.string(),
  fx_rate: decimal(),
  region: z.string().nullish(),
  channel: z.enum(["web", "partner", "direct"]).nullish(),
  line_items: z.array(CanonicalOrderLineSchema),
  source_refs: z.array(SourceRefSchema),
  record_hash: z.string(),
  created_at: datetime(),
});
export type CanonicalOrder = z.infer<typeof CanonicalOrderSchema>;

/** Mirror of `OpsEvent` (immutable operations fact). */
export const OpsEventSchema = z.object({
  canonical_ops_event_id: uuid(),
  tenant_id: z.string(),
  service: z.string(),
  region: z.string().nullish(),
  level: z.enum(["info", "warn", "error"]),
  status_code: z.number().int().nullish(),
  latency_ms: z.number().nullish(),
  message: z.string().nullish(),
  event_ts: datetime(),
  source_refs: z.array(SourceRefSchema),
  record_hash: z.string(),
});
export type OpsEvent = z.infer<typeof OpsEventSchema>;

/** Mirror of `CustomerActivity` (immutable customer-activity fact). */
export const CustomerActivitySchema = z.object({
  canonical_activity_id: uuid(),
  tenant_id: z.string(),
  canonical_customer_id: uuid().nullish(),
  session_id: z.string(),
  event: z.string(),
  region: z.string().nullish(),
  channel: z.enum(["web", "partner", "direct"]).nullish(),
  props: strMap(),
  event_ts: datetime(),
  source_refs: z.array(SourceRefSchema),
  record_hash: z.string(),
});
export type CustomerActivity = z.infer<typeof CustomerActivitySchema>;

/** Mirror of `MetricObservation` (a row in the TimescaleDB hypertable). */
export const MetricObservationSchema = z.object({
  tenant_id: z.string(),
  metric_key: z.string(),
  ts: datetime(),
  dimensions: strMap(),
  value: z.number(),
  unit: z.string().nullish(),
  source_refs: z.array(SourceRefSchema),
});
export type MetricObservation = z.infer<typeof MetricObservationSchema>;

// --- Canonical event payloads (events.py) ---

/** Mirror of `MetricPoint` -- payload of `edis.metrics.points.v1`. */
export const MetricPointSchema = z.object({
  tenant_id: z.string(),
  metric_key: z.string(),
  ts: datetime(),
  value: z.number(),
  dimensions: strMap().default({}),
  unit: z.string().nullish(),
  source: z.string(),
  schema_version: schemaVersion(),
});
export type MetricPoint = z.infer<typeof MetricPointSchema>;

/** Mirror of `CanonicalEvent` -- payload of `edis.canonical.<entity>.v1`. */
export const CanonicalEventSchema = z.object({
  event_id: uuid(),
  tenant_id: z.string(),
  entity: z.enum(["customer", "product", "order"]),
  op: z.enum(["created", "updated", "corrected"]),
  occurred_at: datetime(),
  emitted_at: datetime(),
  canonical_id: uuid(),
  before: z.record(z.string(), z.unknown()).nullish(),
  after: z.record(z.string(), z.unknown()).nullish(),
  lineage_run_id: uuid().nullish(),
  is_late: z.boolean().default(false),
  correction_of: uuid().nullish(),
  schema_version: schemaVersion(),
});
export type CanonicalEvent = z.infer<typeof CanonicalEventSchema>;
