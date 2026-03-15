"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface IngestStats {
  totalFiles: number;
  unmatched: number;
  unclaimed: number;
}

export function IngestStatusWidget() {
  const [stats, setStats] = useState<IngestStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get<{ total: number }>("/api/files?page_size=1").catch(() => ({ total: 0 })),
      api.get<unknown[]>("/api/ingest/unmatched").catch(() => []),
      api.get<unknown[]>("/api/ingest/unclaimed").catch(() => []),
    ])
      .then(([filesResp, unmatched, unclaimed]) => {
        setStats({
          totalFiles: (filesResp as { total: number }).total ?? 0,
          unmatched: Array.isArray(unmatched) ? unmatched.length : 0,
          unclaimed: Array.isArray(unclaimed) ? unclaimed.length : 0,
        });
      })
      .catch(() => setError("Failed to load ingest data"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-ingest-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Ingest Status
      </h3>
      {loading && (
        <div className="animate-pulse space-y-2" data-testid="widget-loading">
          <div className="h-6 bg-gray-100 rounded w-1/2" />
          <div className="h-4 bg-gray-100 rounded w-3/4" />
        </div>
      )}
      {error && (
        <div className="text-sm text-red-600" data-testid="widget-error">{error}</div>
      )}
      {!loading && !error && !stats && (
        <p className="text-sm text-gray-400" data-testid="widget-empty">No ingest activity.</p>
      )}
      {!loading && !error && stats && (
        <div className="space-y-2">
          <div className="flex justify-between">
            <span className="text-sm text-gray-600">Files ingested</span>
            <span className="text-sm font-medium">{stats.totalFiles}</span>
          </div>
          {stats.unmatched > 0 && (
            <div className="flex justify-between">
              <span className="text-sm text-amber-600">Unmatched</span>
              <span className="text-sm font-medium text-amber-600">{stats.unmatched}</span>
            </div>
          )}
          {stats.unclaimed > 0 && (
            <div className="flex justify-between">
              <span className="text-sm text-amber-600">Unclaimed</span>
              <span className="text-sm font-medium text-amber-600">{stats.unclaimed}</span>
            </div>
          )}
          <Link href="/data/upload" className="text-xs text-bioaf-600 hover:underline">
            View ingest activity
          </Link>
        </div>
      )}
    </div>
  );
}
