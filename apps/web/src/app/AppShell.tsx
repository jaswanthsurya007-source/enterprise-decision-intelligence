/**
 * AppShell — the persistent cockpit chrome: a slim sidebar (primary nav), a
 * topbar with the tenant badge, the authenticated user, and a live-connection
 * indicator driven by the realtime status. Content renders into the `<Outlet>`.
 *
 * Dense, neutral, Grafana/Linear-flavored: zinc surfaces, subtle borders, one
 * teal accent for the active nav item.
 */
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { useRealtimeStatus } from "../realtime/useSubscription";
import type { ConnectionStatus } from "../realtime/events";
import { TenantSwitcher } from "./TenantSwitcher";

interface NavItem {
  to: string;
  label: string;
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview" },
  { to: "/copilot", label: "Copilot" },
];

function aggregateStatus(
  statuses: Record<string, ConnectionStatus>,
): { label: string; tone: string } {
  const values = Object.values(statuses);
  if (values.every((s) => s === "open")) {
    return { label: "Live", tone: "bg-status-ok" };
  }
  if (values.some((s) => s === "open")) {
    return { label: "Partial", tone: "bg-status-warn" };
  }
  if (values.some((s) => s === "connecting" || s === "reconnecting")) {
    return { label: "Connecting", tone: "bg-status-warn" };
  }
  return { label: "Offline", tone: "bg-status-critical" };
}

export function AppShell() {
  const { identity, roles } = useAuth();
  const status = useRealtimeStatus();
  const conn = aggregateStatus(status);

  return (
    <div className="grid h-full grid-cols-[200px_1fr] grid-rows-[48px_1fr] bg-surface-base">
      {/* Sidebar */}
      <aside className="row-span-2 flex flex-col border-r border-border-subtle bg-surface-raised">
        <div className="flex h-12 items-center gap-2 border-b border-border-subtle px-4">
          <span className="inline-block h-2 w-2 rounded-sm bg-accent" />
          <span className="text-sm font-semibold tracking-tight text-fg-default">
            EDIS
          </span>
          <span className="text-2xs text-fg-subtle">cockpit</span>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 p-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                [
                  "focus-ring rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-accent-muted/40 text-fg-default"
                    : "text-fg-muted hover:bg-surface-overlay hover:text-fg-default",
                ].join(" ")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-border-subtle p-3 text-2xs text-fg-subtle">
          MVP · dev JWT auth
        </div>
      </aside>

      {/* Topbar */}
      <header className="col-start-2 flex items-center justify-between border-b border-border-subtle bg-surface-raised px-4">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex h-1.5 w-1.5 rounded-full ${conn.tone}`}
            aria-hidden
          />
          <span className="text-xs text-fg-muted">{conn.label}</span>
        </div>
        <div className="flex items-center gap-4">
          <TenantSwitcher />
          <div className="flex items-center gap-2 text-2xs text-fg-subtle">
            <span className="font-mono text-fg-muted">
              {identity?.userId ?? "anonymous"}
            </span>
            {roles.length > 0 && (
              <span className="rounded border border-border-subtle px-1.5 py-0.5">
                {roles.join(", ")}
              </span>
            )}
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="col-start-2 overflow-auto p-4">
        <Outlet />
      </main>
    </div>
  );
}
