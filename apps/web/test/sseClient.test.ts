/**
 * B1 smoke tests for the realtime SSE client and dev-JWT decoder — proving the
 * shell's plumbing works with NO backend (a mocked `fetch` streams SSE bytes).
 */
import { describe, expect, it, vi } from "vitest";
import { SseClient, type SseFrame } from "../src/realtime/sseClient";
import { decodeIdentity, isExpired } from "../src/auth/jwt";

/** Build a Response whose body streams the given chunks then closes. */
function streamResponse(chunks: string[]): Response {
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

/** Mint an unsigned JWT (header.payload.sig) for claim-decoding tests. */
function makeJwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}.sig`;
}

describe("SseClient", () => {
  it("parses event/data frames and skips heartbeats", async () => {
    const frames: SseFrame[] = [];
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        streamResponse([
          ":hb\n\n",
          'event: metric\ndata: {"value":1}\n\n',
          "event: heartbeat\ndata: \n\n",
          'event: metric\ndata: {"value":2}\n\n',
        ]),
      )
      // After the stream closes the client reconnects; keep it from looping.
      .mockResolvedValue(streamResponse([]));

    const client = new SseClient({
      url: "http://localhost:8080/v1/stream/metrics",
      headers: () => ({ Authorization: "Bearer t" }),
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onFrame: (f) => frames.push(f),
      backoffBaseMs: 10_000, // keep the reconnect from firing during the test
    });
    client.start();
    await vi.waitFor(() => expect(frames.length).toBe(2));
    client.stop();

    expect(frames.map((f) => f.event)).toEqual(["metric", "metric"]);
    expect(JSON.parse(frames[0]!.data)).toEqual({ value: 1 });
    // bearer attached
    const headers = (fetchImpl.mock.calls[0]![1] as RequestInit).headers as Record<
      string,
      string
    >;
    expect(headers.Authorization).toBe("Bearer t");
  });

  it("invokes onReconnect before re-opening (snapshot gap-close)", async () => {
    const onReconnect = vi.fn();
    const fetchImpl = vi.fn().mockResolvedValue(streamResponse([]));
    const client = new SseClient({
      url: "http://localhost:8080/v1/stream/anomalies",
      headers: () => ({}),
      fetchImpl: fetchImpl as unknown as typeof fetch,
      onFrame: () => {},
      onReconnect,
      backoffBaseMs: 5,
      backoffMaxMs: 5,
    });
    client.start();
    await vi.waitFor(() => expect(onReconnect).toHaveBeenCalled());
    client.stop();
  });
});

describe("decodeIdentity", () => {
  it("reads tenant/roles/scopes from claims", () => {
    const token = makeJwt({
      tenant_id: "acme",
      user_id: "u-1",
      roles: ["analyst", "operator"],
      scopes: ["read"],
    });
    const id = decodeIdentity(token);
    expect(id).toMatchObject({
      tenantId: "acme",
      userId: "u-1",
      roles: ["analyst", "operator"],
      scopes: ["read"],
    });
  });

  it("returns null for malformed/empty tokens", () => {
    expect(decodeIdentity(null)).toBeNull();
    expect(decodeIdentity("not-a-jwt")).toBeNull();
    expect(decodeIdentity(makeJwt({ roles: [] }))).toBeNull(); // missing tenant/user
  });

  it("detects expiry", () => {
    const past = makeJwt({ tenant_id: "acme", user_id: "u", exp: 1 });
    const id = decodeIdentity(past)!;
    expect(isExpired(id, 10_000)).toBe(true);
  });
});
