/**
 * SSE client built on `fetch` + a streamed body (NOT the native `EventSource`),
 * because `EventSource` cannot attach an `Authorization` header — and every
 * gateway call must carry the dev-JWT bearer (§5.6). It implements:
 *
 *  - an incremental SSE frame parser (`event:` / `data:` / `id:` / `:comment`),
 *  - a heartbeat watchdog (the gateway emits periodic `:hb` comments / `heartbeat`
 *    events; if none arrive within `heartbeatTimeoutMs`, we treat the link as dead
 *    and reconnect),
 *  - exponential backoff with jitter on reconnect (capped),
 *  - an `onReconnect` hook fired BEFORE re-opening so the provider can invalidate
 *    the REST snapshot and close any gap missed while disconnected.
 *
 * It does not eagerly connect: nothing happens until `start()` is called (so no
 * connection opens at module load / before mount).
 */
import type { ConnectionStatus } from "./events";

export interface SseFrame {
  event: string;
  data: string;
  id?: string;
}

export interface SseClientOptions {
  url: string;
  /** Header factory (re-evaluated per connection so a refreshed token applies). */
  headers: () => Record<string, string>;
  onFrame: (frame: SseFrame) => void;
  onStatus?: (status: ConnectionStatus) => void;
  /** Fired right before a RE-connect (not the first connect): refetch snapshot. */
  onReconnect?: () => void;
  onError?: (err: unknown) => void;
  /** Test seam. */
  fetchImpl?: typeof fetch;
  /** Backoff base (ms). Default 500. */
  backoffBaseMs?: number;
  /** Backoff cap (ms). Default 15000. */
  backoffMaxMs?: number;
  /** Dead-link watchdog (ms). Default 35000 (gateway hb interval is ~15s). */
  heartbeatTimeoutMs?: number;
}

const HEARTBEAT_EVENTS = new Set(["heartbeat", "hb", "ping"]);

export class SseClient {
  private readonly opts: Required<
    Omit<SseClientOptions, "onStatus" | "onReconnect" | "onError" | "fetchImpl">
  > &
    Pick<
      SseClientOptions,
      "onStatus" | "onReconnect" | "onError" | "fetchImpl"
    >;
  private readonly fetchImpl: typeof fetch;

  private abort: AbortController | null = null;
  private stopped = true;
  private attempt = 0;
  private hasConnectedOnce = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private lastEventId: string | undefined;
  private status: ConnectionStatus = "idle";

  constructor(options: SseClientOptions) {
    this.opts = {
      backoffBaseMs: 500,
      backoffMaxMs: 15000,
      heartbeatTimeoutMs: 35000,
      ...options,
    };
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  private setStatus(s: ConnectionStatus) {
    if (this.status === s) return;
    this.status = s;
    this.opts.onStatus?.(s);
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.abort?.abort();
    this.abort = null;
    this.setStatus("closed");
  }

  private clearTimers(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.heartbeatTimer) {
      clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private armHeartbeat(): void {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => {
      // No traffic within the window — consider the link dead and recycle it.
      this.abort?.abort();
    }, this.opts.heartbeatTimeoutMs);
  }

  private async connect(): Promise<void> {
    if (this.stopped) return;

    // A reconnect (not the first connect) should refetch the REST snapshot
    // first, so the cache reflects anything missed while we were disconnected.
    if (this.hasConnectedOnce) {
      this.setStatus("reconnecting");
      try {
        this.opts.onReconnect?.();
      } catch (err) {
        this.opts.onError?.(err);
      }
    } else {
      this.setStatus("connecting");
    }

    this.abort = new AbortController();
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
      "Cache-Control": "no-cache",
      ...this.opts.headers(),
    };
    if (this.lastEventId) headers["Last-Event-ID"] = this.lastEventId;

    try {
      const res = await this.fetchImpl(this.opts.url, {
        method: "GET",
        headers,
        signal: this.abort.signal,
      });
      if (!res.ok || !res.body) {
        throw new Error(`SSE connect failed: HTTP ${res.status}`);
      }

      this.hasConnectedOnce = true;
      this.attempt = 0;
      this.setStatus("open");
      this.armHeartbeat();

      await this.readStream(res.body);
      // Stream ended cleanly (server closed) — schedule a reconnect.
      this.scheduleReconnect();
    } catch (err) {
      if (this.stopped) return;
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        this.opts.onError?.(err);
      }
      this.scheduleReconnect();
    }
  }

  private async readStream(body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        // Any byte traffic counts as a heartbeat — re-arm the watchdog.
        this.armHeartbeat();
        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by a blank line; split greedily on any of
        // the three legal separators, keeping the trailing partial in `buffer`.
        for (;;) {
          const sep = nextSeparator(buffer);
          if (!sep) break;
          const rawEvent = buffer.slice(0, sep.index);
          buffer = buffer.slice(sep.index + sep.length);
          this.dispatch(rawEvent);
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  private dispatch(rawEvent: string): void {
    const lines = rawEvent.split(/\r\n|\r|\n/);
    let event = "message";
    const dataLines: string[] = [];
    let id: string | undefined;

    for (const line of lines) {
      if (line === "" || line.startsWith(":")) {
        // Comment line (`:hb`) — heartbeat only; watchdog already re-armed.
        continue;
      }
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let val = colon === -1 ? "" : line.slice(colon + 1);
      if (val.startsWith(" ")) val = val.slice(1);
      switch (field) {
        case "event":
          event = val;
          break;
        case "data":
          dataLines.push(val);
          break;
        case "id":
          id = val;
          break;
        default:
          break;
      }
    }

    if (id !== undefined) this.lastEventId = id;
    if (HEARTBEAT_EVENTS.has(event)) return; // heartbeat frame, no payload
    if (dataLines.length === 0) return;

    this.opts.onFrame({ event, data: dataLines.join("\n"), id });
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    this.clearTimers();
    const base = this.opts.backoffBaseMs;
    const max = this.opts.backoffMaxMs;
    const expo = Math.min(max, base * 2 ** this.attempt);
    const jitter = Math.random() * expo * 0.3;
    const delay = Math.min(max, expo + jitter);
    this.attempt += 1;
    this.setStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => void this.connect(), delay);
  }
}

/** Finds the earliest SSE event separator and its length, or null. */
function nextSeparator(
  buffer: string,
): { index: number; length: number } | null {
  const seps: Array<{ index: number; length: number }> = [
    { index: buffer.indexOf("\r\n\r\n"), length: 4 },
    { index: buffer.indexOf("\n\n"), length: 2 },
    { index: buffer.indexOf("\r\r"), length: 2 },
  ].filter((s) => s.index !== -1);
  if (seps.length === 0) return null;
  return seps.reduce((a, b) => (b.index < a.index ? b : a));
}
