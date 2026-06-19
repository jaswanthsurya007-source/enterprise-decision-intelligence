/**
 * Copilot SSE wire schemas.
 *
 * There is no shared `@edis/contracts` schema for the copilot stream frame
 * format (it is an L5/gateway transport concern, not a persisted canonical
 * payload), so it is defined here — the genuine boundary the web app must
 * validate. It deliberately REUSES shared contract pieces where a frame embeds a
 * canonical object (a cited `Finding` / `Recommendation`).
 *
 * GROUNDED-ANSWER GUARANTEE (pin, §5.6 / §9): the assistant narrative
 * (`token` frames) is rendered as PROSE ONLY. Every number the UI presents as an
 * authoritative metric MUST come from the structured `facts` / `citation`
 * frames below (`CopilotFact.value`, the embedded `Finding`/`Recommendation`
 * figures) — NEVER parsed out of the free-text narrative. B3 tests this; do not
 * weaken these schemas to let the UI scrape numbers from `token.text`.
 */
import { z } from "zod";
import {
  FindingSchema,
  RecommendationSchema,
  type Finding,
  type Recommendation,
} from "@edis/contracts";

/**
 * A single grounded fact the model was permitted to cite — surfaced as a
 * "grounded facts" chip. `value` is the ONLY authoritative number source for a
 * scalar metric in the answer; it is computed/retrieved upstream, not generated.
 */
export const CopilotFactSchema = z.object({
  id: z.string(),
  label: z.string(),
  value: z.number().nullish(),
  unit: z.string().nullish(),
  /** Optional pre-formatted display string from the gateway (e.g. "-8.3% WoW"). */
  display: z.string().nullish(),
  source_tool: z.string().nullish(),
  metric_key: z.string().nullish(),
});
export type CopilotFact = z.infer<typeof CopilotFactSchema>;

/**
 * A citation marker `[n]` in the narrative. It points at the structured source —
 * a tool result, a `Finding`, or a `Recommendation` — and may embed the cited
 * object so the UI renders its figures from the validated structured fields.
 */
export const CopilotCitationSchema = z.object({
  index: z.number().int(),
  label: z.string(),
  source_tool: z.string().nullish(),
  finding: FindingSchema.nullish(),
  recommendation: RecommendationSchema.nullish(),
  fact_ids: z.array(z.string()).default([]),
});
export type CopilotCitation = z.infer<typeof CopilotCitationSchema>;

/** One step of the model's tool-use trace (display:"summarized" reasoning). */
export const CopilotToolStepSchema = z.object({
  tool: z.string(),
  args: z.record(z.string(), z.unknown()).default({}),
  status: z.enum(["started", "ok", "error"]).default("started"),
  summary: z.string().nullish(),
});
export type CopilotToolStep = z.infer<typeof CopilotToolStepSchema>;

/** Token-usage / cost accounting frame (informational; never a metric). */
export const CopilotUsageSchema = z.object({
  input_tokens: z.number().int().nullish(),
  output_tokens: z.number().int().nullish(),
  cache_read_input_tokens: z.number().int().nullish(),
  model: z.string().nullish(),
});
export type CopilotUsage = z.infer<typeof CopilotUsageSchema>;

/**
 * Discriminated union over the SSE event `data:` payloads. Each maps to a named
 * SSE event (`event:` line); see `realtime/events.ts` for the channel mapping.
 */
export const CopilotFrameSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("token"), text: z.string() }),
  z.object({ type: z.literal("thinking"), text: z.string() }),
  z.object({ type: z.literal("tool"), step: CopilotToolStepSchema }),
  z.object({ type: z.literal("fact"), fact: CopilotFactSchema }),
  z.object({ type: z.literal("citation"), citation: CopilotCitationSchema }),
  z.object({ type: z.literal("usage"), usage: CopilotUsageSchema }),
  z.object({
    type: z.literal("done"),
    stop_reason: z.string().nullish(),
    grounding_passed: z.boolean().nullish(),
  }),
  z.object({ type: z.literal("error"), message: z.string() }),
]);
export type CopilotFrame = z.infer<typeof CopilotFrameSchema>;

/** Body for `POST /v1/copilot/chat`. */
export const CopilotChatRequestSchema = z.object({
  question: z.string().min(1),
  conversation_id: z.string().nullish(),
});
export type CopilotChatRequest = z.infer<typeof CopilotChatRequestSchema>;

/**
 * Accumulated, render-ready answer assembled from the frame stream. The UI binds
 * its authoritative numbers to `facts` / `citations` only; `narrative` is prose.
 */
export interface CopilotAnswer {
  narrative: string;
  thinking: string;
  tools: CopilotToolStep[];
  facts: CopilotFact[];
  citations: CopilotCitation[];
  usage: CopilotUsage | null;
  status: "streaming" | "done" | "error";
  stopReason: string | null;
  groundingPassed: boolean | null;
  error: string | null;
}

export type { Finding, Recommendation };
