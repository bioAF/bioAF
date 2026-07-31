"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface Paper {
  id: number;
  title: string;
  journal: string | null;
}

interface PaperList {
  items: Paper[];
  total: number;
}

export function MyReadingListWidget() {
  const [items, setItems] = useState<Paper[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<PaperList>("/api/literature/papers?reading_status=unread&page_size=6")
      .then((res) => setItems(res.items || []))
      .catch(() => setError("Failed to load reading list"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-my-reading-list">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        My reading list
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading papers...</span>
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
          No unread papers.
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
                  <span className="block truncate text-xs text-gray-400">
                    {p.journal || "Unknown journal"}
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
