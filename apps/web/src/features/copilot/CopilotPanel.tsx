/**
 * CopilotPanel — the grounded Q&A surface. Drives `useCopilot` (streams the
 * `/v1/copilot/chat` SSE), renders the streamed answer via `Message` (prose +
 * citation chips + grounded-facts strip + tool trace), and offers a question
 * input with example prompts.
 *
 * The panel keeps the last question alongside the live answer so the `Message`
 * shows both. Numbers are rendered only from structured facts/citations.
 */
import { useState } from "react";
import { useCopilot } from "../../query/useCopilot";
import { Message } from "./Message";

const EXAMPLES = [
  "Why did revenue drop last week?",
  "What should we do about the EMEA anomaly?",
  "Which region is underperforming?",
];

export function CopilotPanel() {
  const { answer, isStreaming, ask, cancel } = useCopilot();
  const [input, setInput] = useState("");
  const [asked, setAsked] = useState<string | null>(null);

  const submit = (question: string) => {
    const q = question.trim();
    if (!q || isStreaming) return;
    setAsked(q);
    setInput("");
    ask(q);
  };

  return (
    <div className="flex h-full flex-col gap-4" data-testid="copilot-panel">
      <div className="card flex min-h-[320px] flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="inline-block h-2 w-2 rounded-sm bg-accent" />
            <h2 className="text-sm font-semibold text-fg-default">Copilot</h2>
            <span className="text-2xs text-fg-subtle">grounded · cited</span>
          </div>
          {isStreaming && (
            <button
              type="button"
              onClick={cancel}
              className="focus-ring rounded-md border border-border-strong px-2.5 py-1 text-2xs text-fg-muted hover:bg-surface-overlay hover:text-fg-default"
            >
              Stop
            </button>
          )}
        </div>

        <div className="flex-1 overflow-auto p-4">
          {asked && answer ? (
            <Message question={asked} answer={answer} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <p className="max-w-sm text-sm text-fg-muted">
                Ask about your operations. Every figure in the answer is grounded
                in computed facts and cited — never invented.
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => submit(ex)}
                    className="focus-ring rounded-full border border-border-subtle bg-surface-inset px-3 py-1.5 text-2xs text-fg-muted hover:border-accent/40 hover:text-fg-default"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the copilot…"
          aria-label="Ask the copilot"
          className="focus-ring flex-1 rounded-md border border-border-strong bg-surface-inset px-3 py-2 text-sm text-fg-default placeholder:text-fg-subtle"
        />
        <button
          type="submit"
          disabled={isStreaming || input.trim().length === 0}
          className="focus-ring rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-fg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Ask
        </button>
      </form>
    </div>
  );
}
