"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { withinHours } from "@/components/dashboard/time";

interface Run {
  id: number;
  status: string;
  completed_at: string | null;
}
interface RunList {
  runs: Run[];
}
interface Experiment {
  id: number;
  created_at: string;
}
interface ExperimentList {
  experiments: Experiment[];
}

const WEEK_HOURS = 24 * 7;

export function TeamOutputWidget() {
  const [counts, setCounts] = useState<{ runs: number; experiments: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    Promise.all([
      api
        .getWithRetry<RunList>("/api/pipeline-runs?status=completed&page_size=50")
        .catch(() => ({ runs: [] }) as RunList),
      api
        .getWithRetry<ExperimentList>("/api/experiments?page_size=50")
        .catch(() => ({ experiments: [] }) as ExperimentList),
    ])
      .then(([runs, exps]) => {
        setCounts({
          runs: (runs.runs || []).filter((r) => withinHours(r.completed_at, WEEK_HOURS)).length,
          experiments: (exps.experiments || []).filter((e) =>
            withinHours(e.created_at, WEEK_HOURS),
          ).length,
        });
      })
      .catch(() => setError("Failed to load team output"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-team-output">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Team output this week
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading output...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={() => window.location.reload()}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && counts && (
        <div className="flex gap-8">
          <div>
            <div className="text-3xl font-bold text-bioaf-600">{counts.runs}</div>
            <p className="text-xs text-gray-500 mt-1">runs completed</p>
          </div>
          <div>
            <div className="text-3xl font-bold text-bioaf-600">{counts.experiments}</div>
            <p className="text-xs text-gray-500 mt-1">experiments started</p>
          </div>
        </div>
      )}
    </div>
  );
}
