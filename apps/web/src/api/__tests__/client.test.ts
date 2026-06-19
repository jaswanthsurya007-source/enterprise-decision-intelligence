/**
 * B3 #1 — API client Zod validation at the gateway boundary.
 *
 * A well-formed payload parses into typed data; a malformed payload (wrong type
 * on a required field) is REJECTED with a typed `ApiError(kind:"validation")` —
 * never silently coerced, never letting `any` cross the boundary. Also covers the
 * RFC 9457 problem+json mapping and bearer attachment.
 */
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../client";
import { ApiError } from "../errors";
import { getKpis, getAnomalies } from "../endpoints";
import { KPI_TILES, EMEA_FINDING } from "../../../test/fixtures";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(fetchImpl: typeof fetch, token: string | null = "dev-jwt") {
  return new ApiClient({
    baseUrl: "http://localhost:8080",
    getToken: () => token,
    fetchImpl,
  });
}

/** Await a promise expected to reject and return the thrown `ApiError`. */
async function expectApiError(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    return e as ApiError;
  }
  throw new Error("expected the request to reject, but it resolved");
}

describe("ApiClient — Zod validation", () => {
  it("parses a well-formed /v1/kpis payload into typed tiles", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse(KPI_TILES)) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);

    const tiles = await getKpis(client);

    expect(tiles).toHaveLength(KPI_TILES.length);
    const emea = tiles.find((t) => t.status === "critical");
    expect(emea?.delta_pct).toBe(-8.3);
    expect(emea?.value).toBe(385000);
  });

  it("parses a well-formed /v1/anomalies Finding (shared contract)", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse([EMEA_FINDING])) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);

    const findings = await getAnomalies(client);
    expect(findings).toHaveLength(1);
    expect(findings[0]!.candidate_causes).toHaveLength(2);
    expect(findings[0]!.candidate_causes[0]!.contribution_pct).toBe(71);
  });

  it("REJECTS a malformed payload (wrong type) with kind:validation", async () => {
    // `value` must be a number; a string must NOT be coerced. A fresh Response is
    // built per fetch so the body isn't re-read across calls.
    const malformed = [{ ...KPI_TILES[0], value: "not-a-number" }];
    const fetchImpl = vi
      .fn()
      .mockImplementation(async () => jsonResponse(malformed)) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);

    const err = await expectApiError(getKpis(client));
    expect(err.kind).toBe("validation");
  });

  it("REJECTS a missing required field (no fallback to any)", async () => {
    // Drop `finding_id` (a required uuid) from a finding.
    const { finding_id: _omit, ...rest } = EMEA_FINDING;
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse([rest])) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);

    await expect(getAnomalies(client)).rejects.toMatchObject({
      kind: "validation",
    });
  });

  it("REJECTS a non-array where a list is expected", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ not: "an array" })) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);
    await expect(getKpis(client)).rejects.toMatchObject({ kind: "validation" });
  });

  it("maps a 500 problem+json into ApiError(kind:http) with the detail", async () => {
    const problem = {
      type: "about:blank",
      title: "Internal Server Error",
      status: 500,
      detail: "boom",
      trace_id: "trace-123",
    };
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse(problem, 500)) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);

    const err = await expectApiError(getKpis(client));
    expect(err.kind).toBe("http");
    expect(err.status).toBe(500);
    expect(err.message).toBe("boom");
    expect(err.traceId).toBe("trace-123");
  });

  it("maps a 401 into ApiError(kind:auth)", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ title: "Unauthorized" }, 401)) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);
    const err = await expectApiError(getKpis(client));
    expect(err.kind).toBe("auth");
    expect(err.isAuth).toBe(true);
  });

  it("attaches the dev-JWT bearer on every call", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(KPI_TILES));
    const client = makeClient(fetchImpl as unknown as typeof fetch, "my-token");
    await getKpis(client);
    const init = fetchImpl.mock.calls[0]![1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer my-token");
  });

  it("surfaces a fetch throw as ApiError(kind:network)", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValue(new TypeError("Failed to fetch")) as unknown as typeof fetch;
    const client = makeClient(fetchImpl);
    await expect(getKpis(client)).rejects.toMatchObject({ kind: "network" });
  });
});
