"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { timeAgo } from "@/components/dashboard/time";

interface Paper {
  id: number;
  title: string;
  journal: string | null;
  created_at: string;
  comment_count: number;
}

interface PaperList {
  items: Paper[];
  total: number;
}

export function RecentLiteratureWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () => {
      const res = await api.getWithRetry<PaperList>("/api/literature/papers?sort=added&page_size=6");
      return res.items || [];
    },
    "Literature",
  );
  const items = data;

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-recent-literature">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Recent literature
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading papers...</span>
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
          No papers shared yet.
        </p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <div>
          <ul className="space-y-1">
            {items.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/lab-knowledge/literature/papers/${p.id}`}
                  className="block rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  <span className="block truncate text-sm text-gray-800">{p.title}</span>
                  <span className="block truncate text-xs text-gray-500">
                    {p.journal || "Unknown journal"} &middot; {timeAgo(p.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/lab-knowledge/literature"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View literature
          </Link>
        </div>
      )}
    </div>
  );
}
