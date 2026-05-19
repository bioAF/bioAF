"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { getCurrentUser, isAuthenticated } from "@/lib/auth";
import {
  cleanText,
  formatAuthors,
  formatYear,
  literature,
  type Recommendation,
  type RecommendationStatus,
} from "@/lib/literature";

const BUCKET_COLORS: Record<string, string> = {
  high: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  low: "bg-gray-100 text-gray-700",
};

export default function LiteratureRecommendationsPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canDecide =
    user?.role_name === "admin" || user?.role_name === "comp_bio";

  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [status, setStatus] = useState<RecommendationStatus>("accepted");
  const [loading, setLoading] = useState(true);
  const [runExperimentId, setRunExperimentId] = useState("");
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  function refresh() {
    setLoading(true);
    literature
      .listRecommendations({ status })
      .then((data) => setRecommendations(data.items))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  async function triggerRun() {
    const eid = Number(runExperimentId);
    if (!eid) {
      setRunError("Enter an experiment ID.");
      return;
    }
    setRunning(true);
    setRunMessage(null);
    setRunError(null);
    try {
      const run = await literature.runLitReview(eid);
      setRunMessage(
        `Lit Review Run ${run.id} queued for experiment ${eid}. ` +
          `Recommended papers will be added to the Library with an AI note as the run completes.`,
      );
      setRunExperimentId("");
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const r2 = await literature.getRun(run.id);
        if (
          r2.status === "complete" ||
          r2.status === "partial" ||
          r2.status === "failed"
        ) {
          setRunMessage(
            `Lit Review Run ${run.id} ${r2.status}; ${r2.recommendation_count ?? 0} papers added to the Library.`,
          );
          break;
        }
      }
      refresh();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Run failed.";
      setRunError(message);
    } finally {
      setRunning(false);
    }
  }

  async function dismiss(r: Recommendation) {
    if (!confirm(`Dismiss "${cleanText(r.paper.title)}" org-wide?`)) return;
    await literature.dismissRecommendation(r.id);
    refresh();
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => router.push("/data/literature")}
            className="text-bioaf-700 hover:underline text-sm mb-4"
          >
            &larr; Back to library
          </button>
          <div className="flex justify-between items-center mb-6">
            <h1 className="text-2xl font-bold">Literature Recommendations</h1>
            <div>
              <label className="text-xs text-gray-500 mr-2">Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as RecommendationStatus)}
                className="border border-gray-300 rounded px-3 py-2"
              >
                <option value="accepted">Active in Library</option>
                <option value="dismissed">Dismissed</option>
              </select>
            </div>
          </div>

          <div className="bg-purple-50 border border-purple-200 rounded p-3 mb-6 text-sm text-purple-900">
            Lit Review Runs add recommended papers to the Library automatically
            with an AI Lit Review Bot note on each paper detail explaining the
            recommendation. Use this page to review what the LLM picked or to
            dismiss a recommendation org-wide.
          </div>

          {canDecide && (
            <div className="bg-white rounded shadow p-4 mb-6">
              <h2 className="font-semibold mb-2">Run a Lit Review</h2>
              <p className="text-xs text-gray-500 mb-3">
                Runs require an active LLM Provider for the org. The run uses
                the experiment context plus existing library papers to ask the
                LLM for adjacent searches, then scores candidates 0.0 to 1.0.
                Accepted papers go straight into the Library, associated with
                the experiment.
              </p>
              <div className="flex gap-2 items-end">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">
                    Experiment ID
                  </label>
                  <input
                    type="number"
                    value={runExperimentId}
                    onChange={(e) => setRunExperimentId(e.target.value)}
                    className="border border-gray-300 rounded px-3 py-2 w-32"
                    placeholder="e.g. 42"
                  />
                </div>
                <button
                  onClick={triggerRun}
                  disabled={running || !runExperimentId}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
                >
                  {running ? "Running..." : "Run Lit Review"}
                </button>
              </div>
              {runMessage && (
                <div className="text-sm text-green-700 mt-2">{runMessage}</div>
              )}
              {runError && (
                <div className="text-sm text-red-700 mt-2">{runError}</div>
              )}
            </div>
          )}

          {loading ? (
            <LoadingSpinner />
          ) : recommendations.length === 0 ? (
            <div className="border border-dashed border-gray-300 rounded p-12 text-center text-gray-500">
              No {status === "accepted" ? "active" : "dismissed"} recommendations.
              Trigger a Lit Review Run from an experiment to generate some.
            </div>
          ) : (
            <ul className="space-y-4">
              {recommendations.map((r) => (
                <li key={r.id} className="bg-white rounded shadow p-4">
                  <div className="flex justify-between items-start">
                    <div className="flex-1">
                      <button
                        onClick={() =>
                          router.push(`/data/literature/papers/${r.paper.id}`)
                        }
                        className="text-lg font-medium text-bioaf-700 hover:underline text-left"
                      >
                        {cleanText(r.paper.title)}
                      </button>
                      <div className="text-sm text-gray-600 mt-1">
                        {formatAuthors(r.paper.authors)} &middot;{" "}
                        {formatYear(r.paper.publication_date)} &middot;{" "}
                        {cleanText(r.paper.journal)}
                      </div>
                      {r.paper.abstract && (
                        <p className="text-sm text-gray-700 mt-2 line-clamp-3">
                          {cleanText(r.paper.abstract)}
                        </p>
                      )}
                      {r.reasoning && (
                        <div className="text-sm italic text-gray-600 mt-2">
                          AI: {r.reasoning}
                        </div>
                      )}
                      <div className="flex gap-2 mt-3 text-xs">
                        <span
                          className={`px-2 py-0.5 rounded ${BUCKET_COLORS[r.relevance_bucket] ?? "bg-gray-100"}`}
                        >
                          relevance {r.relevance_score.toFixed(2)} ({r.relevance_bucket})
                        </span>
                        <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                          experiment {r.experiment_id}
                        </span>
                        <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                          run {r.review_run_id}
                        </span>
                      </div>
                    </div>
                    {status === "accepted" && canDecide && (
                      <div className="flex flex-col gap-2 ml-4">
                        <button
                          onClick={() => dismiss(r)}
                          className="border border-red-300 text-red-700 px-3 py-1 rounded text-sm hover:bg-red-50"
                        >
                          Dismiss
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </main>
      </div>
    </div>
  );
}
