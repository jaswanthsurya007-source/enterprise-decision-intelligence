/**
 * Message — renders one copilot answer.
 *
 * GROUNDED-ANSWER GUARANTEE (pin §5.6/§9): the narrative is rendered as PROSE.
 * Inline `[n]` markers are matched to the STRUCTURED `citations` array and
 * rendered as `CitationChip`s — the chip's figures come from the embedded,
 * validated `Finding`/`Recommendation`. The "grounded facts" strip renders
 * `CopilotFact.value` (computed upstream). At NO point does this component parse a
 * number out of `narrative` text and present it as an authoritative metric.
 *
 * The `[n]` regex is used ONLY to position a citation chip (a structured-source
 * affordance); it never extracts a numeric value.
 */
import { Fragment, type ReactNode } from "react";
import { CitationChip } from "./CitationChip";
import { ToolTrace } from "./ToolTrace";
import { formatFact } from "../../lib/format";
import type { CopilotAnswer, CopilotCitation } from "../../schemas/copilot";

/** Split narrative on `[n]` markers; interleave the matched citation chips. */
function renderNarrative(
  narrative: string,
  citations: CopilotCitation[],
): ReactNode[] {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  const parts: ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(narrative)) !== null) {
    const text = narrative.slice(last, m.index);
    if (text) parts.push(<Fragment key={`t${key++}`}>{text}</Fragment>);
    const idx = Number(m[1]);
    const citation = byIndex.get(idx);
    if (citation) {
      parts.push(
        <span key={`c${key++}`} className="mx-0.5 inline-block align-baseline">
          <CitationChip citation={citation} />
        </span>,
      );
    } else {
      // Unknown marker: keep the literal text (still prose, no number scraped).
      parts.push(<Fragment key={`t${key++}`}>{m[0]}</Fragment>);
    }
    last = re.lastIndex;
  }
  const tail = narrative.slice(last);
  if (tail) parts.push(<Fragment key={`t${key++}`}>{tail}</Fragment>);
  return parts;
}

export interface MessageProps {
  question: string;
  answer: CopilotAnswer;
}

export function Message({ question, answer }: MessageProps) {
  const orphanCitations = answer.citations.filter(
    (c) => !new RegExp(`\\[${c.index}\\]`).test(answer.narrative),
  );

  return (
    <div className="space-y-3" data-testid="copilot-message">
      {/* Question */}
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg rounded-br-sm bg-surface-overlay px-3 py-2 text-sm text-fg-default">
          {question}
        </div>
      </div>

      {/* Grounded facts strip — authoritative numbers ONLY from structured facts. */}
      {answer.facts.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="grounded-facts">
          {answer.facts.map((fact) => (
            <div
              key={fact.id}
              className="flex items-baseline gap-1.5 rounded-md border border-border-subtle bg-surface-inset px-2 py-1"
              data-testid="grounded-fact"
            >
              <span className="text-2xs text-fg-subtle">{fact.label}</span>
              <span className="kpi-figure text-xs text-fg-default">
                {fact.display ?? formatFact(fact.value, fact.unit)}
              </span>
              {fact.source_tool && (
                <span className="text-2xs text-fg-subtle">
                  · {fact.source_tool}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {answer.tools.length > 0 && <ToolTrace steps={answer.tools} />}

      {/* Narrative prose with inline citation chips. */}
      <div className="rounded-lg rounded-tl-sm border border-border-subtle bg-surface-raised px-3 py-2">
        {answer.narrative ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg-default">
            {renderNarrative(answer.narrative, answer.citations)}
          </p>
        ) : answer.status === "streaming" ? (
          <p className="text-sm text-fg-subtle">Thinking…</p>
        ) : null}

        {answer.status === "error" && (
          <p className="mt-1 text-xs text-status-critical">
            {answer.error ?? "The copilot stream failed."}
          </p>
        )}

        {/* Citations not inlined in the prose still get a chip list. */}
        {orphanCitations.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 border-t border-border-subtle pt-2">
            {orphanCitations.map((c) => (
              <CitationChip key={c.index} citation={c} />
            ))}
          </div>
        )}

        {answer.status === "done" && answer.groundingPassed !== null && (
          <div className="mt-2 flex items-center gap-1.5 border-t border-border-subtle pt-2 text-2xs">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${answer.groundingPassed ? "bg-status-ok" : "bg-status-warn"}`}
              aria-hidden
            />
            <span className="text-fg-subtle">
              {answer.groundingPassed
                ? "Grounding verified — every figure traces to a cited source."
                : "Grounding incomplete — some figures were stripped."}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
