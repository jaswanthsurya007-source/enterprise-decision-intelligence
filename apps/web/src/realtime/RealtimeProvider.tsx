/**
 * RealtimeProvider — owns the live SSE links to the gateway (metrics, anomalies,
 * recommendations) and bridges validated frames to (a) the TanStack Query cache
 * via `setQueryData` and (b) per-channel subscribers (`useSubscription`).
 *
 * Lifecycle: connections open on MOUNT (in an effect) and only when an auth
 * token is present — never at module load, never eagerly. On reconnect each
 * client invalidates its REST snapshot query so the cache closes any gap missed
 * while disconnected (§5.6). Every `data:` frame is Zod-validated against the
 * shared contract for that channel; invalid frames are dropped (and reported),
 * so no `any` reaches a subscriber.
 */
import {
  createContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/useAuth";
import { SseClient } from "./sseClient";
import {
  CHANNELS,
  type ChannelPayloadMap,
  type ConnectionStatus,
  type RealtimeChannel,
} from "./events";

type Listener<C extends RealtimeChannel> = (payload: ChannelPayloadMap[C]) => void;

export interface RealtimeApi {
  status: Record<RealtimeChannel, ConnectionStatus>;
  subscribe: <C extends RealtimeChannel>(
    channel: C,
    listener: Listener<C>,
  ) => () => void;
}

export const RealtimeContext = createContext<RealtimeApi | null>(null);

const CHANNEL_NAMES: RealtimeChannel[] = [
  "metrics",
  "anomalies",
  "recommendations",
];

export interface RealtimeProviderProps {
  children: ReactNode;
  /** Disable auto-connect (tests drive clients manually). Default false. */
  autoConnect?: boolean;
  fetchImpl?: typeof fetch;
}

export function RealtimeProvider({
  children,
  autoConnect = true,
  fetchImpl,
}: RealtimeProviderProps) {
  const { client, getToken, isAuthenticated } = useAuth();
  const queryClient = useQueryClient();

  const [status, setStatus] = useState<Record<RealtimeChannel, ConnectionStatus>>({
    metrics: "idle",
    anomalies: "idle",
    recommendations: "idle",
  });

  // Per-channel listener registries (stable across renders).
  const listenersRef = useRef<{
    [C in RealtimeChannel]: Set<Listener<C>>;
  }>({
    metrics: new Set(),
    anomalies: new Set(),
    recommendations: new Set(),
  });

  const api = useMemo<RealtimeApi>(
    () => ({
      status,
      subscribe: (channel, listener) => {
        const set = listenersRef.current[channel] as Set<typeof listener>;
        set.add(listener);
        return () => {
          set.delete(listener);
        };
      },
    }),
    [status],
  );

  useEffect(() => {
    if (!autoConnect || !isAuthenticated) return;

    const clients = CHANNEL_NAMES.map((channel) => {
      const def = CHANNELS[channel];
      const sse = new SseClient({
        url: client.url(def.path),
        headers: () => {
          const token = getToken();
          const h: Record<string, string> = {};
          if (token) h.Authorization = `Bearer ${token}`;
          return h;
        },
        ...(fetchImpl ? { fetchImpl } : {}),
        onStatus: (s) =>
          setStatus((prev) =>
            prev[channel] === s ? prev : { ...prev, [channel]: s },
          ),
        // On reconnect, refetch the REST snapshot to close the gap.
        onReconnect: () => {
          void queryClient.invalidateQueries({ queryKey: def.snapshotKey });
        },
        onFrame: (frame) => {
          let json: unknown;
          try {
            json = JSON.parse(frame.data);
          } catch {
            return; // malformed JSON — drop
          }
          const parsed = def.schema.safeParse(json);
          if (!parsed.success) {
            if (import.meta.env.DEV) {
              console.warn(
                `[realtime:${channel}] dropped invalid frame`,
                parsed.error.issues,
              );
            }
            return;
          }
          const payload = parsed.data as ChannelPayloadMap[typeof channel];
          for (const listener of listenersRef.current[channel]) {
            (listener as Listener<typeof channel>)(payload);
          }
        },
        onError: (err) => {
          if (import.meta.env.DEV) {
            console.warn(`[realtime:${channel}] error`, err);
          }
        },
      });
      sse.start();
      return sse;
    });

    return () => {
      for (const c of clients) c.stop();
    };
    // Reconnect wholesale when identity/transport changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoConnect, isAuthenticated, client, getToken, queryClient, fetchImpl]);

  return (
    <RealtimeContext.Provider value={api}>{children}</RealtimeContext.Provider>
  );
}
