"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { literature } from "@/lib/literature";
import type { ExperimentListResponse, ProjectListResponse } from "@/lib/types";

interface NamedItem {
  id: number;
  name: string;
}

interface Props {
  /** Papers to associate. The modal renders only when this is non-empty. */
  paperIds: number[];
  onClose: () => void;
  /** Called after a successful association so the caller can refresh. */
  onAssociated: () => void;
}

/**
 * Cascading Project -> Experiment picker that associates one or more papers.
 * Choosing an experiment associates at experiment scope; choosing only a
 * project associates at project scope. Shared by the Library list and the
 * paper detail page so the behavior stays identical in both places.
 */
export function AssociatePaperModal({ paperIds, onClose, onAssociated }: Props) {
  const [projects, setProjects] = useState<NamedItem[]>([]);
  const [projectId, setProjectId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [experiments, setExperiments] = useState<NamedItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const open = paperIds.length > 0;

  useEffect(() => {
    if (!open) return;
    setProjectId("");
    setExperimentId("");
    setError(null);
    // Move focus into the dialog on open so keyboard users start inside it.
    const id = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    api
      .get<ProjectListResponse>("/api/projects?page_size=100")
      .then((data) =>
        setProjects(data.projects.map((p) => ({ id: p.id, name: p.name }))),
      )
      .catch(() => setProjects([]));
    return () => window.clearTimeout(id);
  }, [open, paperIds]);

  useEffect(() => {
    if (!projectId) {
      setExperiments([]);
      setExperimentId("");
      return;
    }
    api
      .get<ExperimentListResponse>(
        `/api/experiments?project_id=${projectId}&page_size=100`,
      )
      .then((data) =>
        setExperiments(data.experiments.map((e) => ({ id: e.id, name: e.name }))),
      )
      .catch(() => setExperiments([]));
  }, [projectId]);

  if (!open) return null;

  const performAssociate = async () => {
    if (!projectId && !experimentId) return;
    setBusy(true);
    setError(null);
    try {
      for (const pid of paperIds) {
        if (experimentId) {
          await literature.addAssociation(pid, {
            scope_type: "experiment",
            scope_id: Number(experimentId),
          });
        } else if (projectId) {
          await literature.addAssociation(pid, {
            scope_type: "project",
            scope_id: Number(projectId),
          });
        }
      }
      onAssociated();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && !busy) {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50"
      onKeyDown={onKeyDown}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-white rounded-lg shadow-xl p-6 w-96"
      >
        <h3 id={titleId} className="font-semibold mb-3">
          Associate {paperIds.length === 1 ? "paper" : `${paperIds.length} papers`}
        </h3>
        <div className="mb-3">
          <label htmlFor="project" className="block text-xs text-gray-500 mb-1">Project</label>
          <select id="project"
            ref={firstFieldRef}
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2"
          >
            <option value="">Select project</option>
            {projects.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="mb-4">
          <label htmlFor="experiment-optional" className="block text-xs text-gray-500 mb-1">
            Experiment (optional)
          </label>
          <select id="experiment-optional"
            value={experimentId}
            onChange={(e) => setExperimentId(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2"
            disabled={!projectId}
          >
            <option value="">No experiment</option>
            {experiments.map((e) => (
              <option key={e.id} value={String(e.id)}>
                {e.name}
              </option>
            ))}
          </select>
          <p className="text-xs text-gray-500 mt-1">
            Choosing an experiment associates with the experiment scope; choosing
            only a project associates with the project scope.
          </p>
        </div>
        {error && <div className="text-xs text-red-700 mb-2">{error}</div>}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 border border-gray-300 rounded hover:bg-gray-50 text-sm"
          >
            Cancel
          </button>
          <button
            onClick={performAssociate}
            disabled={busy || (!projectId && !experimentId)}
            className="px-3 py-1.5 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 text-sm disabled:opacity-50"
          >
            {busy ? "Associating..." : "Associate"}
          </button>
        </div>
      </div>
    </div>
  );
}
