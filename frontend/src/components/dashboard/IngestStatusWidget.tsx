"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface FileGroup {
  total: number;
  by_type: Record<string, number>;
}

interface FileStats {
  artifacts: FileGroup;
  uploaded: FileGroup;
}

/**
 * What is in file storage.
 *
 * Two defects, both closed here. It reported failure with `setError("Failed to
 * load file stats")` and **no Retry at all**, so a transient blip cost the user a
 * whole page refresh; and that catch discarded the error entirely, making this one
 * of the last two widgets not following the rule that the screen gets a plain
 * sentence and the log gets the real thing.
 *
 * It was also absent from `registry.tsx` despite being fully built, tested, and
 * named in the Getting Started tour, so no user could ever add it. Registered now.
 */
export function IngestStatusWidget() {
  const { data: stats, loading, error, retry } = useWidgetData<FileStats>(
    () => api.getWithRetry<FileStats>("/api/files/stats"),
    "File inventory",
  );

  function renderGroup(label: string, group: FileGroup) {
    return (
      <div>
        <div className="flex justify-between mb-1">
          <span className="text-sm font-medium text-gray-700">{label}</span>
          <span className="text-sm font-semibold">{group.total}</span>
        </div>
        {Object.entries(group.by_type).map(([type, count]) => (
          <div key={type} className="flex justify-between pl-4">
            <span className="text-xs text-gray-500 uppercase">{type}</span>
            <span className="text-xs text-gray-500">{count}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-ingest-status">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        File Inventory
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading file stats...</span>
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
      {!loading && !error && stats && (stats.artifacts?.total ?? 0) === 0 && (stats.uploaded?.total ?? 0) === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">No files yet.</p>
      )}
      {!loading && !error && stats && ((stats.artifacts?.total ?? 0) > 0 || (stats.uploaded?.total ?? 0) > 0) && (
        <div className="space-y-3">
          {(stats.artifacts?.total ?? 0) > 0 && renderGroup("Artifacts", stats.artifacts)}
          {(stats.uploaded?.total ?? 0) > 0 && renderGroup("Uploaded", stats.uploaded)}
          <Link href="/data/files" className="text-xs text-bioaf-600 hover:underline block">
            View all files
          </Link>
        </div>
      )}
    </div>
  );
}
