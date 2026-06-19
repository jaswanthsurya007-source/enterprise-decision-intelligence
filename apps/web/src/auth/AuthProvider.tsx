/**
 * Dev-mode auth context.
 *
 * Reads the pre-minted static JWT from `import.meta.env.VITE_DEV_JWT`, decodes
 * its claims for tenant/roles/scopes (display only; the gateway is the authority),
 * and exposes a stable `getToken()` the `ApiClient` uses to attach the bearer.
 *
 * A `tenant` override is supported (the `TenantSwitcher` is a dev affordance): in
 * the MVP the tenant comes from the token, so switching tenants is only
 * meaningful if you provide a token for that tenant. We expose the override so
 * the UI can reflect a selected tenant; production OIDC is the designed-future
 * replacement and would slot in behind this same context shape.
 */
import {
  createContext,
  useCallback,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiClient } from "../api/client";
import { decodeIdentity, isExpired, type DecodedIdentity } from "./jwt";

export interface AuthState {
  /** Decoded identity from the dev JWT, or null if absent/malformed. */
  identity: DecodedIdentity | null;
  /** True when a usable (present, non-expired) token is configured. */
  isAuthenticated: boolean;
  /** True when the configured token is present but expired. */
  isExpired: boolean;
  /** The active tenant (from token; reflects switcher override if set). */
  tenantId: string | null;
  roles: string[];
  scopes: string[];
  /** Raw token getter for the API/SSE layers. */
  getToken: () => string | null;
  /** Configured, ready-to-use gateway client (bearer wired). */
  client: ApiClient;
  /** Role/scope predicates (UX-only guards). */
  hasRole: (role: string) => boolean;
  hasAnyRole: (roles: string[]) => boolean;
  hasScope: (scope: string) => boolean;
  /** Dev-only tenant override (does not re-mint the token). */
  setTenantOverride: (tenantId: string | null) => void;
}

export const AuthContext = createContext<AuthState | null>(null);

export interface AuthProviderProps {
  children: ReactNode;
  /** Test/override seam — defaults to `import.meta.env.VITE_DEV_JWT`. */
  token?: string | null;
  /** Test/override seam — defaults to `import.meta.env.VITE_GATEWAY_URL`. */
  baseUrl?: string;
  /** Test seam for the underlying client. */
  fetchImpl?: typeof fetch;
}

export function AuthProvider({
  children,
  token,
  baseUrl,
  fetchImpl,
}: AuthProviderProps) {
  const resolvedToken =
    token !== undefined ? token : (import.meta.env.VITE_DEV_JWT ?? null);
  const resolvedBaseUrl =
    baseUrl ?? import.meta.env.VITE_GATEWAY_URL ?? "http://localhost:8080";

  const [tenantOverride, setTenantOverride] = useState<string | null>(null);

  const identity = useMemo(
    () => decodeIdentity(resolvedToken),
    [resolvedToken],
  );

  const getToken = useCallback(() => resolvedToken, [resolvedToken]);

  const client = useMemo(
    () =>
      new ApiClient({
        baseUrl: resolvedBaseUrl,
        getToken,
        ...(fetchImpl ? { fetchImpl } : {}),
      }),
    [resolvedBaseUrl, getToken, fetchImpl],
  );

  const value = useMemo<AuthState>(() => {
    const expired = identity ? isExpired(identity) : false;
    const roles = identity?.roles ?? [];
    const scopes = identity?.scopes ?? [];
    return {
      identity,
      isAuthenticated: identity !== null && !expired,
      isExpired: expired,
      tenantId: tenantOverride ?? identity?.tenantId ?? null,
      roles,
      scopes,
      getToken,
      client,
      hasRole: (role) => roles.includes(role),
      hasAnyRole: (wanted) => wanted.some((r) => roles.includes(r)),
      hasScope: (scope) => scopes.includes(scope),
      setTenantOverride,
    };
  }, [identity, tenantOverride, getToken, client]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
