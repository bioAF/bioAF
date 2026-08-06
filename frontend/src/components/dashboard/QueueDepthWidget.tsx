"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface QueueData {
  queued: number;
  budget_queued: number;
}

export function QueueDepthWidget() {
  const [data, setData] = useState<QueueData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ runs: unknown[]; total: number }>("/api/pipeline-triggers/queue")
      .then((resp) => {
        setData({ queued: resp.total, budget_queued: resp.runs.length });
      })
      .catch(() => {
        setData({ queued: 0, budget_queued: 0 });
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-queue-depth">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Queue Depth
      </h3>
      {loading && (
        <div className="animate-pulse" data-testid="widget-loading">
          <div className="h-10 bg-gray-100 rounded" />
        </div>
      )}
      {error && (
        <div className="text-sm text-red-600" data-testid="widget-error">{error}</div>
      )}
      {!loading && !error && data && (
        <div>
          <div className="text-3xl font-bold text-gray-800">{data.queued}</div>
          <p className="text-sm text-gray-500 mt-1">pending jobs</p>
          {data.budget_queued > 0 && (
            <p className="text-xs text-amber-700 mt-1">
              {data.budget_queued} awaiting budget approval
            </p>
          )}
        </div>
      )}
      {!loading && !error && !data && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">No queued jobs.</p>
      )}
    </div>
  );
}
