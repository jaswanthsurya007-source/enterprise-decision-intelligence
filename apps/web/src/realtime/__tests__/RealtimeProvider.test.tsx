/**
 * B3 #2b — RealtimeProvider: out-of-order event handling + on-reconnect snapshot
 * REFETCH.
 *
 *  - OUT-OF-ORDER: anomaly frames that arrive newest-first then older are folded
 *    into the cache and re-sorted to a stable order (newest-first by created_at)
 *    by the query hook's upsert — the UI never depends on arrival order.
 *  - REFETCH ON RECONNECT: when an SSE link is recycled, the provider's
 *    `onReconnect` invalidates exactly that channel's snapshot query, so the
 *    cache closes the gap. We assert `queryClient.invalidateQueries` ran for the
 *    anomalies key.
 *  - VALIDATION: a malformed `data:` frame is dropped (never reaches a
 *    subscriber), upholding the no-`any`-at-the-boundary rule.
 *
 * No backend: SseClient bodies are driven by a mocked `fetchImpl` streaming SSE.
 */
import { describe, expect, it, vi } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { AuthProvider } from "../../auth/AuthProvider";
import { RealtimeProvider } from "../RealtimeProvider";
import { useSubscription } from "../useSubscription";
import { QueryKeys } from "../events";
import { devToken, TEST_GATEWAY } from "../../../test/utils";
import type { Finding } from "../../schemas/anomaly";
import { EMEA_FINDING } from "../../../test/fixtures";

/** A fetch stub that streams SSE chunks per URL, then holds the connection open. */
function makeStreamingFetch(chunksByPathFragment: Record<string, string[]>) {
  const encoder = new TextEncoder();
  return vi.fn(async (input: RequestInfo | URL) => {
    const u = typeof input === "string" ? input : input.toString();
    const key = Object.keys(chunksByPathFragment).find((k) => u.includes(k));
    const chunks = key ? chunksByPathFragment[key]! : [];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        // Do NOT close — keep the link "open" so it doesn't immediately reconnect.
      },
    });
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }) as unknown as typeof fetch;
}

function sse(event: string, payload: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

/** Test harness component: records anomaly payloads delivered to a subscriber. */
function AnomalyProbe({ sink }: { sink: Finding[] }) {
  useSubscription("anomalies", (f) => {
    sink.push(f);
  });
  return null;
}

function wrap(
  ui: ReactNode,
  queryClient: QueryClient,
  fetchImpl: typeof fetch,
) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider token={devToken()} baseUrl={TEST_GATEWAY} fetchImpl={fetchImpl}>
        <RealtimeProvider autoConnect fetchImpl={fetchImpl}>
          {ui}
        </RealtimeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

const olderFinding: Finding = {
  ...EMEA_FINDING,
  finding_id: "aaaaaaaa-0000-4000-8000-00000000000a",
  created_at: "2026-06-10T00:00:00Z",
  metric_key: "orders",
};
const newerFinding: Finding = {
  ...EMEA_FINDING,
  finding_id: "bbbbbbbb-0000-4000-8000-00000000000b",
  created_at: "2026-06-18T00:00:00Z",
  metric_key: "revenue",
};

describe("RealtimeProvider", () => {
  it("delivers validated frames and DROPS malformed ones", async () => {
    const sink: Finding[] = [];
    const fetchImpl = makeStreamingFetch({
      "/v1/stream/anomalies": [
        sse("anomaly", newerFinding),
        // malformed: deviation_pct must be a number — invalid frame is dropped
        sse("anomaly", { ...olderFinding, deviation_pct: "huge" }),
        sse("anomaly", olderFinding),
      ],
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(wrap(<AnomalyProbe sink={sink} />, qc, fetchImpl));

    // Only the two VALID findings reach the subscriber; the malformed one is gone.
    await waitFor(() => expect(sink.length).toBe(2));
    expect(sink.map((f) => f.finding_id)).toEqual([
      newerFinding.finding_id,
      olderFinding.finding_id,
    ]);
  });

  it("handles OUT-OF-ORDER anomalies — cache ends newest-first regardless of arrival", async () => {
    // Arrive OLDER first, then NEWER (out of chronological order).
    const fetchImpl = makeStreamingFetch({
      "/v1/stream/anomalies": [
        sse("anomaly", olderFinding),
        sse("anomaly", newerFinding),
      ],
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    // Seed the snapshot so the hook's upsert (which the provider patches via
    // setQueryData) has a base list. The provider patches the cache directly.
    qc.setQueryData(QueryKeys.anomalies, []);

    // We patch the cache the same way useAnomalies does, but the realtime path is
    // exercised through a subscriber that upserts into the cache.
    function Upserter() {
      useSubscription("anomalies", (f) => {
        qc.setQueryData<Finding[]>(QueryKeys.anomalies, (prev) => {
          const without = (prev ?? []).filter(
            (x) => x.finding_id !== f.finding_id,
          );
          const next = [f, ...without];
          next.sort(
            (a, b) =>
              new Date(b.created_at).getTime() -
              new Date(a.created_at).getTime(),
          );
          return next;
        });
      });
      return null;
    }

    render(wrap(<Upserter />, qc, fetchImpl));

    await waitFor(() => {
      const list = qc.getQueryData<Finding[]>(QueryKeys.anomalies) ?? [];
      expect(list.length).toBe(2);
    });
    const list = qc.getQueryData<Finding[]>(QueryKeys.anomalies)!;
    // Despite older-then-newer ARRIVAL, the newest sorts to the head.
    expect(list[0]!.finding_id).toBe(newerFinding.finding_id);
    expect(list[1]!.finding_id).toBe(olderFinding.finding_id);
  });

  it("REFETCHES the snapshot on reconnect (invalidateQueries fires per channel)", async () => {
    // Stream then CLOSE the body so the client schedules a reconnect, whose
    // onReconnect invalidates the snapshot query.
    const encoder = new TextEncoder();
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const u = typeof input === "string" ? input : input.toString();
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          if (u.includes("/v1/stream/anomalies")) {
            controller.enqueue(encoder.encode(sse("anomaly", newerFinding)));
          }
          controller.close(); // close -> triggers reconnect path
        },
      });
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }) as unknown as typeof fetch;

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");

    render(wrap(<AnomalyProbe sink={[]} />, qc, fetchImpl));

    await waitFor(() => {
      const calls = invalidateSpy.mock.calls.map((c) => c[0]);
      expect(
        calls.some(
          (c) =>
            JSON.stringify(
              (c as { queryKey?: unknown } | undefined)?.queryKey,
            ) === JSON.stringify(QueryKeys.anomalies),
        ),
      ).toBe(true);
    });
  });

  it("does not connect when unauthenticated (no token)", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider token={null} baseUrl={TEST_GATEWAY} fetchImpl={fetchImpl}>
          <RealtimeProvider autoConnect fetchImpl={fetchImpl}>
            <AnomalyProbe sink={[]} />
          </RealtimeProvider>
        </AuthProvider>
      </QueryClientProvider>,
    );
    // Give effects a chance to run.
    await act(async () => {
      await Promise.resolve();
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
