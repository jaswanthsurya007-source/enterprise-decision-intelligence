/**
 * Hooks to consume realtime channels.
 *
 * `useSubscription(channel, handler)` registers a validated-payload listener for
 * the lifetime of the component. `useRealtimeStatus()` exposes per-channel
 * connection state for the topbar live indicator.
 */
import { useContext, useEffect, useRef } from "react";
import { RealtimeContext, type RealtimeApi } from "./RealtimeProvider";
import type { ChannelPayloadMap, RealtimeChannel } from "./events";

function useRealtime(): RealtimeApi {
  const ctx = useContext(RealtimeContext);
  if (!ctx) {
    throw new Error("useSubscription must be used within a <RealtimeProvider>");
  }
  return ctx;
}

/**
 * Subscribe to a realtime channel. The handler may change every render without
 * re-subscribing (kept in a ref), so callers need not memoize it.
 */
export function useSubscription<C extends RealtimeChannel>(
  channel: C,
  handler: (payload: ChannelPayloadMap[C]) => void,
): void {
  const { subscribe } = useRealtime();
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const unsubscribe = subscribe(channel, (payload) => {
      handlerRef.current(payload);
    });
    return unsubscribe;
  }, [channel, subscribe]);
}

export function useRealtimeStatus() {
  return useRealtime().status;
}
