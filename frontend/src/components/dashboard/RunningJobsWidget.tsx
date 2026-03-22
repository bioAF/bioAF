"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface RunStats {
  running: number;
  pending: number;
  completed_today: number;
  failed_today: number;
}

export function RunningJobsWidget() {
  const [stats, setStats] = useState<RunStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    Promise.all([
      api.getWithRetry<{ total: number }>("/api/pipeline-runs?status=running&page_size=1").catch(() => ({ total: 0 })),
      api.getWithRetry<{ total: number }>("/api/pipeline-runs?status=pending&page_size=1").catch(() => ({ total: 0 })),
    ])
      .then(([running, pending]) => {
        setStats({
          running: running.total,
          pending: pending.total,
          completed_today: 0,
          failed_today: 0,
        });
      })
      .catch(() => setError("Failed to load job stats"))
      .finally(() => { clearTimeout(timeout); setLoading(false); });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-running-jobs">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Running Jobs
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading jobs...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button onClick={() => window.location.reload()} className="ml-2 text-bioaf-600 hover:underline">
            Retry
          </button>
        </div>
      )}
      {!loading && !error && !stats && (
        <p className="text-sm text-gray-400" data-testid="widget-empty">No pipeline activity yet.</p>
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
