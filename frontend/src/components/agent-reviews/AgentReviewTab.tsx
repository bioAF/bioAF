"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

export type AgentReviewEntityType = "pipeline_run" | "experiment";

type Severity = "red" | "orange" | "green" | "unknown" | null;
type Status = "pending" | "succeeded" | "failed";
type Filter = "active" | "dismissed" | "stale" | "failed";

interface AgentReviewSummary {
  id: number;
  entity_type: string;
  entity_id: number;
  included_run_ids: number[] | null;
  review_type: string;
  provider: string;
  model: string;
  status: Status;
  severity: Severity;
  headline: string | null;
  stale: boolean;
  dismissed: boolean;
  prompt_source: string | null;
  created_at: string;
  completed_at: string | null;
}

interface AgentReviewDetail extends AgentReviewSummary {
  flags: { title: string; body: string; severity: string }[] | null;
  evidence: string[] | null;
  body: string | null;
  error_text: string | null;
  artifact_gcs_paths: string[];
  dismissed_at: string | null;
  dismissed_by_user_id: number | null;
  prompt_text: string | null;
  prompt_sections: string[] | null;
  prompt_source: string | null;
  prompt_custom_id: number | null;
}

interface ListResponse {
  items: AgentReviewSummary[];
}

function filterKeyFor(entityType: AgentReviewEntityType, entityId: number): string {
  return `agentReviewTab:${entityType}:${entityId}:filter`;
}

interface AgentReviewTabProps {
  entityType: AgentReviewEntityType;
  entityId: number;
}

export function AgentReviewTab({ entityType, entityId }: AgentReviewTabProps) {
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
  }, [refresh]);

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

function severityClass(review: AgentReviewSummary): {
  bar: string;
  badge: string;
  badgeLabel: string;
} {
  // Always render one of red/orange/green per the user spec. Failed = red;
  // pending and parse-failure "unknown" severities map to orange.
  if (review.status === "failed") {
    return { bar: "bg-red-500", badge: "bg-red-100 text-red-700", badgeLabel: "red" };
  }
  if (review.status === "pending") {
    return {
      bar: "bg-amber-500",
      badge: "bg-amber-100 text-amber-700",
      badgeLabel: "orange",
    };
  }
  if (review.severity === "green") {
    return {
      bar: "bg-emerald-500",
      badge: "bg-emerald-100 text-emerald-700",
      badgeLabel: "green",
    };
  }
  if (review.severity === "red") {
    return { bar: "bg-red-500", badge: "bg-red-100 text-red-700", badgeLabel: "red" };
  }
  return {
    bar: "bg-amber-500",
    badge: "bg-amber-100 text-amber-700",
    badgeLabel: "orange",
  };
}

function shortTitle(headline: string | null): string {
  if (!headline) return "(no headline)";
  const words = headline.trim().split(/\s+/);
  if (words.length <= 7) return headline;
  return words.slice(0, 7).join(" ") + "…";
}

