/**
 * Zod mirror of `edis_contracts/ingest.py` (L1 -> L2 ingestion contracts).
 *
 * The {@link IngestEnvelopeSchema} is the stable boundary between untrusted
 * source reality and the platform. Per-domain payloads use `.strict()` to mirror
 * Pydantic's `extra="forbid"`.
 */
import { z } from "zod";
import { anyMap, datetime, schemaVersion, strMap, uuid } from "./common.js";

/** Pydantic `Domain = Literal["sales", "ops", "customer"]`. */
export const DomainSchema = z.enum(["sales", "ops", "customer"]);
export type Domain = z.infer<typeof DomainSchema>;

/** Mirror of `SalesPayloadV1` (`extra="forbid"` -> `.strict()`). */
export const SalesPayloadV1Schema = z
  .object({
    order_id: z.string(),
    customer_id: z.string(),
    sku: z.string(),
    qty: z.number().int(),
    unit_price: z.number(),
    currency: z.string().default("USD"),
    region: z.string().nullish(),
    channel: z.string().nullish(),
    order_ts: datetime(),
  })
  .strict();
export type SalesPayloadV1 = z.infer<typeof SalesPayloadV1Schema>;

/** Mirror of `OpsPayloadV1`. */
export const OpsPayloadV1Schema = z
  .object({
    service: z.string(),
    region: z.string().nullish(),
    level: z.enum(["info", "warn", "error"]).default("info"),
    status_code: z.number().int().nullish(),
    latency_ms: z.number().nullish(),
    message: z.string().nullish(),
    event_ts: datetime(),
  })
  .strict();
export type OpsPayloadV1 = z.infer<typeof OpsPayloadV1Schema>;

/** Mirror of `CustomerPayloadV1`. */
export const CustomerPayloadV1Schema = z
  .object({
    customer_id: z.string().nullish(),
    session_id: z.string(),
    event: z.string(),
    region: z.string().nullish(),
    channel: z.string().nullish(),
    props: strMap().default({}),
    event_ts: datetime(),
  })
  .strict();
export type CustomerPayloadV1 = z.infer<typeof CustomerPayloadV1Schema>;

/**
 * Mirror of `IngestEnvelope` -- the one envelope every record entering the bus is
 * wrapped in. Pydantic config is `frozen=True, extra="forbid"`; on the TS side we
 * mark the inferred type `Readonly` via `.strict()` + consumers treating it as
 * immutable.
 */
export const IngestEnvelopeSchema = z
  .object({
    event_id: uuid(),
    idempotency_key: z.string(),
    schema_ref: z.string(),
    domain: DomainSchema,
    tenant_id: z.string(),
    source_system: z.string(),
    ingest_ts: datetime(),
    event_ts: datetime(),
    producer: z.string().default("ingestion"),
    trace_context: strMap().default({}),
    is_synthetic: z.boolean().default(true),
    anomaly_label: z.string().nullish(),
    payload: anyMap(),
    schema_version: schemaVersion(),
  })
  .strict();
export type IngestEnvelope = z.infer<typeof IngestEnvelopeSchema>;

/** Mirror of `DLQRecord` (dead-letter at the ingestion edge). */
export const DLQRecordSchema = z
  .object({
    dlq_id: uuid(),
    tenant_id: z.string().nullish(),
    stage: z.enum(["ingest", "integration"]).default("ingest"),
    domain: DomainSchema.nullish(),
    source_system: z.string().nullish(),
    raw: z.unknown().nullable().default(null),
    error_type: z.string(),
    error_detail: z.string(),
    occurred_at: datetime(),
    trace_id: z.string().nullish(),
    schema_version: schemaVersion(),
  })
  .strict();
export type DLQRecord = z.infer<typeof DLQRecordSchema>;

/** Mirror of `QuarantinedRecord` (integration-layer DQ failure). */
export const QuarantinedRecordSchema = z
  .object({
    quarantine_id: uuid(),
    tenant_id: z.string(),
    stage: z.literal("integration").default("integration"),
    reason: z.string(),
    dq_failures: z.array(z.string()).default([]),
    raw: z.unknown().nullable().default(null),
    occurred_at: datetime(),
    schema_version: schemaVersion(),
  })
  .strict();
export type QuarantinedRecord = z.infer<typeof QuarantinedRecordSchema>;
