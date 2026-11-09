"use client";

// The AI Reviews surface at the top of a QC report. A "Run / Re-run AI Review"
// trigger (only for llm_integration:use holders) sits above a minimizable panel
// of the run's existing review cards. Reads are open to anyone who can view the
// QC report; the whole surface is hidden unless an LLM provider is active for
// the org.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { usePermissions } from "@/hooks/usePermissions";
import { useResource } from "@/hooks/useResource";
import {
  AgentReviewSummary,
  ReviewCard,
  ReviewModal,
} from "@/components/agent-reviews/reviewItems";
import { SectionBuilderModal } from "@/components/agent-reviews/SectionBuilderModal";
import { Button } from "@/components/ui/Button";

interface ListResponse {
  items: AgentReviewSummary[];
}

function collapseKey(runId: number): string {
  return `qcAiReviewSection:${runId}:collapsed`;
}

export function QCAiReviewSection({ pipelineRunId }: { pipelineRunId: number }) {
  const { canAccess } = usePermissions();
  const canUse = canAccess("llm_integration", "use");

  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [triggerOpen, setTriggerOpen] = useState(false);

  // Restore the collapsed/expanded preference (default expanded).
  useEffect(() => {
    if (typeof window === "undefined") return;
    setCollapsed(window.localStorage.getItem(collapseKey(pipelineRunId)) === "1");
  }, [pipelineRunId]);

  // Availability and the review list are read independently so a transient
  // list error does not also hide the surface via a falsey "enabled". A failed
  // availability check is not a confirmed "off": it is reported, not hidden.
  const availability = useResource(
    "agent-review-availability",
    useCallback(
      () => api.get<{ enabled: boolean }>("/api/agent_reviews/availability"),
      [],
    ),
    { onError: (e) => logError("checking whether AI review is available", e) },
  );
  const enabled = availability.data ? availability.data.enabled === true : null;
  const availabilityFailed = availability.data === undefined && availability.error !== null;

  // Poll while a shown review is still running so its card resolves in place.
  const listPath = `/api/agent_reviews?entity_type=pipeline_run&entity_id=${pipelineRunId}&filter=all`;
  const [hasPending, setHasPending] = useState(false);
  const list = useResource(
    listPath,
    useCallback((signal: AbortSignal) => api.get<ListResponse>(listPath, { signal }), [listPath]),
    {
      poll: hasPending ? 3000 : false,
      onSuccess: (resp) =>
        setHasPending(
          Array.isArray(resp?.items) &&
            resp.items.some((i) => !i.dismissed && i.status === "pending"),
        ),
    },
  );
  const items: AgentReviewSummary[] = Array.isArray(list.data?.items) ? list.data.items : [];
  const error = triggerError ?? (list.error ? (list.error as Error).message : null);
  const refresh = useCallback(async () => {
    setTriggerError(null);
    await list.refresh();
  }, [list]);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(collapseKey(pipelineRunId), next ? "1" : "0");
      }
      return next;
    });
  }

  if (availabilityFailed) {
    return (
      <div
        data-testid="qc-ai-review-unavailable"
        role="status"
        className="mb-6 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 flex items-center justify-between gap-3"
      >
        <span>
          Whether AI review is available could not be checked. The technical detail is in the
          application logs.
        </span>
        <Button variant="secondary" size="sm" onClick={() => void availability.refresh()}>
          Retry
        </Button>
      </div>
    );
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
          <Button size="sm"
            onClick={() => setTriggerOpen(true)}>
            {triggerLabel}
          </Button>
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
            void refresh();
          }}
          onError={(m) => {
            setTriggerOpen(false);
            setTriggerError(m);
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
