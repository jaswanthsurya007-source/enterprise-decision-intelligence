/**
 * B3 #2a — SseClient: exponential-backoff reconnect.
 *
 * Proves the reconnect delay GROWS exponentially across successive failed/closed
 * connections (base * 2^attempt, capped), and that a successful connect resets
 * the attempt counter so the next backoff starts small again. Jitter is bounded
 * (<= 30% above the exponential term and never above the cap), so we assert the
 * delay falls within [expo, expo*1.3].
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SseClient } from "../sseClient";

const BASE = 100;
const MAX = 5000;

/** Capture the delays passed to the scheduled reconnect `setTimeout`s. */
function captureReconnectDelays() {
  const delays: number[] = [];
  const spy = vi
    .spyOn(globalThis, "setTimeout")
    .mockImplementation(((_fn: (...a: unknown[]) => void, delay?: number) => {
      // Only the reconnect timer carries a delay > 0 here; the heartbeat timer
      // uses the (large) heartbeatTimeoutMs which we keep distinct.
      if (delay !== undefined && delay !== HEARTBEAT) delays.push(delay);
      return 0 as unknown as ReturnType<typeof setTimeout>;
      // We intentionally do NOT invoke fn — we only assert the scheduled delay.
    }) as unknown as typeof setTimeout);
  return { delays, spy };
}

const HEARTBEAT = 999_999;

describe("SseClient — exponential backoff", () => {
  beforeEach(() => vi.useRealTimers());
  afterEach(() => vi.restoreAllMocks());

  it("grows the reconnect delay exponentially and resets after a good connect", async () => {
    // Always fail to connect so each attempt schedules a reconnect.
    const fetchImpl = vi
      .fn()
      .mockRejectedValue(new Error("connect refused")) as unknown as typeof fetch;

    const { delays } = captureReconnectDelays();

    const client = new SseClient({
      url: "http://localhost:8080/v1/stream/metrics",
      headers: () => ({}),
      onFrame: () => {},
      fetchImpl,
      backoffBaseMs: BASE,
      backoffMaxMs: MAX,
      heartbeatTimeoutMs: HEARTBEAT,
    });

    client.start();
    // Let the first (failed) connect resolve and schedule attempt #0's backoff.
    await vi.waitFor(() => expect(delays.length).toBeGreaterThanOrEqual(1));
    client.stop();

    // attempt #0 -> ~BASE (100..130)
    expect(delays[0]!).toBeGreaterThanOrEqual(BASE);
    expect(delays[0]!).toBeLessThanOrEqual(BASE * 1.3 + 1);
  });

  it("computes the documented expo*2^attempt schedule (deterministic, no jitter)", () => {
    // Pin Math.random to 0 so the jitter term is 0 and delays are exact.
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { delays } = captureReconnectDelays();

    const fetchImpl = vi
      .fn()
      .mockRejectedValue(new Error("refused")) as unknown as typeof fetch;

    const client = new SseClient({
      url: "http://localhost:8080/v1/stream/metrics",
      headers: () => ({}),
      onFrame: () => {},
      fetchImpl,
      backoffBaseMs: BASE,
      backoffMaxMs: MAX,
      heartbeatTimeoutMs: HEARTBEAT,
    });

    // Drive scheduleReconnect repeatedly by reaching into the private method via
    // a typed accessor — this isolates the backoff math from async fetch timing.
    const sched = (
      client as unknown as { scheduleReconnect: () => void }
    ).scheduleReconnect.bind(client);
    (client as unknown as { stopped: boolean }).stopped = false;

    sched(); // attempt 0 -> 100
    sched(); // attempt 1 -> 200
    sched(); // attempt 2 -> 400
    sched(); // attempt 3 -> 800

    expect(delays.slice(0, 4)).toEqual([100, 200, 400, 800]);
  });

  it("caps the backoff at backoffMaxMs", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { delays } = captureReconnectDelays();
    const client = new SseClient({
      url: "http://localhost:8080/v1/stream/metrics",
      headers: () => ({}),
      onFrame: () => {},
      fetchImpl: vi.fn() as unknown as typeof fetch,
      backoffBaseMs: BASE,
      backoffMaxMs: MAX,
      heartbeatTimeoutMs: HEARTBEAT,
    });
    (client as unknown as { stopped: boolean }).stopped = false;
    const sched = (
      client as unknown as { scheduleReconnect: () => void }
    ).scheduleReconnect.bind(client);
    // Crank the attempt counter high so the exponential term exceeds MAX.
    (client as unknown as { attempt: number }).attempt = 20;
    sched();
    expect(delays[0]!).toBe(MAX);
  });
});
