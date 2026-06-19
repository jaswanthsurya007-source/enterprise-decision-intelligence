/**
 * App — wires the router and renders a clear "dev token not configured" gate
 * when no usable JWT is present, so the cockpit degrades gracefully instead of
 * 401-storming the gateway. Providers (QueryClient, Auth, Realtime) are mounted
 * in `main.tsx` above this component.
 */
import { RouterProvider } from "react-router-dom";
import { useAuth } from "./auth/useAuth";
import { router } from "./routes";

export function App() {
  const { isAuthenticated, isExpired, identity } = useAuth();

  if (!isAuthenticated) {
    return (
      <div className="flex h-full items-center justify-center bg-surface-base p-8">
        <div className="card card-pad max-w-md">
          <div className="mb-1 flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-sm bg-accent" />
            <span className="text-sm font-semibold text-fg-default">
              EDIS cockpit
            </span>
          </div>
          <h1 className="mb-2 text-base font-semibold text-fg-default">
            {isExpired ? "Dev token expired" : "No dev token configured"}
          </h1>
          <p className="text-sm text-fg-muted">
            {isExpired
              ? "The configured VITE_DEV_JWT has expired. Mint a fresh dev token and restart."
              : identity === null
                ? "Set VITE_DEV_JWT in .env.local to a dev JWT the gateway accepts (HS256, shared dev secret), then restart the dev server."
                : "The configured token is missing required claims (tenant_id / user_id)."}
          </p>
          <p className="mt-3 text-2xs text-fg-subtle">
            See .env.example. OIDC/PKCE sign-in is the designed-future path.
          </p>
        </div>
      </div>
    );
  }

  return <RouterProvider router={router} />;
}
