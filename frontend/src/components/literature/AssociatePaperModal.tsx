"use client";

import { Modal } from "@/components/shared/Modal";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { literature } from "@/lib/literature";
import { useProjectOptions } from "@/hooks/useProjectOptions";
import { logError } from "@/lib/errorReporting";
import { listAllExperiments } from "@/lib/experimentOptions";
import {
  EXPERIMENTS_DENIED_MESSAGE,
  isPermissionDenied,
  PROJECTS_DENIED_MESSAGE,
} from "@/lib/projectOptions";
import { Button } from "@/components/ui/Button";
import { PickerNotice } from "@/components/shared/PickerNotice";

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
 *
 * Associating with an experiment needs no project, so when the project list is
 * unavailable the experiment picker lists every experiment on its own.
 */
export function AssociatePaperModal({ paperIds, onClose, onAssociated }: Props) {
  const [projectId, setProjectId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [experiments, setExperiments] = useState<NamedItem[]>([]);
  const [experimentsProblem, setExperimentsProblem] = useState<"denied" | "failed" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const firstFieldRef = useRef<HTMLSelectElement>(null);
  const experimentFieldRef = useRef<HTMLSelectElement>(null);
  // Set by Retry so an experiment already chosen outlives the project list arriving.
  const keepExperiment = useRef(false);

  const open = paperIds.length > 0;
  const projectOptions = useProjectOptions({ enabled: open });
  const projectsUnavailable = projectOptions.unavailable;

  useEffect(() => {
    if (!open) return;
    setProjectId("");
    setExperimentId("");
    setError(null);
    keepExperiment.current = false;
    // Move focus into the dialog on open so keyboard users start inside it.
    const id = window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open, paperIds]);

  // A disabled select cannot hold focus, and focus must stay inside the dialog.
  useEffect(() => {
    if (projectsUnavailable) experimentFieldRef.current?.focus();
  }, [projectsUnavailable]);

  useEffect(() => {
    setExperimentsProblem(null);
    if (!projectId && !projectsUnavailable) {
      if (keepExperiment.current) {
        keepExperiment.current = false;
        return;
      }
      setExperiments([]);
      setExperimentId("");
      return;
    }
    let stale = false;
    listAllExperiments(projectId)
      .then((all) => {
        if (!stale) setExperiments(all.map((e) => ({ id: e.id, name: e.name })));
      })
      .catch((e) => {
        if (stale) return;
        setExperiments([]);
        if (isPermissionDenied(e)) {
          setExperimentsProblem("denied");
        } else {
          logError("loading the experiments to associate a paper with", e);
          setExperimentsProblem("failed");
        }
      });
    return () => {
      stale = true;
    };
  }, [projectId, projectsUnavailable]);

  if (!open) return null;

  const retryProjects = () => {
    keepExperiment.current = Boolean(experimentId);
    projectOptions.retry();
  };

  const performAssociate = async () => {
    if (!projectId && !experimentId) return;
    setBusy(true);
    setError(null);
    try {
      for (const pid of paperIds) {
        if (experimentId) {
          await literature.addAssociation(pid, {
            scope_type: "experiment",
            scope_id: Number(experimentId) });
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


  return (
    <Modal
      open
      title={`Associate ${paperIds.length === 1 ? "paper" : `${paperIds.length} papers`}`}
      // Busy means an association is in flight; dismissing mid-request would
      // leave the caller unsure whether it landed. Mirrors the old onKeyDown,
      // which also ignored Escape while busy.
      dismissible={!busy}
      onClose={onClose}
      size="sm"
      footer={
        <>
          <button
            onClick={onClose}
            className="px-3 py-1.5 border border-gray-300 rounded hover:bg-gray-50 text-sm"
          >
            Cancel
          </button>
          <Button size="sm"
            onClick={performAssociate}
            disabled={busy || (!projectId && !experimentId)}>
            {busy ? "Associating..." : "Associate"}
          </Button>
        </>
      }
    >
        <div className="mb-3">
          <label htmlFor="project" className="block text-xs text-gray-500 mb-1">Project</label>
          <select id="project"
            ref={firstFieldRef}
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            disabled={projectsUnavailable}
            className="w-full border border-gray-300 rounded px-3 py-2 disabled:bg-gray-50 disabled:text-gray-400"
          >
            <option value="">Select project</option>
            {projectOptions.projects.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
          <PickerNotice
            status={projectOptions.status}
            deniedMessage={PROJECTS_DENIED_MESSAGE}
            failedWhat="Projects"
            onRetry={retryProjects}
          />
        </div>
        <div className="mb-4">
          <label htmlFor="experiment-optional" className="block text-xs text-gray-500 mb-1">
            Experiment (optional)
          </label>
          <select id="experiment-optional"
            ref={experimentFieldRef}
            value={experimentId}
            onChange={(e) => setExperimentId(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2"
            disabled={!projectId && !projectsUnavailable && !experimentId}
          >
            <option value="">No experiment</option>
            {experiments.map((e) => (
              <option key={e.id} value={String(e.id)}>
                {e.name}
              </option>
            ))}
          </select>
          <PickerNotice
            status={experimentsProblem ?? "loaded"}
            deniedMessage={EXPERIMENTS_DENIED_MESSAGE}
            failedWhat="Experiments"
          />
          <p className="text-xs text-gray-500 mt-1">
            Choosing an experiment associates with the experiment scope; choosing
            only a project associates with the project scope.
          </p>
        </div>
        {error && <div className="text-xs text-red-700 mb-2">{error}</div>}
    </Modal>
  );
}
