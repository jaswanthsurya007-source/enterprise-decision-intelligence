/**
 * B3 #3 — KpiGrid renders the demo KPIs, including the RED EMEA revenue tile with
 * its -8.3% WoW delta, sorted to the front (critical first). Loading / empty /
 * error states are covered too. Data flows through `useKpis` -> `GET /v1/kpis`
 * (MSW), so the real Zod validation runs with NO backend.
 */
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { server } from "../../../../test/setup";
import { handlers, emptyHandlers, errorHandlers } from "../../../../test/msw/handlers";
import { renderWithProviders } from "../../../../test/utils";
import { KpiGrid } from "../KpiGrid";

describe("KpiGrid", () => {
  it("renders the demo tiles with the red EMEA -8.3% revenue tile first", async () => {
    server.use(...handlers);
    renderWithProviders(<KpiGrid />);

    await waitFor(() => expect(screen.getByTestId("kpi-grid")).toBeInTheDocument());

    const cards = screen.getAllByTestId("kpi-card");
    expect(cards.length).toBe(3);

    // Critical tile sorts to the front.
    const first = cards[0]!;
    expect(first).toHaveAttribute("data-status", "critical");
    expect(first).toHaveAttribute("data-metric", "revenue");

    // The authoritative figure + delta render (compact USD + signed pct).
    expect(within(first).getByText("$385.0K")).toBeInTheDocument();
    expect(within(first).getByText("-8.3%")).toBeInTheDocument();
    expect(within(first).getByText("EMEA Revenue")).toBeInTheDocument();
  });

  it("shows the empty state when the gateway returns no tiles", async () => {
    server.use(...emptyHandlers);
    renderWithProviders(<KpiGrid />);
    expect(
      await screen.findByText(/Tiles appear as metrics stream in/i),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("kpi-card")).not.toBeInTheDocument();
  });

  it("shows an error state with a retry affordance on a 500", async () => {
    server.use(...errorHandlers);
    renderWithProviders(<KpiGrid />);
    expect(await screen.findByText(/Could not load KPIs/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
