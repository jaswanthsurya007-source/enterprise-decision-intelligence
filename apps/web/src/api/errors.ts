/**
 * RFC 9457 (Problem Details) error mapping for the gateway boundary.
 *
 * The EDIS platform maps typed exceptions to `application/problem+json`
 * (see `libs/edis-platform/edis_platform/errors.py`). The web client surfaces
 * those as a single typed `ApiError` so callers (and TanStack Query) get a
 * consistent shape regardless of transport, parse, or HTTP failure.
 */
import { z } from "zod";

/** RFC 9457 Problem Details document. */
export const ProblemDetailsSchema = z.object({
  type: z.string().default("about:blank"),
  title: z.string().nullish(),
  status: z.number().int().nullish(),
  detail: z.string().nullish(),
  instance: z.string().nullish(),
  // EDIS extensions (allowed by RFC 9457 §3.2):
  code: z.string().nullish(),
  trace_id: z.string().nullish(),
  errors: z.array(z.record(z.string(), z.unknown())).nullish(),
});
export type ProblemDetails = z.infer<typeof ProblemDetailsSchema>;

export type ApiErrorKind =
  | "http" // non-2xx with a (maybe) problem+json body
  | "validation" // response body failed Zod validation
  | "network" // fetch threw (offline, CORS, DNS)
  | "auth" // 401/403 — token missing/invalid/forbidden
  | "parse"; // body was not valid JSON

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly problem: ProblemDetails | null;
  readonly traceId: string | null;
  override readonly cause: unknown;

  constructor(args: {
    kind: ApiErrorKind;
    message: string;
    status?: number | null;
    problem?: ProblemDetails | null;
    traceId?: string | null;
    cause?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.kind = args.kind;
    this.status = args.status ?? null;
    this.problem = args.problem ?? null;
    this.traceId = args.traceId ?? args.problem?.trace_id ?? null;
    this.cause = args.cause;
  }

  /** True for 401/403 — the UI may prompt re-auth / show a permission notice. */
  get isAuth(): boolean {
    return this.kind === "auth";
  }
}

/** Build an `ApiError` from a non-OK `Response`, attempting RFC 9457 parsing. */
export async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  const kind: ApiErrorKind =
    res.status === 401 || res.status === 403 ? "auth" : "http";
  let problem: ProblemDetails | null = null;
  try {
    const text = await res.text();
    if (text) {
      const parsed = ProblemDetailsSchema.safeParse(JSON.parse(text));
      if (parsed.success) problem = parsed.data;
    }
  } catch {
    // body absent or not JSON — fall through with a status-only error
  }
  const message =
    problem?.detail ??
    problem?.title ??
    `Request failed with status ${res.status}`;
  return new ApiError({ kind, message, status: res.status, problem });
}

/** Coerce any thrown value into an `ApiError` for uniform handling. */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  if (err instanceof Error) {
    return new ApiError({ kind: "network", message: err.message, cause: err });
  }
  return new ApiError({ kind: "network", message: "Unknown network error", cause: err });
}
