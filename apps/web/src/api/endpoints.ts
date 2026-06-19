/**
 * Typed gateway endpoint functions. One function per REST route (§5.8): each
 * binds the path to its Zod response schema so callers get validated, typed
 * data. SSE routes live in `realtime/`; the copilot SSE POST lives in
 * `query/useCopilot` (it streams rather than returning JSON).
 *
 * Paths are centralized in `GatewayPaths` so the SSE client and query hooks
 * reference the same constants.
 */
import type { ApiClient } from "./client";
import { KpiListSchema, type KpiList } from "../schemas/kpi";
import { AnomalyListSchema, type AnomalyList } from "../schemas/anomaly";
import {
  RecommendationListSchema,
  RecommendationSchema,
  type RecommendationList,
  type Recommendation,
} from "../schemas/recommendation";
import { ForecastListSchema, type ForecastList } from "../schemas/forecast";

export const GatewayPaths = {
  kpis: "/v1/kpis",
  anomalies: "/v1/anomalies",
  recommendations: "/v1/recommendations",
  forecasts: "/v1/forecasts",
  copilotChat: "/v1/copilot/chat",
  streamMetrics: "/v1/stream/metrics",
  streamAnomalies: "/v1/stream/anomalies",
  streamRecommendations: "/v1/stream/recommendations",
  recommendationAction: (id: string) => `/v1/recommendations/${id}/action`,
} as const;

export function getKpis(client: ApiClient, signal?: AbortSignal): Promise<KpiList> {
  return client.get(GatewayPaths.kpis, KpiListSchema, { signal });
}

export function getAnomalies(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<AnomalyList> {
  return client.get(GatewayPaths.anomalies, AnomalyListSchema, { signal });
}

export function getRecommendations(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<RecommendationList> {
  return client.get(GatewayPaths.recommendations, RecommendationListSchema, {
    signal,
  });
}

export function getForecasts(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<ForecastList> {
  return client.get(GatewayPaths.forecasts, ForecastListSchema, { signal });
}

/** Accept/reject a recommendation; returns the updated recommendation. */
export function actOnRecommendation(
  client: ApiClient,
  id: string,
  action: "accept" | "reject",
  notes?: string,
): Promise<Recommendation> {
  return client.post(GatewayPaths.recommendationAction(id), RecommendationSchema, {
    action,
    notes,
  });
}
