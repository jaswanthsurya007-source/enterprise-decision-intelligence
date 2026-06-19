/**
 * Shared test fixtures — the `revenue_drop_emea` demo data (ARCHITECTURE §9),
 * shaped EXACTLY to the contracts the gateway emits so MSW handlers and component
 * tests exercise the real Zod validation path. All ids are valid UUIDs (the
 * shared `uuid()` schema is `z.string().uuid()`); the human-friendly "f-7a3..." /
 * "r-91c..." labels from the doc are carried in display labels, not ids.
 *
 * Every figure here is a COMPUTED fact (the gateway/L3/L4 own it). The copilot
 * fixtures deliberately embed a FABRICATED number in the narrative prose that does
 * NOT appear in any structured fact/citation — the grounded-answer guarantee test
 * (B3 #5) asserts the UI never surfaces it as an authoritative metric.
 */
import {
  type Finding,
  type Forecast,
  type Recommendation,
  type MetricPoint,
} from "@edis/contracts";
import type { KpiTile } from "../src/schemas/kpi";
import type { CopilotFrame } from "../src/schemas/copilot";

export const TENANT = "acme";

/** Valid-UUID ids standing in for the doc's f-7a3.../r-91c... labels. */
export const FINDING_ID = "7a3f0b2c-0000-4000-8000-000000000001";
export const RECOMMENDATION_ID = "91c0d1e2-0000-4000-8000-000000000002";
export const SOURCE_FINDING_ID = FINDING_ID;

const WINDOW_START = "2026-06-12T00:00:00Z";
const WINDOW_END = "2026-06-18T23:59:59Z";
const CREATED_AT = "2026-06-18T12:00:00Z";

/**
 * `GET /v1/kpis` — the demo tiles. The EMEA revenue tile is `critical` with the
 * -8.3% WoW delta (the alarming tile KpiGrid sorts to the front).
 */
export const KPI_TILES: KpiTile[] = [
  {
    metric_key: "revenue",
    label: "EMEA Revenue",
    dimensions: { region: "EMEA", channel: "web" },
    value: 385000,
    unit: "USD",
    baseline: 420000,
    delta_pct: -8.3,
    delta_window: "WoW",
    status: "critical",
    spark: [
      { ts: "2026-06-16T00:00:00Z", value: 420000 },
      { ts: "2026-06-17T00:00:00Z", value: 400000 },
      { ts: "2026-06-18T00:00:00Z", value: 385000 },
    ],
    as_of: "2026-06-18T12:00:00Z",
  },
  {
    metric_key: "revenue",
    label: "Total Revenue",
    dimensions: { region: "NA", channel: "web" },
    value: 210000,
    unit: "USD",
    baseline: 208000,
    delta_pct: 1.0,
    delta_window: "WoW",
    status: "ok",
    spark: [],
    as_of: "2026-06-18T12:00:00Z",
  },
  {
    metric_key: "error_rate",
    label: "Checkout error rate (EMEA)",
    dimensions: { region: "EMEA", service: "checkout-api" },
    value: 0.09,
    unit: "pct",
    baseline: 0.004,
    delta_pct: 2150,
    delta_window: "WoW",
    status: "warn",
    spark: [],
    as_of: "2026-06-18T12:00:00Z",
  },
];

/**
 * `GET /v1/anomalies` — the EMEA revenue level-shift Finding with its lag-adjusted
 * candidate causes (latency_p95 leading, contribution 71%; error_rate 22%).
 */
export const EMEA_FINDING: Finding = {
  finding_id: FINDING_ID,
  tenant_id: TENANT,
  kind: "level_shift",
  metric_key: "revenue",
  dimensions: { region: "EMEA", channel: "web" },
  window_start: WINDOW_START,
  window_end: WINDOW_END,
  detector: "stl_seasonal",
  detector_version: "1.0",
  observed_value: 61000,
  expected_value: 95000,
  deviation: -34000,
  deviation_pct: -35.8,
  score: 5.8,
  severity: 0.86,
  confidence: 0.91,
  business_impact_input: 0.78,
  candidate_causes: [
    {
      metric_key: "latency_p95",
      dimensions: { region: "EMEA", service: "checkout-api" },
      correlation: 0.94,
      lag_minutes: 120,
      contribution_pct: 71.0,
      direction: "leading",
      observed_delta: 1220.0,
    },
    {
      metric_key: "error_rate",
      dimensions: { region: "EMEA", service: "checkout-api" },
      correlation: 0.89,
      lag_minutes: 120,
      contribution_pct: 22.0,
      direction: "leading",
      observed_delta: 0.086,
    },
  ],
  narrative:
    "EMEA web revenue fell to $61K/day, a 5.8σ level shift driven by a checkout-api availability regression.",
  narrative_model: "claude-opus-4-8",
  evidence_ref: "11111111-0000-4000-8000-000000000003",
  status: "open",
  created_at: CREATED_AT,
  schema_version: 1,
};

export const ANOMALIES: Finding[] = [EMEA_FINDING];

/**
 * `GET /v1/recommendations` — the rank-1 `operational_fix` action with the
 * confidence breakdown (static prior; calibration_n=0) and impact inputs.
 */
