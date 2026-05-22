"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
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
  const [items, setItems] = useState<AgentReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.get<ListResponse>(
        `/api/agent_reviews?entity_type=${entityType}&entity_id=${entityId}&filter=${filter}`,
      );
      setItems(resp.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [entityType, entityId, filter]);

  useEffect(() => {
    refresh();
  }, [refresh, refreshSignal]);

  // Poll while any item is still pending so the card resolves without a reload.
  useEffect(() => {
    const hasPending = items.some((i) => i.status === "pending");
    if (!hasPending) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [items, refresh]);

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

      {loading && <div className="text-gray-500 text-sm">Loading...</div>}
      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">
          {error}
        </div>
      )}
      {!loading && items.length === 0 && (
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
