"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

interface OtherRun {
  id: number;
  pipeline_name: string;
  pipeline_version: string | null;
  status: string;
  created_at: string;
}

interface RunResponse {
  job_id: number;
  agent_review_id: number;
}

interface AgentReviewButtonsProps {
  runId: number;
  experimentId: number | null;
  pipelineStatus: string;
  onTriggered?: () => void;
}

export function AgentReviewButtons({
  runId,
  experimentId,
  pipelineStatus,
  onTriggered,
}: AgentReviewButtonsProps) {
  const { canAccess } = usePermissions();
  const canUse = canAccess("llm_integration", "use");
  const [hasActiveProvider, setHasActiveProvider] = useState<boolean | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [selectingB, setSelectingB] = useState(false);

  useEffect(() => {
    if (!canUse) return;
    api
      .get<{ active_provider: string | null }>("/api/integrations/llm/providers")
      .then((d) => setHasActiveProvider(d.active_provider != null))
      .catch(() => setHasActiveProvider(false));
  }, [canUse]);

  const triggerA = useCallback(async () => {
    setError(null);
    try {
      await api.post<RunResponse>("/api/agent_reviews/run", {
        entity_type: "pipeline_run",
        entity_id: runId,
        review_type: "pipeline_run_review_v1",
      });
      onTriggered?.();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [runId, onTriggered]);

  if (!canUse) return null;
  if (pipelineStatus !== "completed") return null;
  if (hasActiveProvider === false) return null;

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={triggerA}
        className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded hover:bg-bioaf-50"
      >
        Review this pipeline run
      </button>
      {experimentId !== null && (
        <button
          onClick={() => setSelectingB(true)}
          className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded hover:bg-bioaf-50"
        >
          Review across experiment
        </button>
      )}
      {error && (
        <span className="text-red-600 text-sm" role="alert">
          {error}
        </span>
      )}
      {selectingB && experimentId !== null && (
        <RunSelectionModal
          experimentId={experimentId}
          currentRunId={runId}
          onCancel={() => setSelectingB(false)}
          onSubmitted={() => {
            setSelectingB(false);
            onTriggered?.();
          }}
          onError={(m) => setError(m)}
        />
      )}
    </div>
  );
}

function RunSelectionModal({
  experimentId,
  currentRunId,
  onCancel,
  onSubmitted,
  onError,
}: {
  experimentId: number;
  currentRunId: number;
  onCancel: () => void;
  onSubmitted: () => void;
  onError: (m: string) => void;
}) {
  const [runs, setRuns] = useState<OtherRun[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set([currentRunId]));
  const [includeHtml, setIncludeHtml] = useState<Set<number>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .get<{ items: OtherRun[] }>(
        `/api/pipeline-runs?experiment_id=${experimentId}`,
      )
      .then((d) => {
        if (alive) setRuns(d.items ?? []);
      })
      .catch(() => {
        if (alive) setRuns([]);
      });
    return () => {
      alive = false;
    };
  }, [experimentId]);

  function toggle(id: number, set: Set<number>, setter: (s: Set<number>) => void) {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  }

  async function submit() {
    setSubmitting(true);
    try {
      await api.post<RunResponse>("/api/agent_reviews/run", {
        entity_type: "experiment",
        entity_id: experimentId,
        review_type: "experiment_run_comparison_v1",
        included_run_ids: Array.from(selected),
        include_html_report_run_ids: Array.from(includeHtml),
      });
      onSubmitted();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-y-auto p-6">
        <h3 className="text-lg font-semibold">Review across experiment</h3>
        <p className="text-sm text-gray-600 mt-1">
          Pick the other runs in this experiment to include in the comparison.
          The current run is always included.
        </p>
        {!runs ? (
          <div className="mt-4 text-gray-500">Loading…</div>
        ) : runs.length === 0 ? (
          <div className="mt-4 text-gray-500">No other runs in this experiment.</div>
        ) : (
          <table className="mt-4 w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="py-1 w-8"></th>
                <th className="py-1">Run</th>
                <th className="py-1">Status</th>
                <th className="py-1 w-32">HTML report</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const isCurrent = r.id === currentRunId;
                return (
                  <tr key={r.id} className="border-b">
                    <td className="py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.id)}
                        disabled={isCurrent}
                        onChange={() => toggle(r.id, selected, setSelected)}
                      />
                    </td>
                    <td className="py-2">
                      #{r.id} {r.pipeline_name}
                      {r.pipeline_version ? ` v${r.pipeline_version}` : ""}
                      {isCurrent && (
                        <span className="ml-2 text-xs text-gray-500">
                          (current)
                        </span>
                      )}
                    </td>
                    <td className="py-2">{r.status}</td>
                    <td className="py-2">
                      <label className="inline-flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={includeHtml.has(r.id)}
                          onChange={() =>
                            toggle(r.id, includeHtml, setIncludeHtml)
                          }
                        />
                        <span className="text-xs text-gray-600">include</span>
                      </label>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={submitting || selected.size === 0}
            className="px-3 py-1.5 text-sm bg-bioaf-600 disabled:bg-gray-300 text-white rounded"
          >
            {submitting ? "Submitting…" : "Run review"}
          </button>
        </div>
      </div>
    </div>
  );
}
