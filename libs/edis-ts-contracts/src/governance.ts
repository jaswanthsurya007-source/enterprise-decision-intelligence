/**
 * Zod mirror of `edis_contracts/governance.py` (audit + explainability),
 * `LineageEvent` from `edis_contracts/events.py`, and the authz boundary types
 * from `edis_contracts/security.py`.
 *
 * `LineageEvent` lives here (not in canonical) because it is the payload of
 * `edis.governance.lineage.v1`. The security types are the shapes the BFF derives
 * from a verified JWT and attaches to every request.
 */
import { z } from "zod";
import { anyMap, datetime, schemaVersion, uuid } from "./common.js";

/** Mirror of `LineageEvent` -- payload of `edis.governance.lineage.v1`. */
export const LineageEventSchema = z.object({
  lineage_id: uuid(),
  tenant_id: z.string(),
  run_id: uuid(),
  inputs: z.array(z.record(z.string(), z.unknown())).default([]),
  outputs: z.array(z.record(z.string(), z.unknown())).default([]),
  stage: z.string(),
  occurred_at: datetime(),
  schema_version: schemaVersion(),
});
export type LineageEvent = z.infer<typeof LineageEventSchema>;

/** Mirror of `AuditEvent` -- payload of `edis.governance.audit.v1`. */
export const AuditEventSchema = z.object({
  audit_id: uuid(),
  occurred_at: datetime(),
  tenant_id: z.string(),
  actor: anyMap().default({}),
  action: z.enum([
    "DATA_READ",
    "DATA_WRITE",
    "AI_DECISION",
    "AI_QUERY",
    "AUTH_DENY",
    "RBAC_CHANGE",
    "EXPORT",
  ]),
  resource: anyMap().default({}),
  outcome: z.enum(["ALLOW", "DENY", "ERROR"]),
  reason: z.string().nullish(),
  decision_id: uuid().nullish(),
  trace_id: z.string().nullish(),
  schema_version: schemaVersion(),
});
export type AuditEvent = z.infer<typeof AuditEventSchema>;

/** Mirror of `Evidence` (immutable value snapshot + live pointer). */
export const EvidenceSchema = z.object({
  evidence_id: uuid(),
  kind: z.string(),
  summary: z.string(),
  snapshot: anyMap().default({}),
  ref: z.record(z.string(), z.unknown()).nullish(),
  schema_version: schemaVersion(),
});
export type Evidence = z.infer<typeof EvidenceSchema>;

/** Mirror of `Decision` -- explainability record linking an AI decision to evidence. */
export const DecisionSchema = z.object({
  decision_id: uuid(),
  tenant_id: z.string(),
  decision_type: z.enum([
    "finding_narrative",
    "recommendation",
    "copilot_answer",
  ]),
  subject_id: uuid(),
  actor: anyMap().default({}),
  rationale: z.string(),
  evidence: z.array(EvidenceSchema).default([]),
  created_at: datetime(),
  schema_version: schemaVersion(),
});
export type Decision = z.infer<typeof DecisionSchema>;

// --- security.py: authz boundary types ---

/** Mirror of `Role = Literal[...]`. */
export const RoleSchema = z.enum([
  "viewer",
  "analyst",
  "operator",
  "auditor",
  "admin",
]);
export type Role = z.infer<typeof RoleSchema>;

/** Mirror of `Actor`. */
export const ActorSchema = z.object({
  type: z.enum(["user", "service", "system", "copilot"]),
  id: z.string(),
  roles: z.array(z.string()).default([]),
});
export type Actor = z.infer<typeof ActorSchema>;

/** Mirror of `ResourceRef`. */
export const ResourceRefSchema = z.object({
  type: z.string(),
  id: z.string().nullish(),
  columns: z.array(z.string()).nullish(),
});
export type ResourceRef = z.infer<typeof ResourceRefSchema>;

/** Mirror of `SecurityContext` -- the authenticated principal from the JWT. */
export const SecurityContextSchema = z.object({
  tenant_id: z.string(),
  user_id: z.string(),
  roles: z.array(z.string()).default([]),
  scopes: z.array(z.string()).default([]),
  token_id: z.string().nullish(),
});
export type SecurityContext = z.infer<typeof SecurityContextSchema>;
