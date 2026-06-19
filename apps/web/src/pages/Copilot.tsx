/**
 * Copilot page — hosts the CopilotPanel (streamed grounded Q&A).
 *
 * The grounded-answer guarantee (numbers only from structured facts/citations,
 * never scraped from narrative) is encoded in `schemas/copilot.ts` and enforced
 * by the panel + `Message` rendering. B3 tests it.
 */
import { CopilotPanel } from "../features/copilot/CopilotPanel";

export function CopilotPage() {
  return (
    <div className="flex h-full flex-col gap-4">
      <h1 className="text-lg font-semibold tracking-tight text-fg-default">
        Copilot
      </h1>
      <div className="min-h-0 flex-1">
        <CopilotPanel />
      </div>
    </div>
  );
}
