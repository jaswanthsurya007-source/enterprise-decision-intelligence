/**
 * Realtime channel definitions.
 *
 * The gateway exposes one SSE stream per concern (§5.6/§5.8). Each channel binds
 * its gateway path, the Zod schema its `data:` frames validate against (reusing
 * the shared `@edis/contracts` payloads), and the TanStack Query key its
 * snapshot lives under — so on reconnect we invalidate exactly that query to
 * close the gap.
 */
import type { z } from "zod";
import { MetricPointSchema, type MetricPoint } from "../schemas/kpi";
import { FindingSchema, type Finding } from "../schemas/anomaly";
import {
  RecommendationSchema,
  type Recommendation,
} from "../schemas/recommendation";
import { GatewayPaths } from "../api/endpoints";

export type RealtimeChannel = "metrics" | "anomalies" | "recommendations";

/** TanStack Query keys for the REST snapshots each channel patches. */
export const QueryKeys = {
  kpis: ["kpis"] as const,
  anomalies: ["anomalies"] as const,
  recommendations: ["recommendations"] as const,
  forecasts: ["forecasts"] as const,
};

interface ChannelDef<S extends z.ZodTypeAny> {
  path: string;
  schema: S;
  /** Snapshot query key to invalidate on (re)connect. */
  snapshotKey: readonly unknown[];
  /** Default SSE event name the gateway uses for a data frame on this stream. */
  eventName: string;
}

export const CHANNELS: {
  metrics: ChannelDef<typeof MetricPointSchema>;
  anomalies: ChannelDef<typeof FindingSchema>;
  recommendations: ChannelDef<typeof RecommendationSchema>;
} = {
  metrics: {
    path: GatewayPaths.streamMetrics,
    schema: MetricPointSchema,
    snapshotKey: QueryKeys.kpis,
    eventName: "metric",
  },
  anomalies: {
    path: GatewayPaths.streamAnomalies,
    schema: FindingSchema,
    snapshotKey: QueryKeys.anomalies,
    eventName: "anomaly",
  },
  recommendations: {
    path: GatewayPaths.streamRecommendations,
    schema: RecommendationSchema,
    snapshotKey: QueryKeys.recommendations,
    eventName: "recommendation",
  },
};

/** Payload type a subscriber receives for a given channel. */
export interface ChannelPayloadMap {
  metrics: MetricPoint;
  anomalies: Finding;
  recommendations: Recommendation;
}

export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";
