/**
 * ToolTrace — the agent's tool-use trace (the `display:"summarized"` reasoning
 * surface, §5.5). Each step shows the tool name, its args, a status dot, and an
 * optional summary. Purely informational — it never sources an authoritative
 * number for the answer.
 */
import type { CopilotToolStep } from "../../schemas/copilot";

const STATUS_DOT: Record<CopilotToolStep["status"], string> = {
  started: "bg-status-warn animate-pulse",
  ok: "bg-status-ok",
  error: "bg-status-critical",
};

function argSummary(args: Record<string, unknown>): string {
  const entries = Object.entries(args);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

export interface ToolTraceProps {
  steps: CopilotToolStep[];
}

export function ToolTrace({ steps }: ToolTraceProps) {
  if (steps.length === 0) return null;

  return (
    <div className="rounded-md border border-border-subtle bg-surface-inset p-2" data-testid="tool-trace">
      <div className="mb-1.5 text-2xs uppercase tracking-wide text-fg-subtle">
        Tool trace
      </div>
      <ol className="space-y-1">
        {steps.map((step, i) => (
          <li key={i} className="flex items-start gap-2 text-2xs">
            <span
              className={`mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[step.status]}`}
              aria-hidden
            />
            <div className="min-w-0">
              <span className="font-mono text-fg-default">{step.tool}</span>
              {argSummary(step.args) && (
                <span className="font-mono text-fg-subtle">
                  ({argSummary(step.args)})
                </span>
              )}
              {step.summary && (
                <span className="ml-1 text-fg-muted">— {step.summary}</span>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
