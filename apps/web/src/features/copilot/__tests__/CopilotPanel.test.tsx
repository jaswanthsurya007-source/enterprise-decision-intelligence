/**
 * B3 #5 — THE GROUNDED-ANSWER GUARANTEE (pin, ARCHITECTURE §5.6 / §9).
 *
 * The copilot answer streams as `token` (prose), `fact`, `citation`, `usage`, and
 * `done` frames. The UI MUST render authoritative figures ONLY from the
 * structured `facts` / `citations` (and the embedded validated
 * Finding/Recommendation) — NEVER scrape a number out of the LLM narrative.
 *
 * The fixture stream deliberately embeds a FABRICATED "$999K" in the narrative
 * that exists in NO fact and NO citation. These tests assert:
 *   1. The authoritative numbers (from facts/citations) ARE shown.
 *   2. The fabricated $999K is NOT surfaced as an authoritative metric — it only
 *      survives inside the rendered narrative prose, never as a fact chip / a
 *      citation figure / a grounded-fact value.
 *
 * No backend: the copilot hook uses the global `fetch` directly, so we stub it
 * with a streamed SSE body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../../../test/utils";
import { CopilotPanel } from "../CopilotPanel";
import {
  COPILOT_FRAMES,
  COPILOT_FABRICATED_FIGURE,
  encodeSseFrames,
} from "../../../../test/fixtures";

/** Stream the demo copilot frames as an SSE body when `/v1/copilot/chat` is hit. */
function stubCopilotFetch(): void {
  const encoder = new TextEncoder();
  const body = encodeSseFrames(COPILOT_FRAMES);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const u = typeof input === "string" ? input : input.toString();
      if (!u.includes("/v1/copilot/chat")) {
        throw new Error(`unexpected fetch in copilot test: ${u}`);
      }
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(body));
          controller.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }),
  );
}

describe("CopilotPanel — grounded-answer guarantee", () => {
  beforeEach(() => stubCopilotFetch());
  afterEach(() => vi.unstubAllGlobals());

  async function askDemoQuestion() {
    const user = userEvent.setup();
    renderWithProviders(<CopilotPanel />);
    await user.click(
      screen.getByRole("button", { name: /Why did revenue drop last week\?/i }),
    );
    // Wait for the stream to complete (grounding verified line appears on done).
    await waitFor(
      () =>
        expect(
          screen.getByText(/Grounding verified/i),
        ).toBeInTheDocument(),
      { timeout: 4000 },
    );
  }

  it("renders authoritative figures from structured facts/citations", async () => {
    await askDemoQuestion();

    // Grounded-facts strip — values come from CopilotFact, not narrative text.
    const facts = screen.getByTestId("grounded-facts");
    expect(within(facts).getByText("-8.3% WoW")).toBeInTheDocument(); // fact.display
    expect(within(facts).getByText("~$170K / 5d")).toBeInTheDocument();

    // Inline citation chips position structured sources within the prose.
    expect(screen.getAllByTestId("citation-chip").length).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("surfaces a cited Finding's figures from its validated structured fields", async () => {
    const user = userEvent.setup();
    await askDemoQuestion();

    // Expand the Finding citation chip ([2]) and read the structured numbers.
    const chip = screen
      .getAllByTestId("citation-chip")
      .find((b) => b.textContent?.includes("[2]"));
    expect(chip).toBeDefined();
    await user.click(chip!);

    // observed/expected/deviation come from the embedded Finding (computed by
    // L3). The chip renders metric values without a unit hint -> compact "61K".
    expect(await screen.findByText("61K")).toBeInTheDocument(); // observed_value
    expect(screen.getByText("95K")).toBeInTheDocument(); // expected_value
    expect(screen.getByText("-35.8%")).toBeInTheDocument(); // deviation_pct
  });

  it("does NOT surface the fabricated narrative number as an authoritative metric", async () => {
    await askDemoQuestion();

    // The $999K appears ONLY inside the rendered narrative prose (the message
    // body) — never as a grounded-fact chip nor a citation figure.
    const facts = screen.getByTestId("grounded-facts");
    expect(within(facts).queryByText(/\$999K/)).not.toBeInTheDocument();

    // No citation chip detail exposes the fabricated figure.
    for (const chip of screen.getAllByTestId("citation-chip")) {
      expect(chip.textContent ?? "").not.toContain("$999K");
    }

    // The fabricated figure DOES render verbatim inside the narrative prose
    // (proving the test isn't trivially passing because it was dropped). Match
    // only the leaf element that directly owns the text node.
    const leaves = screen.queryAllByText(
      (content, node) => {
        if (!content.includes(COPILOT_FABRICATED_FIGURE)) return false;
        // Leaf = no child element also contains the text (own text node).
        const child = Array.from(node?.children ?? []).some((c) =>
          c.textContent?.includes(COPILOT_FABRICATED_FIGURE),
        );
        return !child;
      },
    );
    expect(leaves.length).toBeGreaterThanOrEqual(1);

    // Every place the figure appears is narrative prose — NEVER a fact chip or a
    // citation widget (those are the only authoritative-number surfaces).
    for (const el of leaves) {
      expect(el.closest('[data-testid="grounded-fact"]')).toBeNull();
      expect(el.closest('[data-testid="grounded-facts"]')).toBeNull();
      expect(el.closest('[data-testid="citation-chip"]')).toBeNull();
    }
  });

  it("renders the question and the prose narrative", async () => {
    await askDemoQuestion();
    const message = screen.getByTestId("copilot-message");
    expect(
      within(message).getByText(/Why did revenue drop last week\?/i),
    ).toBeInTheDocument();
    expect(
      within(message).getByText(/Revenue fell 8.3% week-over-week/i),
    ).toBeInTheDocument();
  });
});
