"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo } from "@/components/dashboard/time";

interface Plot {
  id: number;
  title: string | null;
  source_type: string | null;
  indexed_at: string;
}

interface PlotList {
  plots: Plot[];
  total: number;
}

export function RecentPlotsWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () => {
      const res = await api.getWithRetry<PlotList>("/api/plots?page_size=6");
      return res.plots || [];
    },
    "Plots",
  );
  const plots = data;

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-recent-plots">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Recent plots
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading plots...</span>
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
      {!loading && !error && plots && plots.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No plots yet.
        </p>
      )}
      {!loading && !error && plots && plots.length > 0 && (
        <div>
          <ul className="space-y-1">
            {plots.map((p) => (
              <li
                key={p.id}
                className="flex items-center justify-between gap-2 text-sm px-1 py-0.5"
              >
                <span className="truncate text-gray-800">{p.title || "Untitled plot"}</span>
                <span className="shrink-0 text-xs text-gray-500">
                  {p.source_type || "plot"} &middot; {timeAgo(p.indexed_at)}
                </span>
              </li>
            ))}
          </ul>
          <Link
            href="/results/plot-archive"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View plot archive
          </Link>
        </div>
      )}
    </div>
  );
}
