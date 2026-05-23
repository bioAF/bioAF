"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo } from "@/components/dashboard/time";

interface Publication {
  id: number;
  dataset_name: string;
  status: string;
  created_at: string;
  published_at: string | null;
}

export function RecentCellxgeneWidget() {
  const [items, setItems] = useState<Publication[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<Publication[]>("/api/cellxgene")
      .then((res) => setItems(res || []))
      .catch(() => setError("Failed to load cellxgene"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-recent-cellxgene">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Recent cellxgene
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading datasets...</span>
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
          No published datasets yet.
        </p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <div>
          <ul className="space-y-1">
            {items.slice(0, 6).map((p) => (
              <li
                key={p.id}
                className="flex items-center justify-between gap-2 text-sm px-1 py-0.5"
              >
                <span className="truncate text-gray-800">{p.dataset_name}</span>
                <span className="shrink-0 text-xs text-gray-400">
                  {timeAgo(p.published_at || p.created_at)}
                </span>
              </li>
            ))}
          </ul>
          <Link
            href="/results/cellxgene"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View cellxgene
          </Link>
        </div>
      )}
    </div>
  );
}