export const EMEA_RECOMMENDATION: Recommendation = {
  recommendation_id: RECOMMENDATION_ID,
  tenant_id: TENANT,
  source_finding_id: SOURCE_FINDING_ID,
  playbook_id: "operational_fix",
  playbook_version: "1.0",
  title: "Mitigate checkout-api latency in EMEA (likely deploy regression)",
  action_type: "operational_fix",
  action_params: { service: "checkout-api", region: "EMEA" },
  impact: {
    value: 170000,
    value_low: 120000,
    value_high: 200000,
    unit: "USD",
    direction: "increase",
    horizon_days: 5,
    inputs: { daily_loss: 34000, affected_days_remaining: 5 },
    method: "recovery_flat",
  },
  effort_tier: "s",
  confidence: {
    value: 0.84,
    components: { insight: 0.91, evidence: 0.88, historical_calibration: 0.74 },
    calibration_n: 0,
  },
  priority_score: 0.93,
  priority_rank: 1,
  explanation_summary:
    "Recover EMEA web revenue by rolling back the checkout-api regression.",
  evidence_trail: [
    { type: "finding", id: FINDING_ID },
    { type: "metric", id: "latency_p95:EMEA:checkout-api" },
  ],
  narrative: null,
  status: "proposed",
  expires_at: "2026-06-25T00:00:00Z",
  created_at: CREATED_AT,
  schema_version: 1,
};

export const RECOMMENDATIONS: Recommendation[] = [EMEA_RECOMMENDATION];

/** `GET /v1/forecasts` — the AutoETS EMEA-web revenue band. */
export const EMEA_FORECAST: Forecast = {
  forecast_id: "22222222-0000-4000-8000-000000000004",
  tenant_id: TENANT,
  metric_key: "revenue",
  dimensions: { region: "EMEA", channel: "web" },
  model: "statsforecast.AutoETS",
  horizon_days: 7,
  points: [
    { ts: "2026-06-19T00:00:00Z", yhat: 94000, yhat_lower: 88000, yhat_upper: 100000 },
    { ts: "2026-06-20T00:00:00Z", yhat: 95000, yhat_lower: 89000, yhat_upper: 101000 },
  ],
  generated_at: CREATED_AT,
  schema_version: 1,
};

export const FORECASTS: Forecast[] = [EMEA_FORECAST];

/** A live `edis.metrics.points.v1` frame for the EMEA revenue tile (SSE patch). */
export const EMEA_METRIC_POINT: MetricPoint = {
  tenant_id: TENANT,
  metric_key: "revenue",
  ts: "2026-06-18T13:00:00Z",
  value: 60500,
  dimensions: { region: "EMEA", channel: "web" },
  unit: "USD",
  source: "gateway",
  schema_version: 1,
};

/**
 * The copilot answer frame stream for "Why did revenue drop last week?".
 *
 * GROUNDED-ANSWER TRAP: the narrative prose contains the figure "$999K" which is
 * a FABRICATED number present in NO fact and NO citation. The UI must render the
 * prose verbatim but NEVER surface $999K as an authoritative metric chip. The
 * authoritative figures ($385K total, -8.3% WoW, $61K EMEA, $170K recovery) all
 * arrive as structured `fact`/`citation` frames.
 */
export const COPILOT_FABRICATED_FIGURE = "$999K";

export const COPILOT_FRAMES: CopilotFrame[] = [
  {
    type: "fact",
    fact: {
      id: "fact-revenue-wow",
      label: "Revenue WoW",
      value: 385000,
      unit: "USD",
      display: "-8.3% WoW",
      source_tool: "metric_lookup",
      metric_key: "revenue",
    },
  },
  {
    type: "fact",
    fact: {
      id: "fact-emea-recovery",
      label: "Est. recovery",
      value: 170000,
      unit: "USD",
      display: "~$170K / 5d",
      source_tool: "semantic_search",
      metric_key: "revenue",
    },
  },
  {
    type: "tool",
    step: {
      tool: "metric_lookup",
      args: { metric: "revenue", grain: "weekly" },
      status: "ok",
      summary: "revenue -8.3% WoW",
    },
  },
  {
    type: "citation",
    citation: {
      index: 1,
      label: "metric revenue weekly",
      source_tool: "metric_lookup",
      fact_ids: ["fact-revenue-wow"],
    },
  },
  {
    type: "citation",
    citation: {
      index: 2,
      label: "Finding EMEA level-shift",
      source_tool: "find_anomalies",
      finding: EMEA_FINDING,
      fact_ids: [],
    },
  },
  {
    type: "citation",
    citation: {
      index: 4,
      label: "Recommendation #1",
      source_tool: "semantic_search",
      recommendation: EMEA_RECOMMENDATION,
      fact_ids: ["fact-emea-recovery"],
    },
  },
  // Narrative tokens — note the FABRICATED $999K that appears nowhere structured.
  {
    type: "token",
    text: "Revenue fell 8.3% week-over-week ($420K -> $385K daily average). [1] ",
  },
  {
    type: "token",
    text: "The drop is concentrated in EMEA web revenue, which fell to $61K/day. [2] ",
  },
  {
    type: "token",
    text: "An unrelated aside mentions a bogus $999K figure not in any fact. ",
  },
  {
    type: "token",
    text: "Recommended action: mitigate checkout-api latency in EMEA. [4]",
  },
  { type: "usage", usage: { input_tokens: 5000, output_tokens: 400, model: "claude-opus-4-8" } },
  { type: "done", stop_reason: "end_turn", grounding_passed: true },
];

/** Encode frames as an SSE event-stream body string (one `data:` block each). */
export function encodeSseFrames(frames: CopilotFrame[]): string {
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("");
}
