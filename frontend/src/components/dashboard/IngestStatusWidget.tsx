"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

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
    const timeout = setTimeout(() => setLoading(false), 30000);
    Promise.all([
      api.getWithRetry<{ total: number }>("/api/files?page_size=1").catch(() => ({ total: 0 })),
      api.getWithRetry<unknown[]>("/api/ingest/unmatched").catch(() => []),
      api.getWithRetry<unknown[]>("/api/ingest/unclaimed").catch(() => []),
    ])
      .then(([filesResp, unmatched, unclaimed]) => {
        setStats({
          totalFiles: (filesResp as { total: number }).total ?? 0,
          unmatched: Array.isArray(unmatched) ? unmatched.length : 0,
          unclaimed: Array.isArray(unclaimed) ? unclaimed.length : 0,
        });
      })
      .catch(() => setError("Failed to load ingest data"))
      .finally(() => { clearTimeout(timeout); setLoading(false); });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-ingest-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Ingest Status
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading ingest data...</span>
        </div>
      )}
      {error && !loading && (
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
