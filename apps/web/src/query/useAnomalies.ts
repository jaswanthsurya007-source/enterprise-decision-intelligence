/**
 * `useAnomalies` — the anomaly feed, fetched from `GET /v1/anomalies` (a list of
 * shared `Finding` payloads) and patched by realtime `anomaly` frames.
 *
 * A live `Finding` frame is upserted by `finding_id` (newest first); if the id is
 * already present (a re-narration / status change) it replaces the existing row.
 * On reconnect the provider invalidates this query so the feed re-syncs.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useApiClient } from "../auth/useAuth";
import { getAnomalies } from "../api/endpoints";
import { QueryKeys } from "../realtime/events";
import { useSubscription } from "../realtime/useSubscription";
import type { AnomalyList, Finding } from "../schemas/anomaly";

const FEED_MAX = 100;

/** Upsert a finding into the feed, newest first, de-duped by id. */
function upsert(feed: AnomalyList, finding: Finding): AnomalyList {
  const without = feed.filter((f) => f.finding_id !== finding.finding_id);
  const next = [finding, ...without];
  next.sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
  return next.slice(0, FEED_MAX);
}

export function useAnomalies() {
  const client = useApiClient();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: QueryKeys.anomalies,
    queryFn: ({ signal }) => getAnomalies(client, signal),
    staleTime: 10_000,
  });

  useSubscription("anomalies", (finding) => {
    queryClient.setQueryData<AnomalyList>(QueryKeys.anomalies, (prev) =>
      upsert(prev ?? [], finding),
    );
  });

  return query;
}
