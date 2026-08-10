"use client";

import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
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
  const { data: counts, loading, error, retry } = useWidgetData(
    async () => {
      // Both halves or neither. Substituting an empty list for a failed half
      // reported "0 runs completed" during a total outage, indistinguishable
      // from a genuinely quiet week.
      const [runs, exps] = await Promise.all([
        api.getWithRetry<RunList>("/api/pipeline-runs?status=completed&page_size=50"),
        api.getWithRetry<ExperimentList>("/api/experiments?page_size=50"),
      ]);
      return {
        runs: (runs.runs || []).filter((r) => withinHours(r.completed_at, WEEK_HOURS)).length,
        experiments: (exps.experiments || []).filter((e) =>
          withinHours(e.created_at, WEEK_HOURS),
        ).length,
      };
    },
    "Team output",
  );

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-team-output">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Team output this week
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading output...</span>
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
