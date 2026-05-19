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
import { AiLitReviewLauncher } from "@/components/literature/AiLitReviewLauncher";

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
            <h1 className="text-2xl font-bold">AI Literature Review</h1>
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
            AI Literature Review adds recommended papers to the Library
            automatically with an AI Lit Review Bot note on each paper detail
            explaining the recommendation. Use this page to review what the
            LLM picked or to dismiss a recommendation org-wide.
          </div>

          {canDecide && (
            <div className="mb-6">
              <AiLitReviewLauncher onSubmitted={() => refresh()} />
            </div>
          )}

          {loading ? (
            <LoadingSpinner />
          ) : recommendations.length === 0 ? (
            <div className="border border-dashed border-gray-300 rounded p-12 text-center text-gray-500">
              No {status === "accepted" ? "active" : "dismissed"} recommendations.
              Run AI Literature Review from the form above (or from an
              experiment&apos;s Literature tab) to generate some.
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
