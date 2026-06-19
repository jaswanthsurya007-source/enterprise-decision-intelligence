/**
 * `useRecommendations` — the prioritized recommendation list (`GET
 * /v1/recommendations`, priority-sorted server-side) patched by realtime
 * `recommendation` frames, plus `useRecommendationAction` for the accept/reject
 * write surface.
 *
 * Live frames upsert by `recommendation_id` and re-sort by `priority_rank`
 * (best/lowest rank first), so the rank-1 action stays at the head. The mutation
 * POSTs the lifecycle transition through the gateway and optimistically reflects
 * the returned, validated `Recommendation`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApiClient } from "../auth/useAuth";
import { actOnRecommendation, getRecommendations } from "../api/endpoints";
import { QueryKeys } from "../realtime/events";
import { useSubscription } from "../realtime/useSubscription";
import type { Recommendation, RecommendationList } from "../schemas/recommendation";

/** Upsert by id, then sort by priority_rank asc, priority_score desc, newest. */
function upsert(
  list: RecommendationList,
  rec: Recommendation,
): RecommendationList {
  const without = list.filter(
    (r) => r.recommendation_id !== rec.recommendation_id,
  );
  const next = [...without, rec];
  next.sort((a, b) => {
    if (a.priority_rank !== b.priority_rank)
      return a.priority_rank - b.priority_rank;
    if (a.priority_score !== b.priority_score)
      return b.priority_score - a.priority_score;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
  return next;
}

export function useRecommendations() {
  const client = useApiClient();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: QueryKeys.recommendations,
    queryFn: ({ signal }) => getRecommendations(client, signal),
    staleTime: 10_000,
  });

  useSubscription("recommendations", (rec) => {
    queryClient.setQueryData<RecommendationList>(
      QueryKeys.recommendations,
      (prev) => upsert(prev ?? [], rec),
    );
  });

  return query;
}

export interface RecommendationActionVars {
  id: string;
  action: "accept" | "reject";
  notes?: string;
}

/**
 * Accept/reject a recommendation. On success the returned (validated) record is
 * merged into the cached list so the card reflects its new status immediately,
 * without waiting for the lifecycle SSE frame to round-trip.
 */
export function useRecommendationAction() {
  const client = useApiClient();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, action, notes }: RecommendationActionVars) =>
      actOnRecommendation(client, id, action, notes),
    onSuccess: (updated) => {
      queryClient.setQueryData<RecommendationList>(
        QueryKeys.recommendations,
        (prev) => upsert(prev ?? [], updated),
      );
    },
  });
}
