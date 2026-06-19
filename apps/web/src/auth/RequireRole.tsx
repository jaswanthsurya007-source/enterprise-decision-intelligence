/**
 * UX-only role guard. The gateway is the authoritative authz boundary (§5.8);
 * this only hides UI the user cannot use, to avoid dead-ends. It never grants
 * access — a forbidden API call still 403s server-side and surfaces as an
 * `ApiError(kind:"auth")`.
 */
import type { ReactNode } from "react";
import { useAuth } from "./useAuth";

export interface RequireRoleProps {
  /** Any one of these roles satisfies the guard. */
  anyOf: string[];
  children: ReactNode;
  /** Rendered when the user lacks the role. Defaults to nothing. */
  fallback?: ReactNode;
}

export function RequireRole({ anyOf, children, fallback = null }: RequireRoleProps) {
  const { hasAnyRole } = useAuth();
  return <>{hasAnyRole(anyOf) ? children : fallback}</>;
}
