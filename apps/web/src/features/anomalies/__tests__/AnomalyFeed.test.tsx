/**
 * B3 #6 — AnomalyFeed + RootCausePanel render the demo EMEA level-shift and its
 * lag-adjusted candidate causes.
 *
 * The feed loads via `useAnomalies` -> `GET /v1/anomalies` (MSW, Zod-validated),
 * auto-selects the first finding, and the RootCausePanel shows the computed
 * detection facts (observed/expected/deviation/score) and the ranked candidate
 * causes (latency_p95 leading @ 71%, error_rate @ 22%). Selecting a row drives the
 * panel.
 */
import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { server } from "../../../../test/setup";
import { handlers, emptyHandlers } from "../../../../test/msw/handlers";
import { renderWithProviders } from "../../../../test/utils";
import { AnomalyFeed } from "../AnomalyFeed";

describe("AnomalyFeed + RootCausePanel", () => {
  it("renders the EMEA level-shift in the feed and its candidate causes in the RCA panel", async () => {
    server.use(...handlers);
    renderWithProviders(<AnomalyFeed />);

    // Feed row for the EMEA revenue level-shift.
    const row = await screen.findByTestId("anomaly-row");
    expect(within(row).getByText("revenue")).toBeInTheDocument();
    expect(within(row).getByText(/level shift/i)).toBeInTheDocument();
    expect(within(row).getByText("-35.8%")).toBeInTheDocument();

    // Auto-selected -> RCA panel shows the computed detection facts. The panel
    // renders observed/expected without a unit hint, so compact form ("61K").
    const panel = await screen.findByTestId("rca-panel");
    expect(within(panel).getByText("61K")).toBeInTheDocument(); // observed
    expect(within(panel).getByText("95K")).toBeInTheDocument(); // expected
    expect(within(panel).getByText("5.8")).toBeInTheDocument(); // score (σ)

    // The ranked candidate causes (lag-adjusted RCA).
    expect(within(panel).getByText("latency_p95")).toBeInTheDocument();
    expect(within(panel).getByText("error_rate")).toBeInTheDocument();
    // contribution 71% and lag 120m on the leading cause.
    expect(within(panel).getByText("71%")).toBeInTheDocument();
    expect(within(panel).getAllByText("120m").length).toBeGreaterThanOrEqual(1);
    // direction badge.
    expect(within(panel).getAllByText(/leading/i).length).toBeGreaterThanOrEqual(
      1,
    );
  });

  it("shows the grounded narrative as prose (numbers come from structured fields)", async () => {
    server.use(...handlers);
    renderWithProviders(<AnomalyFeed />);
    const panel = await screen.findByTestId("rca-panel");
    expect(within(panel).getByText(/Grounded narrative/i)).toBeInTheDocument();
    expect(within(panel).getByText(/claude-opus-4-8/i)).toBeInTheDocument();
  });

  it("renders the empty state when no anomalies are present", async () => {
    server.use(...emptyHandlers);
    renderWithProviders(<AnomalyFeed />);
    expect(
      await screen.findByText(/No anomalies detected/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Select an anomaly to inspect/i),
    ).toBeInTheDocument();
  });
});
