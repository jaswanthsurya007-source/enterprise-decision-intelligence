/**
 * Shared MSW request handlers for component tests (B3).
 *
 * These mock the gateway REST snapshots so tests run with NO backend, seeded with
 * the `revenue_drop_emea` demo data (ARCHITECTURE §9). SSE streams (metrics /
 * anomalies / recommendations / copilot chat) are NOT MSW-handled: the SSE client
 * and the copilot hook read bodies via `fetch` + a `ReadableStream`, so tests
 * inject a mocked `fetchImpl` (see `streamResponse` / `sseStreamResponse` below)
 * rather than going through MSW. That keeps the realtime path fully offline and
 * deterministic.
 */
import { http, HttpResponse } from "msw";
import {
  ANOMALIES,
  FORECASTS,
  KPI_TILES,
  RECOMMENDATIONS,
  EMEA_RECOMMENDATION,
} from "../fixtures";

/** Resolve a gateway path against the test base URL. */
export const GATEWAY = "http://localhost:8080";
export const url = (path: string) => `${GATEWAY}${path}`;

/** Demo-seeded REST snapshots for the four gateway GET routes. */
export const handlers = [
  http.get(url("/v1/kpis"), () => HttpResponse.json(KPI_TILES)),
  http.get(url("/v1/anomalies"), () => HttpResponse.json(ANOMALIES)),
  http.get(url("/v1/recommendations"), () =>
    HttpResponse.json(RECOMMENDATIONS),
  ),
  http.get(url("/v1/forecasts"), () => HttpResponse.json(FORECASTS)),
  // Accept/reject write surface — echoes the recommendation with the new status.
  http.post(url("/v1/recommendations/:id/action"), async ({ request }) => {
    const body = (await request.json()) as { action: "accept" | "reject" };
    return HttpResponse.json({
      ...EMEA_RECOMMENDATION,
      status: body.action === "accept" ? "accepted" : "rejected",
    });
  }),
];

/** Empty-state variant (no demo data) for empty/loading-state tests. */
export const emptyHandlers = [
  http.get(url("/v1/kpis"), () => HttpResponse.json([])),
  http.get(url("/v1/anomalies"), () => HttpResponse.json([])),
  http.get(url("/v1/recommendations"), () => HttpResponse.json([])),
  http.get(url("/v1/forecasts"), () => HttpResponse.json([])),
];

/** A handler set that fails the KPI route (for error-state tests). */
export const errorHandlers = [
  http.get(url("/v1/kpis"), () =>
    HttpResponse.json(
      { type: "about:blank", title: "Internal Server Error", status: 500 },
      { status: 500 },
    ),
  ),
];

/** Build a `Response` whose body streams the given chunks then closes (for SSE). */
export function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}
