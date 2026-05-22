"use client";

// The AI Reviews surface at the top of a QC report. A "Run / Re-run AI Review"
// trigger (only for llm_integration:use holders) sits above a minimizable panel
// of the run's existing review cards. Reads are open to anyone who can view the
// QC report; the whole surface is hidden unless an LLM provider is active for
// the org.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import {
  AgentReviewSummary,
  ReviewCard,
  ReviewModal,
} from "@/components/agent-reviews/reviewItems";
import { SectionBuilderModal } from "@/components/agent-reviews/SectionBuilderModal";

interface ListResponse {
  items: AgentReviewSummary[];
}

function collapseKey(runId: number): string {
  return `qcAiReviewSection:${runId}:collapsed`;
}

export function QCAiReviewSection({ pipelineRunId }: { pipelineRunId: number }) {
  const { canAccess } = usePermissions();
  const canUse = canAccess("llm_integration", "use");

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [items, setItems] = useState<AgentReviewSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [triggerOpen, setTriggerOpen] = useState(false);

  // Restore the collapsed/expanded preference (default expanded).
  useEffect(() => {
    if (typeof window === "undefined") return;
    setCollapsed(window.localStorage.getItem(collapseKey(pipelineRunId)) === "1");
  }, [pipelineRunId]);

  const refresh = useCallback(async () => {
    // Availability and the review list are fetched independently so a transient
    // list error does not also hide the surface via a falsey "enabled".
    try {
      const avail = await api.get<{ enabled: boolean }>("/api/agent_reviews/availability");
      setEnabled(avail.enabled);
    } catch {
      setEnabled(false);
    }
    setError(null);
    try {
      const list = await api.get<ListResponse>(
        `/api/agent_reviews?entity_type=pipeline_run&entity_id=${pipelineRunId}&filter=all`,
      );
      setItems(list.items);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [pipelineRunId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while a shown review is still running so its card resolves in place.
  useEffect(() => {
    const hasPending = items.some((i) => !i.dismissed && i.status === "pending");
    if (!hasPending) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [items, refresh]);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(collapseKey(pipelineRunId), next ? "1" : "0");
      }
      return next;
    });
  }

  // Hidden entirely unless AI Review is enabled for the org (active provider).
  if (enabled !== true) return null;

  const cards = items.filter((i) => !i.dismissed);
  const everReviewed = items.length > 0;
  const triggerLabel = everReviewed ? "Re-run AI Review" : "Run AI Review";

  // Nothing to show for a viewer (no trigger) when there are no cards or errors.
  if (!canUse && cards.length === 0 && !error) return null;

  return (
    <div className="mb-6 space-y-3" data-testid="qc-ai-review-section">
      {canUse && (
        <div>
          <button
            onClick={() => setTriggerOpen(true)}
            className="px-3 py-1.5 text-sm bg-bioaf-600 hover:bg-bioaf-700 text-white rounded"
          >
            {triggerLabel}
          </button>
        </div>
      )}

      {error && (
        <div className="text-red-600 text-sm bg-red-50 border border-red-200 rounded p-3">
          {error}
        </div>
      )}

      {cards.length > 0 && (
        <div className="border border-gray-200 rounded-lg bg-gray-50">
          <button
            onClick={toggleCollapsed}
            aria-expanded={!collapsed}
            className="w-full flex items-center justify-between px-4 py-2 text-left"
          >
            <span className="font-medium text-gray-900">AI Reviews ({cards.length})</span>
            <span className="text-gray-500" aria-hidden="true">
              {collapsed ? "▸" : "▾"}
            </span>
          </button>
          {!collapsed && (
            <div className="px-3 pb-3 space-y-3">
              {cards.map((c) => (
                <ReviewCard key={c.id} review={c} onOpen={() => setOpenId(c.id)} />
              ))}
            </div>
          )}
        </div>
      )}

      {triggerOpen && (
        <SectionBuilderModal
          entityType="pipeline_run"
          runId={pipelineRunId}
          experimentId={null}
          onCancel={() => setTriggerOpen(false)}
          onSubmitted={() => {
            setTriggerOpen(false);
            refresh();
          }}
          onError={(m) => {
            setTriggerOpen(false);
            setError(m);
          }}
        />
      )}

      {openId !== null && (
        <ReviewModal
          reviewId={openId}
          canDismiss={canUse}
          onClose={() => setOpenId(null)}
          onMutated={refresh}
        />
      )}
    </div>
  );
}
