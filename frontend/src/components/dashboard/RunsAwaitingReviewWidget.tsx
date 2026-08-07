"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo } from "@/components/dashboard/time";

interface Run {
  id: number;
  pipeline_name: string;
  status: string;
  completed_at: string | null;
  review_verdict: string | null;
}

interface RunList {
  runs: Run[];
  total: number;
}

export function RunsAwaitingReviewWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () => {
      const res = await api.getWithRetry<RunList>("/api/pipeline-runs?status=completed&page_size=20");
      return res.runs || [];
    },
    "Runs",
  );
  const runs = data;

  // A completed run with no review verdict yet is awaiting review.
  const awaiting = (runs || []).filter((r) => !r.review_verdict);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-runs-awaiting-review">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Runs awaiting review
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading runs...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={retry}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && awaiting.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          Nothing awaiting review.
        </p>
      )}
      {!loading && !error && awaiting.length > 0 && (
        <div>
          <div className="text-3xl font-bold text-amber-700">{awaiting.length}</div>
          <ul className="mt-2 space-y-1">
            {awaiting.slice(0, 5).map((r) => (
              <li key={r.id}>
                <Link
                  href={`/pipelines/runs/${r.id}`}
                  className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <span className="truncate text-sm text-gray-800">{r.pipeline_name}</span>
                  <span className="shrink-0 text-xs text-gray-500">
                    {r.completed_at ? `waiting ${timeAgo(r.completed_at)}` : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/pipelines/runs"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View all runs
          </Link>
        </div>
      )}
    </div>
  );
}
