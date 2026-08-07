"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

interface ExperimentItem {
  id: number;
  name: string;
  status: string;
}

interface ExperimentListResponse {
  experiments: ExperimentItem[];
  total: number;
}

export function ExperimentsStatusWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () =>
      (await api.getWithRetry<ExperimentListResponse>("/api/experiments?page_size=6"))
        .experiments || [],
    "Experiments",
  );
  const items = data;

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-experiments-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Experiments status
      </h3>
      {loading && (
        <div
          className="flex items-center gap-2 text-gray-500 py-4"
          data-testid="widget-loading"
        >
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading experiments...</span>
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
      {!loading && !error && items && items.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No experiments yet.
        </p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <div>
          <ul className="space-y-1">
            {items.map((exp) => (
              <li key={exp.id}>
                <Link
                  href={`/experiments/${exp.id}`}
                  className="flex items-center justify-between gap-2 rounded px-1 py-1 hover:bg-gray-50"
                >
                  <span className="truncate text-sm text-gray-800">{exp.name}</span>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${statusBadgeClass(
                      "experiment",
                      exp.status,
                    )}`}
                  >
                    {statusLabel("experiment", exp.status)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/experiments"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View all experiments
          </Link>
        </div>
      )}
    </div>
  );
}
