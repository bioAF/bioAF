"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface RunStats {
  running: number;
  pending: number;
  completed_today: number;
  failed_today: number;
}

export function RunningJobsWidget() {
  const { data: stats, loading, error, retry } = useWidgetData<RunStats>(
    async () => {
      // No per-count fallback. That question the old comment here left open --
      // "whether a failed count should say so rather than read as no running
      // jobs" -- was answered by measuring it: under a total outage this widget
      // rendered "0 / 0 pending", byte-identical to a genuinely idle cluster.
      //
      // Letting either rejection through is deliberate. A partial total is a
      // wrong total presented as a right one, and on a compute platform "0
      // running" is the one number nobody double-checks.
      const [running, pending] = await Promise.all([
        api.getWithRetry<{ total: number }>("/api/pipeline-runs?status=running&page_size=1"),
        api.getWithRetry<{ total: number }>("/api/pipeline-runs?status=pending&page_size=1"),
      ]);
      return {
        running: running.total,
        pending: pending.total,
        completed_today: 0,
        failed_today: 0,
      };
    },
    "Job counts",
  );

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-running-jobs">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Running Jobs
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading jobs...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button onClick={retry} className="ml-2 text-bioaf-600 hover:underline">
            Retry
          </button>
        </div>
      )}
      {!loading && !error && !stats && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">No pipeline activity yet.</p>
      )}
      {!loading && !error && stats && (
        <div>
          <div className="text-3xl font-bold text-bioaf-600">{stats.running}</div>
          <p className="text-sm text-gray-500 mt-1">
            {stats.pending} pending
          </p>
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