function ReviewCard({
  review,
  onOpen,
}: {
  review: AgentReviewSummary;
  onOpen: () => void;
}) {
  const { bar, badge, badgeLabel } = severityClass(review);
  const reviewTypeLabel =
    review.entity_type === "experiment" ? "Experiment review" : "Pipeline run review";
  const isCustom =
    review.prompt_source === "custom_saved" || review.prompt_source === "custom_one_off";

  return (
    <button
      onClick={onOpen}
      className="w-full text-left flex items-stretch bg-white rounded-lg shadow hover:shadow-md transition"
    >
      <div className={`w-1.5 rounded-l-lg ${bar}`} />
      <div className="flex-1 p-4">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>{new Date(review.created_at).toLocaleString()}</span>
          <span>·</span>
          <span>{reviewTypeLabel}</span>
          <span className={`ml-auto px-2 py-0.5 rounded text-xs font-medium ${badge}`}>
            {badgeLabel}
          </span>
        </div>
        <div className="mt-1 font-medium text-gray-900 truncate">
          {review.status === "pending"
            ? `Running on ${review.provider}…`
            : shortTitle(review.headline)}
        </div>
        <div className="mt-1 flex items-center gap-2 text-xs text-gray-500 flex-wrap">
          <span>{review.provider} · {review.model}</span>
          {isCustom && (
            <span className="px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">
              custom
            </span>
          )}
          {review.stale && (
            <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
              stale
            </span>
          )}
          {review.status === "failed" && (
            <span className="px-1.5 py-0.5 rounded bg-red-100 text-red-700">
              failed
            </span>
          )}
          {review.dismissed && (
            <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
              dismissed
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

interface SubItemLabel {
  id: string;
  label: string;
  section_label: string;
}

interface SectionCatalog {
  sections: {
    id: string;
    label: string;
    sub_items: { id: string; label: string }[];
  }[];
}

let _catalogCache: SectionCatalog | null = null;

async function loadCatalog(): Promise<SectionCatalog> {
  if (_catalogCache) return _catalogCache;
  _catalogCache = await api.get<SectionCatalog>("/api/agent_reviews/section_catalog");
  return _catalogCache;
}

function ReviewModal({
  reviewId,
  canDismiss,
  onClose,
  onMutated,
}: {
  reviewId: number;
  canDismiss: boolean;
  onClose: () => void;
  onMutated: () => void;
}) {
  const [review, setReview] = useState<AgentReviewDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<SectionCatalog | null>(null);

  useEffect(() => {
    loadCatalog().then(setCatalog).catch(() => undefined);
  }, []);

  useEffect(() => {
    let alive = true;
    api
      .get<AgentReviewDetail>(`/api/agent_reviews/${reviewId}`)
      .then((r) => {
        if (alive) setReview(r);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [reviewId]);

  async function toggleDismiss() {
    if (!review) return;
    setBusy(true);
    try {
      if (review.dismissed) {
        await api.post(`/api/agent_reviews/${reviewId}/undismiss`);
      } else {
        await api.post(`/api/agent_reviews/${reviewId}/dismiss`);
      }
      onMutated();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-y-auto p-6">
        {!review ? (
          <div className="text-gray-500">Loading…</div>
        ) : (
          <>
            <div className="flex items-start justify-between">
              <div>
                <div className="text-xs text-gray-500">
                  {new Date(review.created_at).toLocaleString()} ·{" "}
                  {review.provider} · {review.model}
                </div>
                <h2 className="text-lg font-semibold mt-1">
                  {review.headline ?? "(no headline)"}
                </h2>
              </div>
              <button
                onClick={onClose}
                className="text-gray-500 hover:text-gray-700"
              >
                ✕
              </button>
            </div>

            {review.status === "failed" ? (
              <div className="mt-4">
                <h3 className="font-medium text-red-700">Error</h3>
                <pre className="bg-red-50 border border-red-200 rounded p-3 text-xs whitespace-pre-wrap">
                  {review.error_text}
                </pre>
              </div>
            ) : (
              <>
                {review.flags && review.flags.length > 0 && (
                  <div className="mt-4">
                    <h3 className="font-medium">Flags</h3>
                    <ul className="mt-2 space-y-2">
                      {review.flags.map((f, i) => (
                        <li
                          key={i}
                          className="border border-gray-200 rounded p-3"
                        >
                          <div className="font-medium text-sm">{f.title}</div>
                          <div className="text-sm text-gray-700">{f.body}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {review.body && (
                  <div className="mt-4">
                    <h3 className="font-medium">Notes</h3>
                    <pre className="whitespace-pre-wrap text-sm text-gray-800 mt-2">
                      {review.body}
                    </pre>
                  </div>
                )}
                {review.evidence && review.evidence.length > 0 && (
                  <div className="mt-4">
                    <h3 className="font-medium">Evidence</h3>
                    <ul className="list-disc list-inside text-sm text-gray-700 mt-1">
                      {review.evidence.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {review.artifact_gcs_paths.length > 0 && (
                  <div className="mt-4 text-xs text-gray-500">
                    <div>Transmitted artifacts:</div>
                    <ul className="list-disc list-inside">
                      {review.artifact_gcs_paths.map((p) => (
                        <li key={p} className="break-all">{p}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <PromptDetails review={review} catalog={catalog} />
              </>
            )}

            <div className="mt-6 flex justify-end gap-2">
              {canDismiss && (
                <button
                  onClick={toggleDismiss}
                  disabled={busy}
                  className="px-3 py-1.5 text-sm border border-gray-300 rounded"
                >
                  {review.dismissed ? "Un-dismiss" : "Dismiss"}
                </button>
              )}
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PromptDetails({
  review,
  catalog,
}: {
  review: AgentReviewDetail;
  catalog: SectionCatalog | null;
}) {
  if (!review.prompt_source && !review.prompt_text) return null;

  const isCustom =
    review.prompt_source === "custom_saved" || review.prompt_source === "custom_one_off";
  const sourceLabel = isCustom
    ? review.prompt_source === "custom_saved"
      ? "Custom prompt (saved)"
      : "Custom prompt (one-off)"
    : "Standard prompt";

  // Resolve sub-item ids to "Section › Item" labels via the catalog.
  const labelsBySectionId = new Map<string, string>();
  const subItemIndex = new Map<string, { section_label: string; label: string }>();
  if (catalog) {
    for (const section of catalog.sections) {
      labelsBySectionId.set(section.id, section.label);
      for (const si of section.sub_items) {
        subItemIndex.set(si.id, {
          section_label: section.label,
          label: si.label,
        });
      }
    }
  }

  return (
    <details className="mt-4 text-sm">
      <summary className="cursor-pointer text-gray-700 font-medium">
        Prompt details
      </summary>
      <div className="mt-2 text-xs text-gray-700 border border-gray-200 rounded p-3">
        <div className="font-medium mb-2">{sourceLabel}</div>
        {!isCustom && review.prompt_sections && review.prompt_sections.length > 0 && (
          <div>
            <div className="text-gray-500 mb-1">Items requested:</div>
            <ul className="list-disc list-inside space-y-0.5">
              {review.prompt_sections.map((id) => {
                const entry = subItemIndex.get(id);
                return (
                  <li key={id}>
                    {entry
                      ? `${entry.section_label} › ${entry.label}`
                      : id}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {isCustom && review.prompt_text && (
          <div>
            <div className="text-gray-500 mb-1">Prompt body:</div>
            <pre className="whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded p-2 max-h-72 overflow-y-auto">
              {review.prompt_text}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}
