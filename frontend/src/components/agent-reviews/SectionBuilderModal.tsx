"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { AssembledPromptModal } from "./AssembledPromptModal";

interface SubItem {
  id: string;
  label: string;
  default_on: boolean;
  prompt_fragment: string;
}

interface Section {
  id: string;
  label: string;
  experiment_only: boolean;
  sub_items: SubItem[];
}

interface SectionCatalogResponse {
  sections: Section[];
  pipeline_run_defaults: string[];
  experiment_defaults: string[];
}

interface SavedPrompt {
  id: number;
  name: string;
  body: string;
  created_by_user_id: number;
  created_by_user_label: string;
  created_at: string;
}

interface OtherRun {
  id: number;
  pipeline_name: string;
  pipeline_version: string | null;
  status: string;
  created_at: string;
}

type Mode = "builder" | { customSavedId: number };

export type RunBody =
  | {
      entity_type: "pipeline_run";
      entity_id: number;
      selected_sub_item_ids?: string[];
      custom_prompt_id?: number;
      custom_prompt_body?: string;
    }
  | {
      entity_type: "experiment";
      entity_id: number;
      included_run_ids: number[];
      include_html_report_run_ids: number[];
      selected_sub_item_ids?: string[];
      custom_prompt_id?: number;
      custom_prompt_body?: string;
    };

interface Props {
  entityType: "pipeline_run" | "experiment";
  /**
   * Required for pipeline_run scope. Optional for experiment scope: when
   * present the corresponding run is pre-checked and locked, when absent the
   * modal launches with every visible run in the experiment selected.
   */
  runId?: number;
  experimentId: number | null;
  onCancel: () => void;
  onSubmitted: () => void;
  onError: (msg: string) => void;
}

