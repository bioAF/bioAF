"use client";

import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";

interface QueueData {
  queued: number;
  budget_queued: number;
}

/**
 * How deep the pipeline queue is.
 *
 * This widget used to answer "we could not reach the backend" with the number 0.
 * Measured on the deployed app under a total outage, it rendered "0 / pending
 * jobs", byte-identical to a genuinely empty queue, because its catch did
 * `setData({ queued: 0, budget_queued: 0 })`. It also declared `setError` and
 * never called it, so its own error branch was unreachable dead code and there
 * was no Retry.
 *
 * On a platform where the queue is how you know whether compute is backed up, a
 * fabricated zero during an outage reads as a healthy idle cluster. It now shares
 * `useWidgetData` with the rest of the dashboard, which logs the real error, shows
 * the house sentence, and refetches this card alone.
 */
export function QueueDepthWidget() {
  const { data, loading, error, retry } = useWidgetData<QueueData>(
    async () => {
      const resp = await api.get<{ runs: unknown[]; total: number }>(
        "/api/pipeline-triggers/queue",
      );
      return { queued: resp.total, budget_queued: resp.runs.length };
    },
    "Queue depth",
  );

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
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button onClick={retry} className="ml-2 text-bioaf-600 hover:underline">
            Retry
          </button>
        </div>
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
