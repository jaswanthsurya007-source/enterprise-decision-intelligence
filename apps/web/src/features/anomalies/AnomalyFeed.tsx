/**
 * AnomalyFeed — the live feed of detected findings with a drill-in to the
 * RootCausePanel. The feed (left) is a scrollable, severity-dotted list patched
 * by realtime `anomaly` frames; selecting a row shows its RCA on the right.
 *
 * The first finding is auto-selected so the demo's EMEA level-shift and its
 * candidate causes are visible on load without a click.
 */
import { useEffect, useState } from "react";
import { useAnomalies } from "../../query/useAnomalies";
import { AnomalyRow } from "./AnomalyRow";
import { RootCausePanel } from "./RootCausePanel";
import type { Anomaly } from "../../schemas/anomaly";

export function AnomalyFeed() {
  const { data, isLoading, isError, error, refetch } = useAnomalies();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const feed = data ?? [];
  // Auto-select the newest finding once data arrives (or when the selection
  // falls out of the feed), so the RCA panel is never empty when findings exist.
  useEffect(() => {
    if (feed.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    const stillPresent = feed.some((f) => f.finding_id === selectedId);
    if (!stillPresent) setSelectedId(feed[0]!.finding_id);
  }, [feed, selectedId]);

  const selected: Anomaly | null =
    feed.find((f) => f.finding_id === selectedId) ?? null;

  return (
    <section className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(280px,1fr)_1.4fr]">
      <div className="card flex max-h-[420px] flex-col">
        <div className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
          <h2 className="text-sm font-semibold text-fg-default">Anomaly feed</h2>
          <span className="text-2xs text-fg-subtle">{feed.length} open</span>
        </div>
        <div className="flex-1 overflow-auto p-2">
          {isLoading ? (
            <div className="space-y-2" aria-hidden>
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="h-12 animate-pulse rounded-md bg-surface-overlay"
                />
              ))}
            </div>
          ) : isError ? (
            <div className="p-2 text-xs text-status-critical">
              <p>Could not load anomalies.</p>
              <p className="mt-1 text-2xs text-fg-subtle">
                {error instanceof Error ? error.message : "Unknown error."}
              </p>
              <button
                type="button"
                onClick={() => void refetch()}
                className="focus-ring mt-2 rounded-md border border-border-strong px-2.5 py-1 text-2xs text-fg-muted hover:bg-surface-overlay hover:text-fg-default"
              >
                Retry
              </button>
            </div>
          ) : feed.length === 0 ? (
            <p className="p-3 text-xs text-fg-subtle">
              No anomalies detected. The feed updates live.
            </p>
          ) : (
            <div className="space-y-1">
              {feed.map((anomaly) => (
                <AnomalyRow
                  key={anomaly.finding_id}
                  anomaly={anomaly}
                  selected={anomaly.finding_id === selectedId}
                  onSelect={(a) => setSelectedId(a.finding_id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <RootCausePanel anomaly={selected} />
    </section>
  );
}
