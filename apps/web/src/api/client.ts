/**
 * Typed gateway client.
 *
 * - Attaches `Authorization: Bearer <dev-jwt>` on every call (token supplied by
 *   the auth layer; see `auth/AuthProvider`).
 * - Validates every JSON response against a Zod schema — no `any` crosses the
 *   boundary; a shape mismatch throws a typed `ApiError(kind:"validation")`.
 * - Maps non-2xx + RFC 9457 problem+json into `ApiError`.
 *
 * The base URL comes from `import.meta.env.VITE_GATEWAY_URL`. A token getter is
 * injected (rather than read from env directly) so tests and the auth provider
 * control it, and so a future OIDC access-token can drop in unchanged.
 */
import type { z } from "zod";
import { ApiError, apiErrorFromResponse, toApiError } from "./errors";

export type TokenGetter = () => string | null;

export interface ApiClientOptions {
  baseUrl: string;
  getToken: TokenGetter;
  /** Test seam. */
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  /** Skip attaching the bearer (e.g. health checks). Default false. */
  anonymous?: boolean;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getToken: TokenGetter;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: ApiClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.getToken = opts.getToken;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  /** Absolute URL for a gateway path (`/v1/...`). Exposed for the SSE client. */
  url(path: string): string {
    return `${this.baseUrl}${path.startsWith("/") ? "" : "/"}${path}`;
  }

  /** Auth header object (or empty) — shared by REST and SSE-via-fetch. */
  authHeaders(): Record<string, string> {
    const token = this.getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  private async raw(path: string, opts: RequestOptions): Promise<Response> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(opts.anonymous ? {} : this.authHeaders()),
      ...opts.headers,
    };
    let res: Response;
    try {
      res = await this.fetchImpl(this.url(path), {
        method: opts.method ?? "GET",
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        signal: opts.signal,
      });
    } catch (err) {
      throw toApiError(err);
    }
    if (!res.ok) throw await apiErrorFromResponse(res);
    return res;
  }

  /** GET/POST a JSON endpoint and validate the response with `schema`. */
  async request<S extends z.ZodTypeAny>(
    path: string,
    schema: S,
    opts: RequestOptions = {},
  ): Promise<z.infer<S>> {
    const res = await this.raw(path, opts);
    // 204 / empty body with a schema that permits undefined.
    const text = await res.text();
    let json: unknown;
    if (text.length === 0) {
      json = undefined;
    } else {
      try {
        json = JSON.parse(text);
      } catch (err) {
        throw new ApiError({
          kind: "parse",
          message: "Response was not valid JSON",
          status: res.status,
          cause: err,
        });
      }
    }
    const parsed = schema.safeParse(json);
    if (!parsed.success) {
      throw new ApiError({
        kind: "validation",
        message: `Response failed schema validation: ${parsed.error.message}`,
        status: res.status,
        cause: parsed.error,
      });
    }
    return parsed.data;
  }

  get<S extends z.ZodTypeAny>(
    path: string,
    schema: S,
    opts: Omit<RequestOptions, "method" | "body"> = {},
  ): Promise<z.infer<S>> {
    return this.request(path, schema, { ...opts, method: "GET" });
  }

  post<S extends z.ZodTypeAny>(
    path: string,
    schema: S,
    body?: unknown,
    opts: Omit<RequestOptions, "method"> = {},
  ): Promise<z.infer<S>> {
    return this.request(path, schema, { ...opts, method: "POST", body });
  }
}
