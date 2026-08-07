"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";
import { useCapabilities } from "@/hooks/useCapabilities";
import { ReviewBadge } from "@/components/experiments/ReviewBadge";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { PipelineRun, PipelineRunListResponse } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";

export default function PipelineRunsPage() {
  const router = useRouter();
  const { has } = useCapabilities();
  const showCost = has("cost_estimation");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [sortField, setSortField] = useState<"id" | "status" | "pipeline_name">("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    loadRuns();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, page, statusFilter]);

  async function loadRuns() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: "25" });
      if (statusFilter) params.set("status", statusFilter);
      const data = await api.get<PipelineRunListResponse>(`/api/pipeline-runs?${params}`);
      setRuns(data.runs);
      setTotal(data.total);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Could not load pipeline runs.");
    } finally { setLoading(false); }
  }

  function formatDuration(startedAt: string | null, completedAt: string | null): string {
    if (!startedAt) return "—";
    const start = new Date(startedAt).getTime();
    const end = completedAt ? new Date(completedAt).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function formatDateTime(dateStr: string | null): string {
    if (!dateStr) return "—";
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
      d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }

  function toggleSort(field: "id" | "status" | "pipeline_name") {
    if (sortField === field) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  }

  const sortedRuns = [...runs].sort((a, b) => {
    let cmp = 0;
    if (sortField === "id") cmp = a.id - b.id;
    else if (sortField === "status") cmp = a.status.localeCompare(b.status);
    else if (sortField === "pipeline_name") cmp = a.pipeline_name.localeCompare(b.pipeline_name);
    return sortDir === "desc" ? -cmp : cmp;
  });

  const sortIcon = (field: string) => sortField === field ? (sortDir === "desc" ? " ↓" : " ↑") : "";

  // Sortable headers were mouse-only, and the sort direction was conveyed
  // solely by the arrow glyph above. `aria-sort` is the machine-readable
  // equivalent, so the column announces "sorted descending" rather than leaving
  // a screen reader to guess from a character appended to the label.
  const sortProps = (field: "id" | "status" | "pipeline_name") => ({
    ...clickableRow(() => toggleSort(field)),
    "aria-sort": (sortField === field
      ? sortDir === "desc"
        ? "descending"
        : "ascending"
      : "none") as "descending" | "ascending" | "none",
  });

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {loading ? (
        <ContentLoading />
      ) : (
      <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Pipeline Runs</h1>
        <select aria-label="Filter by status" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }} className="border rounded-md px-3 py-1.5 text-sm">
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      <div className="bg-surface rounded-lg shadow overflow-hidden">
        <table className="min-w-full divide-y divide-hairline">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("id")}>Run{sortIcon("id")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("pipeline_name")}>Pipeline{sortIcon("pipeline_name")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Experiment</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("status")}>Status{sortIcon("status")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Review</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Progress</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Submitter</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Started</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Duration</th>
              {showCost && <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Est. $/hr</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {sortedRuns.map((r) => (
              <tr key={r.id} className="hover:bg-surface-muted cursor-pointer" {...clickableRow(() => router.push(`/pipelines/runs/${r.id}`))}>
                <td className="px-4 py-3 text-sm font-mono">#{r.id}</td>
                <td className="px-4 py-3 text-sm">{r.pipeline_name}</td>
                <td className="px-4 py-3 text-sm">{r.experiment?.name || "—"}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 text-xs rounded-full ${statusBadgeClass("pipelineRun", r.status)}`}>{r.status}</span>
                  {r.status === "failed" && r.failure_reason === "oom" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-orange-100 text-orange-700">OOM</span>
                  )}
                  {r.status === "failed" && r.failure_reason === "preemption_exhausted" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-blue-100 text-blue-700">Preempted</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {r.review_verdict ? <ReviewBadge verdict={r.review_verdict} /> : <span className="text-xs text-ink-subtle">—</span>}
                </td>
                <td className="px-4 py-3">
                  {r.progress ? (
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div className="h-full bg-bioaf-500 rounded-full" style={{ width: `${r.progress.percent_complete}%` }} />
                      </div>
                      <span className="text-xs text-ink-subtle">{Math.round(r.progress.percent_complete)}%</span>
                    </div>
                  ) : <span className="text-xs text-ink-subtle">—</span>}
                </td>
                <td className="px-4 py-3 text-sm">{r.submitted_by?.name || r.submitted_by?.email || "—"}</td>
                <td className="px-4 py-3 text-sm text-ink-subtle">{formatDateTime(r.started_at)}</td>
                <td className="px-4 py-3 text-sm text-ink-subtle">{formatDuration(r.started_at, r.completed_at)}</td>
                {showCost && <td className="px-4 py-3 text-sm text-ink-subtle">{r.cost_estimate ? `$${r.cost_estimate.toFixed(2)}/hr` : "—"}</td>}
              </tr>
            ))}
            {loadError ? (
              <tr>
                <td colSpan={showCost ? 10 : 9} className="px-4 py-12 text-center">
                  <p className="text-red-700 mb-3">Could not load pipeline runs. {loadError}</p>
                  <Button variant="secondary" size="sm" onClick={() => loadRuns()}>
                    Retry
                  </Button>
                </td>
              </tr>
            ) : runs.length === 0 ? (
              <tr><td colSpan={showCost ? 10 : 9} className="px-4 py-12 text-center text-ink-subtle">No pipeline runs</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {total > 25 && (
        <div className="flex justify-center gap-2 mt-4">
          <Button variant="secondary" size="sm" onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1}>Previous</Button>
          <span className="text-sm text-ink-subtle py-1">Page {page} of {Math.ceil(total / 25)}</span>
          <Button variant="secondary" size="sm" onClick={() => setPage(page + 1)} disabled={page >= Math.ceil(total / 25)}>Next</Button>
        </div>
      )}
      </>
      )}
    </main>
  );
}
