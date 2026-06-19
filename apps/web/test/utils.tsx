/**
 * Test rendering helpers — wire the provider stack (QueryClient + dev Auth +
 * Realtime) the feature components expect, with NO backend.
 *
 * - A fresh `QueryClient` per render (retries off, GC immediate) so tests don't
 *   leak cache across cases.
 * - `AuthProvider` is given an explicit dev JWT token + base URL (the test seam),
 *   so `import.meta.env` need not be set; roles drive the UX-only guards.
 * - `RealtimeProvider` defaults to `autoConnect={false}` so a component test does
 *   not open SSE links unless it opts in (the SSE behavior is tested directly).
 */
import { type ReactElement, type ReactNode } from "react";
import { render, type RenderResult } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../src/auth/AuthProvider";
import { RealtimeProvider } from "../src/realtime/RealtimeProvider";

export const TEST_GATEWAY = "http://localhost:8080";

/** Mint an unsigned dev JWT (header.payload.sig) carrying the given claims. */
export function makeJwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) =>
    Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}.sig`;
}

/** A dev token for tenant `acme` with operator+analyst roles (can act). */
export function devToken(
  roles: string[] = ["analyst", "operator"],
): string {
  return makeJwt({
    tenant_id: "acme",
    user_id: "u-test",
    roles,
    scopes: ["read"],
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersOptions {
  queryClient?: QueryClient;
  token?: string | null;
  roles?: string[];
  autoConnect?: boolean;
  fetchImpl?: typeof fetch;
}

export interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient;
}

/** Render `ui` inside the full provider stack used by the dashboard features. */
export function renderWithProviders(
  ui: ReactElement,
  opts: RenderWithProvidersOptions = {},
): RenderWithProvidersResult {
  const queryClient = opts.queryClient ?? makeQueryClient();
  const token = opts.token !== undefined ? opts.token : devToken(opts.roles);

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AuthProvider
          token={token}
          baseUrl={TEST_GATEWAY}
          {...(opts.fetchImpl ? { fetchImpl: opts.fetchImpl } : {})}
        >
          <RealtimeProvider
            autoConnect={opts.autoConnect ?? false}
            {...(opts.fetchImpl ? { fetchImpl: opts.fetchImpl } : {})}
          >
            {children}
          </RealtimeProvider>
        </AuthProvider>
      </QueryClientProvider>
    );
  }

  const result = render(ui, { wrapper: Wrapper });
  return { ...result, queryClient };
}
