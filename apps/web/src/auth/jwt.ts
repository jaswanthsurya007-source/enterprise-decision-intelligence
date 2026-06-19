/**
 * Minimal, dependency-free JWT claim decoder.
 *
 * The web app does NOT verify the token — the gateway validates HS256 against
 * the shared dev secret server-side (it is the authoritative authz boundary; the
 * React role guards are UX only, §5.6/§5.8). We only base64url-decode the
 * payload to read `{tenant_id, user_id, roles, scopes}` for the UI.
 */
import { z } from "zod";

/**
 * Claim shape we read. Tenant/roles come ONLY from the verified token (the
 * gateway re-derives them); we accept common aliases so a dev token minted by
 * the platform's `jwt.py` works regardless of the exact claim names.
 */
export const DevClaimsSchema = z
  .object({
    tenant_id: z.string().optional(),
    tid: z.string().optional(),
    user_id: z.string().optional(),
    sub: z.string().optional(),
    roles: z.array(z.string()).optional(),
    scopes: z.array(z.string()).optional(),
    scope: z.string().optional(), // space-delimited fallback
    exp: z.number().optional(),
  })
  .passthrough();
export type DevClaims = z.infer<typeof DevClaimsSchema>;

export interface DecodedIdentity {
  tenantId: string;
  userId: string;
  roles: string[];
  scopes: string[];
  exp: number | null;
}

function base64UrlDecode(input: string): string {
  const pad = input.length % 4 === 0 ? "" : "=".repeat(4 - (input.length % 4));
  const b64 = input.replace(/-/g, "+").replace(/_/g, "/") + pad;
  if (typeof atob === "function") return atob(b64);
  // Node fallback (vitest without jsdom atob).
  return Buffer.from(b64, "base64").toString("binary");
}

/**
 * Decode a JWT's claims. Returns null when the token is absent or malformed —
 * the caller renders the "no dev token configured" state rather than crashing.
 */
export function decodeIdentity(token: string | null | undefined): DecodedIdentity | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;
  const payloadPart = parts[1];
  if (!payloadPart) return null;
  let claims: DevClaims;
  try {
    const json = JSON.parse(
      decodeURIComponent(
        base64UrlDecode(payloadPart)
          .split("")
          .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
          .join(""),
      ),
    );
    const parsed = DevClaimsSchema.safeParse(json);
    if (!parsed.success) return null;
    claims = parsed.data;
  } catch {
    return null;
  }

  const tenantId = claims.tenant_id ?? claims.tid;
  const userId = claims.user_id ?? claims.sub;
  if (!tenantId || !userId) return null;

  const scopes =
    claims.scopes ?? (claims.scope ? claims.scope.split(/\s+/).filter(Boolean) : []);

  return {
    tenantId,
    userId,
    roles: claims.roles ?? [],
    scopes,
    exp: claims.exp ?? null,
  };
}

/** True if the token carries an `exp` in the past. */
export function isExpired(identity: DecodedIdentity, nowMs = Date.now()): boolean {
  return identity.exp !== null && identity.exp * 1000 <= nowMs;
}
