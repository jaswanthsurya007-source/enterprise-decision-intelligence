/**
 * `useCopilot` — drives the streaming copilot chat over `POST /v1/copilot/chat`.
 *
 * The gateway proxies the L5 SSE stream byte-for-byte (see `proxy/copilot.py`), so
 * we POST the question with the dev-JWT bearer, read the streamed response body,
 * parse SSE frames incrementally, and Zod-validate each `data:` frame against the
 * shared `CopilotFrameSchema`. Invalid frames are dropped — no `any` reaches state.
 *
 * GROUNDED-ANSWER GUARANTEE (pin, §5.6/§9): `token` frames accumulate into
 * `narrative` (prose ONLY). Authoritative numbers live in the structured
 * `facts` / `citations` accumulators. This hook NEVER parses a number out of the
 * narrative text — it only concatenates it. B3 tests this.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useApiClient } from "../auth/useAuth";
import { GatewayPaths } from "../api/endpoints";
import {
  CopilotFrameSchema,
  type CopilotAnswer,
  type CopilotChatRequest,
  type CopilotFrame,
} from "../schemas/copilot";

const EMPTY_ANSWER: CopilotAnswer = {
  narrative: "",
  thinking: "",
  tools: [],
  facts: [],
  citations: [],
  usage: null,
  status: "streaming",
  stopReason: null,
  groundingPassed: null,
  error: null,
};

/** Fold one validated frame into the accumulating answer (pure). */
function reduceFrame(answer: CopilotAnswer, frame: CopilotFrame): CopilotAnswer {
  switch (frame.type) {
    case "token":
      // PROSE ONLY — never scanned for numbers.
      return { ...answer, narrative: answer.narrative + frame.text };
    case "thinking":
      return { ...answer, thinking: answer.thinking + frame.text };
    case "tool":
      return { ...answer, tools: mergeToolStep(answer.tools, frame.step) };
    case "fact":
      return { ...answer, facts: [...answer.facts, frame.fact] };
    case "citation":
      return { ...answer, citations: upsertCitation(answer.citations, frame.citation) };
    case "usage":
      return { ...answer, usage: frame.usage };
    case "done":
      return {
        ...answer,
        status: "done",
        stopReason: frame.stop_reason ?? answer.stopReason,
        groundingPassed: frame.grounding_passed ?? answer.groundingPassed,
      };
    case "error":
      return { ...answer, status: "error", error: frame.message };
    default:
      return answer;
  }
}

/** Tool steps stream as started→ok/error for the same tool+args; merge in place. */
function mergeToolStep(
  steps: CopilotAnswer["tools"],
  step: CopilotAnswer["tools"][number],
): CopilotAnswer["tools"] {
  const idx = steps.findIndex(
    (s) => s.tool === step.tool && JSON.stringify(s.args) === JSON.stringify(step.args),
  );
  if (idx === -1) return [...steps, step];
  const next = steps.slice();
  next[idx] = { ...next[idx], ...step };
  return next;
}

function upsertCitation(
  citations: CopilotAnswer["citations"],
  citation: CopilotAnswer["citations"][number],
): CopilotAnswer["citations"] {
  const without = citations.filter((c) => c.index !== citation.index);
  const next = [...without, citation];
  next.sort((a, b) => a.index - b.index);
  return next;
}

/** Find the earliest SSE record separator (\n\n / \r\n\r\n / \r\r). */
function nextSeparator(buf: string): { index: number; length: number } | null {
  const seps = [
    { index: buf.indexOf("\r\n\r\n"), length: 4 },
    { index: buf.indexOf("\n\n"), length: 2 },
    { index: buf.indexOf("\r\r"), length: 2 },
  ].filter((s) => s.index !== -1);
  if (seps.length === 0) return null;
  return seps.reduce((a, b) => (b.index < a.index ? b : a));
}

/** Extract the `data:` payload from one raw SSE event block (or null). */
function dataFromEvent(rawEvent: string): string | null {
  const lines = rawEvent.split(/\r\n|\r|\n/);
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    if (field !== "data") continue;
    let val = colon === -1 ? "" : line.slice(colon + 1);
    if (val.startsWith(" ")) val = val.slice(1);
    dataLines.push(val);
  }
  return dataLines.length ? dataLines.join("\n") : null;
}

export interface UseCopilotResult {
  answer: CopilotAnswer | null;
  isStreaming: boolean;
  /** Send a question; cancels any in-flight stream first. */
  ask: (question: string, conversationId?: string) => void;
  /** Abort the current stream. */
  cancel: () => void;
  /** Clear the answer (start a fresh turn). */
  reset: () => void;
}

export function useCopilot(): UseCopilotResult {
  const client = useApiClient();
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [isStreaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // Hold the accumulator in a ref so each frame folds onto the latest state
  // synchronously (avoids stale closures across async reads).
  const accRef = useRef<CopilotAnswer>(EMPTY_ANSWER);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  const reset = useCallback(() => {
    cancel();
    accRef.current = EMPTY_ANSWER;
    setAnswer(null);
  }, [cancel]);

  const ask = useCallback(
    (question: string, conversationId?: string) => {
      const q = question.trim();
      if (!q) return;
      abortRef.current?.abort();
      const abort = new AbortController();
      abortRef.current = abort;

      accRef.current = { ...EMPTY_ANSWER, status: "streaming" };
      setAnswer(accRef.current);
      setStreaming(true);

      const body: CopilotChatRequest = {
        question: q,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      };

      void (async () => {
        try {
          const res = await fetch(client.url(GatewayPaths.copilotChat), {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
              ...client.authHeaders(),
            },
            body: JSON.stringify(body),
            signal: abort.signal,
          });
          if (!res.ok || !res.body) {
            throw new Error(`Copilot request failed: HTTP ${res.status}`);
          }

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            for (;;) {
              const sep = nextSeparator(buffer);
              if (!sep) break;
              const rawEvent = buffer.slice(0, sep.index);
              buffer = buffer.slice(sep.index + sep.length);
              const data = dataFromEvent(rawEvent);
              if (data === null) continue;
              let json: unknown;
              try {
                json = JSON.parse(data);
              } catch {
                continue; // malformed frame — drop
              }
              const parsed = CopilotFrameSchema.safeParse(json);
              if (!parsed.success) {
                if (import.meta.env.DEV) {
                  console.warn("[copilot] dropped invalid frame", parsed.error.issues);
                }
                continue;
              }
              accRef.current = reduceFrame(accRef.current, parsed.data);
              setAnswer(accRef.current);
            }
          }
          // Stream ended without an explicit `done` frame — mark complete.
          if (accRef.current.status === "streaming") {
            accRef.current = { ...accRef.current, status: "done" };
            setAnswer(accRef.current);
          }
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          accRef.current = {
            ...accRef.current,
            status: "error",
            error: err instanceof Error ? err.message : "Copilot stream failed.",
          };
          setAnswer(accRef.current);
        } finally {
          if (abortRef.current === abort) {
            abortRef.current = null;
            setStreaming(false);
          }
        }
      })();
    },
    [client],
  );

  // Abort any in-flight stream on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  return { answer, isStreaming, ask, cancel, reset };
}
