"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo, withinHours } from "@/components/dashboard/time";

interface Run {
  id: number;
  pipeline_name: string;
  status: string;
  created_at: string;
  completed_at: string | null;
}

interface RunList {
  runs: Run[];
  total: number;
}

const WINDOWS = [
  { label: "24h", hours: 24 },
  { label: "12h", hours: 12 },
  { label: "1h", hours: 1 },
];

export function FailedRunsWidget() {
  const [hours, setHours] = useState(24);
  const { data: runs, loading, error, retry } = useWidgetData(
    async () =>
      (await api.getWithRetry<RunList>("/api/pipeline-runs?status=failed&page_size=20")).runs || [],
    "Failed runs",
  );

  const windowLabel = WINDOWS.find((w) => w.hours === hours)?.label;
  const visible = (runs || []).filter((r) => withinHours(r.completed_at || r.created_at, hours));

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-failed-runs">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
          Failed runs
        </h3>
        <div className="flex gap-1" data-testid="failed-runs-window">
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              onClick={() => setHours(w.hours)}
              data-testid={`failed-window-${w.label}`}
              className={`text-xs px-1.5 py-0.5 rounded ${
                hours === w.hours
                  ? "bg-bioaf-600 text-white"
                  : "text-gray-600 hover:bg-gray-100"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>
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
      {!loading && !error && visible.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No failed runs in the last {windowLabel}.
        </p>
      )}
      {!loading && !error && visible.length > 0 && (
        <div>
          <div className="text-3xl font-bold text-red-600">{visible.length}</div>
          <ul className="mt-2 space-y-1">
            {visible.slice(0, 5).map((r) => (
              <li key={r.id}>
                <Link
                  href={`/pipelines/runs/${r.id}`}
                  className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <span className="truncate text-sm text-gray-800">{r.pipeline_name}</span>
                  <span className="shrink-0 text-xs text-gray-500">
                    {timeAgo(r.completed_at || r.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
