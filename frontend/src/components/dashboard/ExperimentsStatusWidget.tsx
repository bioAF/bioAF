"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface ExperimentItem {
  id: number;
  name: string;
  status: string;
}

interface ExperimentListResponse {
  experiments: ExperimentItem[];
  total: number;
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-green-100 text-green-700",
  in_progress: "bg-green-100 text-green-700",
  completed: "bg-blue-100 text-blue-700",
  draft: "bg-gray-100 text-gray-600",
  planned: "bg-gray-100 text-gray-600",
  archived: "bg-gray-100 text-gray-500",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-red-100 text-red-700",
};

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ExperimentsStatusWidget() {
  const [items, setItems] = useState<ExperimentItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<ExperimentListResponse>("/api/experiments?page_size=6")
      .then((res) => setItems(res.experiments || []))
      .catch(() => setError("Failed to load experiments"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-experiments-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Experiments status
      </h3>
      {loading && (
        <div
          className="flex items-center gap-2 text-gray-400 py-4"
          data-testid="widget-loading"
        >
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading experiments...</span>
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
      {!loading && !error && items && items.length === 0 && (
        <p className="text-sm text-gray-400" data-testid="widget-empty">
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
                    className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                      STATUS_STYLES[exp.status] || "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {statusLabel(exp.status)}
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
