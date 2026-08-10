import type { SearchSummary } from "@/lib/literature";

/**
 * Progress of a multi-source literature search, derived from the per-source
 * status the poll already returns. A source is "done" once it reaches a terminal
 * state (complete or failed), so the count is honest: it reflects real milestones
 * (4 discrete sources), never a made-up time percentage.
 */
export function searchSourceProgress(
  perSourceStatus: Record<string, string>,
): { done: number; total: number } {
  const values = Object.values(perSourceStatus);
  return {
    done: values.filter((s) => s === "complete" || s.startsWith("failed")).length,
    total: values.length,
  };
}

/** Chip color for a single source status: green complete, red failed, gray in-flight. */
export function sourceChipClass(status: string): string {
  const base = "px-2 py-0.5 text-xs rounded";
  if (status === "complete") return `${base} bg-green-100 text-green-700`;
  if (status.startsWith("failed")) return `${base} bg-red-100 text-red-700`;
  return `${base} bg-gray-100 text-gray-700`;
}

/**
 * Live progress panel shown while a search is running: an honest "N of M sources"
 * bar (the sources are discrete, so this is determinate), the per-source chips,
 * and a Stop-watching control. Stopping only stops the client from waiting; the
 * search continues server-side and its results land in the list when it finishes.
 */
export function SearchProgress({
  status,
  onStop,
}: {
  status: SearchSummary;
  onStop: () => void;
}) {
  const { done, total } = searchSourceProgress(status.per_source_status);
  const pct = total ? Math.round((done / total) * 100) : 0;

  return (
    <div className="bg-white rounded shadow p-4 mb-6" data-testid="search-progress">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-800">
          Searching {done} of {total} sources...
        </span>
        <button
          type="button"
          onClick={onStop}
          className="text-sm text-gray-600 hover:text-gray-900 underline"
        >
          Stop watching
        </button>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded bg-gray-100"
        role="progressbar"
        aria-label="Search progress by source"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
      >
        <div
          className="h-full bg-bioaf-600 transition-[width] duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {Object.entries(status.per_source_status).map(([source, st]) => (
          <span key={source} className={sourceChipClass(st)}>
            {source}: {st}
          </span>
        ))}
      </div>
      <p className="mt-2 text-xs text-gray-500">
        The search keeps running if you stop watching; results appear in the list below when it finishes.
      </p>
    </div>
  );
}
