"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { literature, type LitReviewRun } from "@/lib/literature";

interface ProjectSummary {
  id: number;
  name: string;
}

interface ExperimentSummary {
  id: number;
  name: string;
  project: { id: number; name: string } | null;
}

interface ProjectListResponse {
  projects: ProjectSummary[];
  total: number;
}

interface ExperimentListResponse {
  experiments: ExperimentSummary[];
  total: number;
  page: number;
  page_size: number;
}

interface Props {
  onSubmitted?: (run: LitReviewRun) => void;
}

export function AiLitReviewLauncher({ onSubmitted }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  const [experimentId, setExperimentId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    api
      .get<ProjectListResponse>("/api/projects?page_size=200")
      .then((d) =>
        setProjects(d.projects.map((p) => ({ id: p.id, name: p.name }))),
      )
      .catch(() => setProjects([]));
    api
      .get<ExperimentListResponse>("/api/experiments?page_size=500")
      .then((d) =>
        setExperiments(
          d.experiments.map((e) => ({
            id: e.id,
            name: e.name,
            project: e.project
              ? { id: e.project.id, name: e.project.name }
              : null,
          })),
        ),
      )
      .catch(() => setExperiments([]));
  }, []);

  const filteredExperiments = useMemo(() => {
    if (!projectId) return experiments;
    const pid = Number(projectId);
    return experiments.filter((e) => e.project?.id === pid);
  }, [experiments, projectId]);

  // Clear the experiment selection if the project filter removes it.
  useEffect(() => {
    if (!experimentId) return;
    if (!filteredExperiments.some((e) => String(e.id) === experimentId)) {
      setExperimentId("");
    }
  }, [filteredExperiments, experimentId]);

  const labelFor = (e: ExperimentSummary): string => {
    if (!projectId && e.project) return `${e.project.name} > ${e.name}`;
    return e.name;
  };

  async function submit() {
    if (!experimentId) return;
    cancelRef.current = false;
    setElapsed(0);
    setRunning(true);
    setMessage(null);
    setError(null);
    try {
      const run = await literature.runLitReview(Number(experimentId));
      setMessage(
        `AI Lit Review run ${run.id} queued. Recommended papers will be added to the Library with an AI note as the run completes.`,
      );
      // The run exposes only a coarse status (no per-step signal), so we can't show
      // a real percentage: poll until terminal and surface elapsed time instead.
      for (let i = 0; i < 60; i++) {
        if (cancelRef.current) break;
        await new Promise((r) => setTimeout(r, 1000));
        if (cancelRef.current) break;
        setElapsed(i + 1);
        const r2 = await literature.getRun(run.id);
        if (
          r2.status === "complete" ||
          r2.status === "partial" ||
          r2.status === "failed"
        ) {
          setMessage(
            `AI Lit Review run ${run.id} ${r2.status}; ${r2.recommendation_count ?? 0} papers added to the Library.`,
          );
          break;
        }
      }
      onSubmitted?.(run);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Run failed.");
    } finally {
      setRunning(false);
    }
  }

  // Stop the client from waiting. The run keeps going server-side and its papers
  // land in the Library as it completes; the poll loop notices the flag and exits.
  function stopWatching() {
    cancelRef.current = true;
    setRunning(false);
    setMessage(
      "The review keeps running in the background; recommended papers will appear in the Library as it completes.",
    );
  }

  return (
    <div className="bg-white rounded shadow p-4">
      <h2 className="font-semibold mb-2">Run AI Literature Review</h2>
      <p className="text-xs text-gray-500 mb-3">
        Runs require an active LLM Provider for the org. The run uses the
        experiment context plus existing library papers to ask the LLM for
        adjacent searches, then scores candidates against the org&apos;s
        relevance threshold. Selected papers go straight into the Library,
        associated with the experiment.
      </p>
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label
            htmlFor="ai-lit-review-project"
            className="block text-xs text-gray-500 mb-1"
          >
            Project (optional filter)
          </label>
          <select
            id="ai-lit-review-project"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="border border-gray-300 rounded px-3 py-2 text-sm w-64"
          >
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="ai-lit-review-experiment"
            className="block text-xs text-gray-500 mb-1"
          >
            Experiment
          </label>
          <select
            id="ai-lit-review-experiment"
            value={experimentId}
            onChange={(e) => setExperimentId(e.target.value)}
            className="border border-gray-300 rounded px-3 py-2 text-sm w-80"
          >
            <option value="">Select an experiment</option>
            {filteredExperiments.map((e) => (
              <option key={e.id} value={String(e.id)}>
                {labelFor(e)}
              </option>
            ))}
          </select>
        </div>
        <button
          onClick={submit}
          disabled={running || !experimentId}
          className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
        >
          {running ? "Running..." : "Run AI Lit Review"}
        </button>
      </div>
      {running && (
        <div className="mt-3" data-testid="lit-review-progress">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm text-gray-700">
              Running AI Lit Review... {elapsed}s
            </span>
            <button
              type="button"
              onClick={stopWatching}
              className="text-sm text-gray-600 hover:text-gray-900 underline"
            >
              Stop watching
            </button>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded bg-gray-100"
            role="progressbar"
            aria-label="AI Lit Review in progress"
          >
            <div className="h-full w-full animate-pulse rounded bg-bioaf-600" />
          </div>
        </div>
      )}
      {message && <div className="text-sm text-green-700 mt-2">{message}</div>}
      {error && <div className="text-sm text-red-700 mt-2">{error}</div>}
    </div>
  );
}
