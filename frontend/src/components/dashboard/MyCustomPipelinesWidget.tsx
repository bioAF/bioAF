"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo } from "@/components/dashboard/time";

interface CustomPipeline {
  id: number;
  name: string;
  pipeline_key: string;
  updated_at: string;
}

export function MyCustomPipelinesWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () => {
      const res = await api.getWithRetry<CustomPipeline[]>("/api/v1/custom-pipelines");
      return res || [];
    },
    "Pipelines",
  );
  const items = data;

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-custom-pipelines">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        My custom pipelines
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading pipelines...</span>
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
      {!loading && !error && items && items.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No custom pipelines yet.
        </p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <div>
          <ul className="space-y-1">
            {items.slice(0, 6).map((p) => (
              <li key={p.id}>
                <Link
                  href={`/pipelines/custom/${p.id}`}
                  className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <span className="truncate text-sm text-gray-800">{p.name}</span>
                  <span className="shrink-0 text-xs text-gray-500">{timeAgo(p.updated_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/pipelines/custom"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View custom pipelines
          </Link>
        </div>
      )}
    </div>
  );
}
