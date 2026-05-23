"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface Tier {
  tier: string;
  name: string;
  status: string;
}

interface BackupStatus {
  tiers: Tier[];
  overall_status: string;
}

function statusStyle(status: string): string {
  const s = (status || "").toLowerCase();
  if (["healthy", "ok", "completed", "active", "enabled"].includes(s)) {
    return "bg-green-100 text-green-700";
  }
  if (["degraded", "warning", "pending"].includes(s)) {
    return "bg-amber-100 text-amber-700";
  }
  if (["failed", "error", "disabled"].includes(s)) {
    return "bg-red-100 text-red-700";
  }
  return "bg-gray-100 text-gray-600";
}

export function BackupStatusWidget() {
  const [data, setData] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<BackupStatus>("/api/backups/status")
      .then((res) => setData({ tiers: res.tiers || [], overall_status: res.overall_status }))
      .catch(() => setError("Failed to load backup status"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-backup-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Backup status
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading backups...</span>
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
      {!loading && !error && data && (
        <div>
          <span
            className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${statusStyle(
              data.overall_status,
            )}`}
          >
            {data.overall_status || "unknown"}
          </span>
          {data.tiers.length > 0 && (
            <ul className="mt-3 space-y-1">
              {data.tiers.map((t) => (
                <li key={t.tier} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate text-gray-700">{t.name}</span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${statusStyle(t.status)}`}>
                    {t.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link
            href="/infrastructure/backup"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View backups
          </Link>
        </div>
      )}
    </div>
  );
}
