/**
 * B3 #4 — RecommendationCard renders the rank-1 `operational_fix` action with its
 * confidence gauge and explainability accordion.
 *
 * Asserts the computed figures render verbatim (impact $170K, range $120K-$200K,
 * 0.84 confidence, the confidence-component breakdown and impact inputs from the
 * static-prior facts) and that the accordion expands. Also verifies the UX-only
 * role guard: an operator can act; a viewer's buttons are disabled.
 */
import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../../../test/utils";
import { RecommendationCard } from "../RecommendationCard";
import { EMEA_RECOMMENDATION } from "../../../../test/fixtures";

describe("RecommendationCard", () => {
  it("renders the rank-1 operational_fix action with computed impact + confidence", () => {
    renderWithProviders(
      <RecommendationCard recommendation={EMEA_RECOMMENDATION} />,
      { roles: ["operator"] },
    );

    const card = screen.getByTestId("recommendation-card");
    expect(within(card).getByText("#1")).toBeInTheDocument();
    expect(within(card).getByText(/operational fix/i)).toBeInTheDocument();
    expect(
      within(card).getByText(
        /Mitigate checkout-api latency in EMEA/i,
      ),
    ).toBeInTheDocument();

    // Computed impact estimate, rendered verbatim (compact USD).
    expect(within(card).getByText("+$170.0K")).toBeInTheDocument();
    expect(
      within(card).getByText(/\$120\.0K\s*–\s*\$200\.0K/),
    ).toBeInTheDocument();

    // Confidence gauge shows the blended value as a meter.
    const gauge = screen.getByTestId("confidence-gauge");
    expect(gauge).toHaveAttribute("aria-valuenow", "0.84");
    expect(within(gauge).getByText("0.84")).toBeInTheDocument();
  });

  it("expands the explainability accordion to show confidence components + impact inputs", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <RecommendationCard recommendation={EMEA_RECOMMENDATION} />,
      { roles: ["operator"] },
    );

    expect(screen.queryByTestId("explainability-body")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("explainability-toggle"));

    const body = screen.getByTestId("explainability-body");
    // Confidence component breakdown (static prior).
    expect(within(body).getByText(/historical calibration/i)).toBeInTheDocument();
    expect(within(body).getByText("0.74")).toBeInTheDocument();
    expect(within(body).getByText(/calibration n/i)).toBeInTheDocument();
    // Impact inputs are the auditable facts the estimator used.
    expect(within(body).getByText(/daily loss/i)).toBeInTheDocument();
    expect(within(body).getByText(/recovery_flat/i)).toBeInTheDocument();
  });

  it("enables accept/reject for an operator (proposed status)", () => {
    renderWithProviders(
      <RecommendationCard recommendation={EMEA_RECOMMENDATION} />,
      { roles: ["operator"] },
    );
    expect(screen.getByTestId("accept-btn")).toBeEnabled();
    expect(screen.getByTestId("reject-btn")).toBeEnabled();
  });

  it("disables actions for a viewer (UX-only role guard)", () => {
    renderWithProviders(
      <RecommendationCard recommendation={EMEA_RECOMMENDATION} />,
      { roles: ["viewer"] },
    );
    expect(screen.getByTestId("accept-btn")).toBeDisabled();
    expect(screen.getByTestId("reject-btn")).toBeDisabled();
    expect(
      screen.getByText(/requires the operator or admin role/i),
    ).toBeInTheDocument();
  });
});
