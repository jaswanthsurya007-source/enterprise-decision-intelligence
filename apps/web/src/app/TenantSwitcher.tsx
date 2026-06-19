/**
 * Tenant badge + dev tenant switcher.
 *
 * In the MVP the tenant comes from the verified dev JWT (the gateway is the
 * authority). This control reflects the active tenant and offers a dev-only
 * override; switching to a tenant the token does not authorize will simply 403
 * server-side. It is intentionally lightweight — full multi-tenant token
 * exchange arrives with OIDC (designed-future).
 */
import { useAuth } from "../auth/useAuth";

/** Known demo tenants (the seed creates `acme`); editable in dev. */
const KNOWN_TENANTS = ["acme"];

export function TenantSwitcher() {
  const { tenantId, identity, setTenantOverride } = useAuth();

  if (!identity) {
    return (
      <span className="rounded-md border border-border-subtle bg-surface-overlay px-2 py-1 text-2xs text-fg-subtle">
        no tenant
      </span>
    );
  }

  const tenants = Array.from(
    new Set([identity.tenantId, ...KNOWN_TENANTS, tenantId].filter(Boolean)),
  ) as string[];

  return (
    <label className="flex items-center gap-2 text-2xs text-fg-subtle">
      <span className="uppercase tracking-wide">tenant</span>
      <select
        value={tenantId ?? identity.tenantId}
        onChange={(e) => {
          const next = e.target.value;
          setTenantOverride(next === identity.tenantId ? null : next);
        }}
        className="focus-ring rounded-md border border-border-strong bg-surface-overlay px-2 py-1 font-mono text-xs text-fg-default"
      >
        {tenants.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
}
