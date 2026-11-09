"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { useResource } from "@/hooks/useResource";
import { StaleNotice } from "@/components/shared/StaleNotice";
import {
  AgentReviewEntityType,
  AgentReviewSummary,
  ReviewCard,
  ReviewModal,
} from "./reviewItems";

export type { AgentReviewEntityType };

type Filter = "active" | "dismissed" | "stale" | "failed";

interface ListResponse {
  items: AgentReviewSummary[];
}

function filterKeyFor(entityType: AgentReviewEntityType, entityId: number): string {
  return `agentReviewTab:${entityType}:${entityId}:filter`;
}

interface AgentReviewTabProps {
  entityType: AgentReviewEntityType;
  entityId: number;
  /** Incremented by the parent after a new review is dispatched so the tab
   * refetches without a page reload. */
  refreshSignal?: number;
}

export function AgentReviewTab({
  entityType,
  entityId,
  refreshSignal,
}: AgentReviewTabProps) {
  const { canAccess } = usePermissions();
  const canDismiss = canAccess("llm_integration", "use");
  const [filter, setFilter] = useState<Filter>("active");
  const [openId, setOpenId] = useState<number | null>(null);

  // Persist filter selection in localStorage per (entity, tab).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(filterKeyFor(entityType, entityId));
    if (stored === "active" || stored === "dismissed" || stored === "stale" || stored === "failed") {
      setFilter(stored);
    }
  }, [entityType, entityId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(filterKeyFor(entityType, entityId), filter);
  }, [filter, entityType, entityId]);

  // Keyed by entity and filter: only the current filter's answer is shown.
  const path = `/api/agent_reviews?entity_type=${entityType}&entity_id=${entityId}&filter=${filter}`;
  const list = useResource(
    path,
    useCallback((signal: AbortSignal) => api.get<ListResponse>(path, { signal }), [path]),
    {
      // Poll while any item is still pending so the card resolves without a reload.
      poll: ({ data }) => (data?.items.some((i) => i.status === "pending") ? 3000 : false),
    },
  );
  const items: AgentReviewSummary[] = list.data?.items ?? [];
  const loading = list.loading;
  const error = list.error && !list.stale ? (list.error as Error).message : null;
  const refresh = list.refresh;

  // The parent bumps refreshSignal after dispatching a review; the first value is the mount's.
  const lastSignal = useRef(refreshSignal);
  useEffect(() => {
    if (lastSignal.current === refreshSignal) return;
    lastSignal.current = refreshSignal;
    void refresh();
  }, [refreshSignal, refresh]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {(["active", "dismissed", "stale", "failed"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded-full text-sm border ${
              filter === f
                ? "bg-bioaf-600 text-white border-bioaf-600"
                : "bg-white text-gray-700 border-gray-300"
            }`}
          >
            {f[0].toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {list.stale && <StaleNotice what="Reviews" onRetry={() => void refresh()} />}
      {loading && <div className="text-gray-500 text-sm">Loading...</div>}
      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">
          {error}
        </div>
      )}
      {!loading && !error && items.length === 0 && (
        <div className="text-gray-500 text-sm">No reviews match this filter.</div>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <ReviewCard
            key={item.id}
            review={item}
            onOpen={() => setOpenId(item.id)}
          />
        ))}
      </div>

      {openId !== null && (
        <ReviewModal
          reviewId={openId}
          canDismiss={canDismiss}
          onClose={() => setOpenId(null)}
          onMutated={refresh}
        />
      )}
    </div>
  );
}
