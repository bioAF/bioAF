"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface AutoIngest {
  enabled: boolean;
  messages_processed_24h: number;
  messages_failed_24h: number;
}

export function AutoIngestActivityWidget() {
  const [data, setData] = useState<AutoIngest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<AutoIngest>("/api/v1/settings/auto-ingest")
      .then((res) => setData(res))
      .catch(() => setError("Failed to load auto-ingest"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-auto-ingest">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Auto-ingest activity
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading activity...</span>
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
            className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
              data.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
            }`}
          >
            {data.enabled ? "Enabled" : "Disabled"}
          </span>
          <div className="mt-3 flex gap-8">
            <div>
              <div className="text-3xl font-bold text-bioaf-600">
                {data.messages_processed_24h}
              </div>
              <p className="text-xs text-gray-500 mt-1">processed (24h)</p>
            </div>
            <div>
              <div
                className={`text-3xl font-bold ${
                  data.messages_failed_24h > 0 ? "text-red-600" : "text-gray-500"
                }`}
              >
                {data.messages_failed_24h}
              </div>
              <p className="text-xs text-gray-500 mt-1">failed (24h)</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