export function SectionBuilderModal({
  entityType,
  runId,
  experimentId,
  onCancel,
  onSubmitted,
  onError,
}: Props) {
  const isExperiment = entityType === "experiment";

  const [catalog, setCatalog] = useState<SectionCatalogResponse | null>(null);
  const [savedPrompts, setSavedPrompts] = useState<SavedPrompt[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<Mode>("builder");
  const [submitting, setSubmitting] = useState(false);

  // Button B-only state.
  const [otherRuns, setOtherRuns] = useState<OtherRun[] | null>(null);
  const [selectedRuns, setSelectedRuns] = useState<Set<number>>(
    runId !== undefined ? new Set([runId]) : new Set(),
  );
  const [htmlReportRuns, setHtmlReportRuns] = useState<Set<number>>(new Set());

  // Display prompt modal state.
  const [displayPromptBody, setDisplayPromptBody] = useState<string | null>(null);
  const [displayPromptLoading, setDisplayPromptLoading] = useState(false);

  // Load catalog + saved prompts.
  useEffect(() => {
    api
      .get<SectionCatalogResponse>("/api/agent_reviews/section_catalog")
      .then((d) => {
        setCatalog(d);
        const defaults = isExperiment ? d.experiment_defaults : d.pipeline_run_defaults;
        setSelected(new Set(defaults));
        setExpanded(new Set(d.sections.map((s) => s.id)));
      })
      .catch((e) => onError((e as Error).message));
    api
      .get<{ items: SavedPrompt[] }>("/api/agent_reviews/prompts")
      .then((d) => setSavedPrompts(d.items))
      .catch(() => undefined);
  }, [isExperiment, onError]);

  // Load other runs in the experiment for Button B's run selector.
  useEffect(() => {
    if (!isExperiment || experimentId === null) return;
    api
      .get<{ items: OtherRun[] }>(`/api/pipeline-runs?experiment_id=${experimentId}`)
      .then((d) => {
        const items = d.items ?? [];
        setOtherRuns(items);
        // Experiment-page entry (no runId): preselect every visible run.
        if (runId === undefined) {
          setSelectedRuns(new Set(items.map((r) => r.id)));
        }
      })
      .catch(() => setOtherRuns([]));
  }, [isExperiment, experimentId, runId]);

  const visibleSections = useMemo<Section[]>(() => {
    if (!catalog) return [];
    return catalog.sections.filter((s) => !s.experiment_only || isExperiment);
  }, [catalog, isExperiment]);

  function toggleSubItem(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSection(section: Section) {
    setSelected((prev) => {
      const next = new Set(prev);
      const allOn = section.sub_items.every((si) => prev.has(si.id));
      for (const si of section.sub_items) {
        if (allOn) next.delete(si.id);
        else next.add(si.id);
      }
      return next;
    });
  }

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function sectionState(section: Section): "all" | "some" | "none" {
    const on = section.sub_items.filter((si) => selected.has(si.id)).length;
    if (on === 0) return "none";
    if (on === section.sub_items.length) return "all";
    return "some";
  }

  function toggleRun(id: number) {
    if (runId !== undefined && id === runId) return; // current run always included
    setSelectedRuns((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleHtmlReport(id: number) {
    setHtmlReportRuns((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const usingCustom = typeof mode === "object" && "customSavedId" in mode;
  const activeCustom = usingCustom
    ? savedPrompts.find((p) => p.id === mode.customSavedId)
    : null;

  const canRun =
    !submitting &&
    (usingCustom || selected.size > 0) &&
    (!isExperiment || selectedRuns.size > 0);

  const showDisplayPrompt = useCallback(async () => {
    setDisplayPromptLoading(true);
    try {
      if (usingCustom && activeCustom) {
        setDisplayPromptBody(activeCustom.body);
        return;
      }
      const resp = await api.post<{ body: string }>(
        "/api/agent_reviews/assemble_prompt",
        {
          entity_type: entityType,
          selected_sub_item_ids: Array.from(selected),
        },
      );
      setDisplayPromptBody(resp.body);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setDisplayPromptLoading(false);
    }
  }, [activeCustom, entityType, onError, selected, usingCustom]);

  async function submit(overrideBody: string | null) {
    setSubmitting(true);
    try {
      const base = {
        entity_type: entityType,
        entity_id: isExperiment ? experimentId! : runId!,
        ...(isExperiment
          ? {
              included_run_ids: Array.from(selectedRuns),
              include_html_report_run_ids: Array.from(htmlReportRuns),
            }
          : {}),
      };
      let promptBody: Record<string, unknown> = {};
      if (overrideBody !== null) {
        promptBody = { custom_prompt_body: overrideBody };
      } else if (usingCustom && activeCustom) {
        promptBody = { custom_prompt_id: activeCustom.id };
      } else {
        promptBody = { selected_sub_item_ids: Array.from(selected) };
      }
      await api.post("/api/agent_reviews/run", { ...base, ...promptBody });
      onSubmitted();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[85vh] overflow-y-auto p-6">
        <h3 className="text-lg font-semibold">
          {isExperiment ? "Review across experiment" : "Review this pipeline run"}
        </h3>

        <div className="mt-4">
          <label className="text-sm font-medium text-gray-700">Prompt source</label>
          <select
            className="mt-1 w-full border border-gray-300 rounded px-3 py-2 text-sm"
            value={typeof mode === "string" ? "builder" : `custom-${mode.customSavedId}`}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "builder") setMode("builder");
              else setMode({ customSavedId: Number(v.replace("custom-", "")) });
            }}
          >
            <option value="builder">bioAF prompt builder</option>
            {savedPrompts.map((p) => (
              <option key={p.id} value={`custom-${p.id}`}>
                custom - {p.name} - {p.created_by_user_label}
              </option>
            ))}
          </select>
        </div>

        {!usingCustom && catalog && (
          <div className="mt-5 space-y-2">
            {visibleSections.map((section) => {
              const state = sectionState(section);
              return (
                <div key={section.id} className="border border-gray-200 rounded">
                  <div className="flex items-center p-2 gap-2">
                    <button
                      type="button"
                      onClick={() => toggleExpanded(section.id)}
                      aria-label={
                        expanded.has(section.id) ? "Collapse section" : "Expand section"
                      }
                      className="text-gray-500 hover:text-gray-800 text-sm w-4"
                    >
                      {expanded.has(section.id) ? "▾" : "▸"}
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleSection(section)}
                      className="text-sm flex items-center gap-2"
                    >
                      <span
                        className={`inline-flex items-center justify-center w-4 h-4 border rounded text-xs ${
                          state === "all"
                            ? "bg-bioaf-600 border-bioaf-600 text-white"
                            : state === "some"
                              ? "bg-bioaf-100 border-bioaf-400 text-bioaf-700"
                              : "border-gray-400 text-transparent"
                        }`}
                      >
                        {state === "all" ? "✓" : state === "some" ? "–" : ""}
                      </span>
                      <span className="font-medium">{section.label}</span>
                    </button>
                  </div>
                  {expanded.has(section.id) && (
                    <div className="px-3 pb-2 pl-10 space-y-1">
                      {section.sub_items.map((si) => (
                        <label
                          key={si.id}
                          className="flex items-start gap-2 text-sm py-1"
                        >
                          <input
                            type="checkbox"
                            checked={selected.has(si.id)}
                            onChange={() => toggleSubItem(si.id)}
                            className="mt-1"
                          />
                          <span>
                            <span className="font-medium">{si.label}</span>
                            <span className="block text-xs text-gray-500">
                              {si.prompt_fragment}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {usingCustom && activeCustom && (
          <div className="mt-5">
            <div className="text-sm text-gray-600 mb-1">
              Saved prompt body (read-only). Use Display prompt to view and
              optionally customize this run.
            </div>
            <pre className="bg-gray-50 border border-gray-200 rounded p-3 text-xs whitespace-pre-wrap max-h-64 overflow-y-auto">
              {activeCustom.body}
            </pre>
          </div>
        )}

        {isExperiment && (
          <div className="mt-6">
            <h4 className="font-medium text-sm mb-2">Included pipeline runs</h4>
            {!otherRuns ? (
              <div className="text-sm text-gray-500">Loading…</div>
            ) : otherRuns.length === 0 ? (
              <div className="text-sm text-gray-500">
                No runs visible in this experiment.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b">
                    <th className="py-1 w-8"></th>
                    <th className="py-1">Run</th>
                    <th className="py-1">Status</th>
                    <th className="py-1 w-28">HTML report</th>
                  </tr>
                </thead>
                <tbody>
                  {otherRuns.map((r) => {
                    const isCurrent = runId !== undefined && r.id === runId;
                    return (
                      <tr key={r.id} className="border-b">
                        <td className="py-2">
                          <input
                            type="checkbox"
                            checked={selectedRuns.has(r.id)}
                            disabled={isCurrent}
                            onChange={() => toggleRun(r.id)}
                          />
                        </td>
                        <td className="py-2">
                          #{r.id} {r.pipeline_name}
                          {r.pipeline_version ? ` v${r.pipeline_version}` : ""}
                          {isCurrent && (
                            <span className="ml-2 text-xs text-gray-500">(current)</span>
                          )}
                        </td>
                        <td className="py-2">{r.status}</td>
                        <td className="py-2">
                          <label className="inline-flex items-center gap-1">
                            <input
                              type="checkbox"
                              checked={htmlReportRuns.has(r.id)}
                              onChange={() => toggleHtmlReport(r.id)}
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
          </div>
        )}

        <div className="mt-6 flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded"
          >
            Cancel
          </button>
          <button
            onClick={showDisplayPrompt}
            disabled={
              displayPromptLoading ||
              (!usingCustom && selected.size === 0)
            }
            className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded disabled:opacity-50"
          >
            {displayPromptLoading ? "Loading…" : "Display prompt"}
          </button>
          <button
            onClick={() => submit(null)}
            disabled={!canRun}
            className="px-3 py-1.5 text-sm bg-bioaf-600 disabled:bg-gray-300 text-white rounded"
          >
            {submitting ? "Submitting…" : "Run review"}
          </button>
        </div>
      </div>

      {displayPromptBody !== null && (
        <AssembledPromptModal
          body={displayPromptBody}
          onClose={() => setDisplayPromptBody(null)}
          onRunWithCustomBody={async (customBody) => {
            setDisplayPromptBody(null);
            await submit(customBody);
          }}
          onSavedAndRun={async (saved) => {
            // Selecting the saved prompt and running uses custom_prompt_id.
            setSavedPrompts((prev) => [saved, ...prev]);
            setMode({ customSavedId: saved.id });
            setDisplayPromptBody(null);
            // Submit using the saved id rather than body, since the user
            // explicitly saved it.
            setSubmitting(true);
            try {
              const base = {
                entity_type: entityType,
                entity_id: isExperiment ? experimentId! : runId!,
                ...(isExperiment
                  ? {
                      included_run_ids: Array.from(selectedRuns),
                      include_html_report_run_ids: Array.from(htmlReportRuns),
                    }
                  : {}),
                custom_prompt_id: saved.id,
              };
              await api.post("/api/agent_reviews/run", base);
              onSubmitted();
            } catch (e) {
              onError((e as Error).message);
            } finally {
              setSubmitting(false);
            }
          }}
        />
      )}
    </div>
  );
}
