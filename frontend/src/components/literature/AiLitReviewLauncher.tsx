"use client";

import { useEffect, useRef, useState } from "react";
import { literature, type LitReviewRun } from "@/lib/literature";
import { useProjectOptions } from "@/hooks/useProjectOptions";
import { PROJECTS_DENIED_MESSAGE } from "@/lib/projectOptions";
import { PickerNotice } from "@/components/shared/PickerNotice";
import { Button } from "@/components/ui/Button";
import { isAbortError } from "@/lib/abort";
import { watchJob } from "@/lib/watchJob";
import { ExperimentPicker } from "@/components/experiments/ExperimentPicker";
import type { ExperimentOption } from "@/lib/experimentOptions";

interface Props {
  onSubmitted?: (run: LitReviewRun) => void;
}

export function AiLitReviewLauncher({ onSubmitted }: Props) {
  const projectOptions = useProjectOptions({ path: "/api/projects?page_size=200" });
  const [projectId, setProjectId] = useState<string>("");
  const [experimentId, setExperimentId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The run being watched, if any. Unmounting stops watching it (not the run).
  const watchRef = useRef<{ controller: AbortController; run: LitReviewRun | null } | null>(null);
  useEffect(
    () => () => {
      watchRef.current?.controller.abort();
      watchRef.current = null;
    },
    [],
  );

  const labelFor = (e: ExperimentOption): string => {
    if (!projectId && e.project) return `${e.project.name} > ${e.name}`;
    return e.name;
  };

  async function submit() {
    if (!experimentId) return;
    // Each run's watch is its own: a new run never revives a stopped one.
    watchRef.current?.controller.abort();
    const controller = new AbortController();
    const owned: { controller: AbortController; run: LitReviewRun | null } = { controller, run: null };
    watchRef.current = owned;
    setElapsed(0);
    setRunning(true);
    setMessage(null);
    setError(null);
    try {
      const run = await literature.runLitReview(Number(experimentId));
      if (controller.signal.aborted) return;
      owned.run = run;
      const runId = run.id;
      setMessage(
        `AI Lit Review run ${runId} queued. Recommended papers will be added to the Library with an AI note as the run completes.`,
      );
      // The run exposes only a coarse status (no per-step signal), so we can't show
      // a real percentage: poll until terminal (at most 60 reads, one second apart)
      // and surface elapsed time instead.
      let reads = 0;
      const { value: r2, timedOut } = await watchJob(
        run,
        async () => {
          reads += 1;
          setElapsed(reads);
          return literature.getRun(runId);
        },
        (r) => r !== run && (r.status === "complete" || r.status === "partial" || r.status === "failed"),
        { intervalMs: 1000, maxReads: 60, signal: controller.signal },
      );
      if (!timedOut) {
        setMessage(
          `AI Lit Review run ${runId} ${r2.status}; ${r2.recommendation_count ?? 0} papers added to the Library.`,
        );
      }
      watchRef.current = null;
      onSubmitted?.(run);
    } catch (e) {
      // A stopped watch (or a closed page) runs no completion code.
      if (isAbortError(e) || controller.signal.aborted) return;
      setError(e instanceof Error ? e.message : "Run failed.");
    } finally {
      if (!controller.signal.aborted) setRunning(false);
    }
  }

  // Stop the client from waiting. The run keeps going server-side and its papers
  // land in the Library as it completes. The stopped run notifies once, here.
  function stopWatching() {
    const owned = watchRef.current;
    watchRef.current = null;
    owned?.controller.abort();
    setRunning(false);
    setMessage(
      "The review keeps running in the background; recommended papers will appear in the Library as it completes.",
    );
    if (owned?.run) onSubmitted?.(owned.run);
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
            onChange={(e) => {
              // A new project filter starts the experiment choice over: never run on one it hides.
              setProjectId(e.target.value);
              setExperimentId("");
            }}
            disabled={projectOptions.unavailable}
            className="border border-gray-300 rounded px-3 py-2 text-sm w-64 disabled:bg-gray-50 disabled:text-gray-400"
          >
            <option value="">All projects</option>
            {projectOptions.projects.map((p) => (
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
          <div className="w-80">
            <ExperimentPicker
              id="ai-lit-review-experiment"
              label="Experiment"
              value={experimentId ? Number(experimentId) : null}
              onChange={(id) => setExperimentId(id == null ? "" : String(id))}
              projectId={projectId ? Number(projectId) : null}
              noneLabel="Select an experiment"
              optionLabel={labelFor}
              className="border border-gray-300 rounded px-3 py-2 text-sm w-full"
            />
          </div>
        </div>
        <Button
          onClick={submit}
          disabled={running || !experimentId}>
          {running ? "Running..." : "Run AI Lit Review"}
        </Button>
        <div className="w-full">
          <PickerNotice
            status={projectOptions.status}
            deniedMessage={PROJECTS_DENIED_MESSAGE}
            failedWhat="Projects"
            onRetry={projectOptions.retry}
          />
        </div>
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
