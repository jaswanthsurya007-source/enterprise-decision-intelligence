/**
 * Route table. The AppShell wraps all authenticated pages; the realtime + auth
 * providers are mounted above the router (see `main.tsx`).
 */
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { OverviewPage } from "./pages/Overview";
import { CopilotPage } from "./pages/Copilot";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "copilot", element: <CopilotPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
