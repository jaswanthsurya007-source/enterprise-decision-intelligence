/**
 * Entry point. Provider order (outer → inner):
 *   QueryClientProvider  (TanStack Query cache)
 *   AuthProvider         (dev-JWT → identity + bearer-wired ApiClient)
 *   RealtimeProvider     (SSE links; connect on mount, only when authenticated)
 *   ErrorBoundary        (last-resort render guard)
 *   App                  (auth gate + router)
 *
 * Nothing connects at module load: RealtimeProvider opens SSE inside a mount
 * effect, gated on `isAuthenticated`.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./auth/AuthProvider";
import { RealtimeProvider } from "./realtime/RealtimeProvider";
import { ErrorBoundary } from "./app/ErrorBoundary";
import { App } from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Realtime SSE keeps the cache fresh; snapshots are the gap-closer.
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RealtimeProvider>
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
        </RealtimeProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
