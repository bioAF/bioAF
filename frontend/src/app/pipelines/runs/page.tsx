"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { ReviewBadge } from "@/components/experiments/ReviewBadge";
import type { PipelineRun, PipelineRunListResponse, PipelineRunStatus } from "@/lib/types";

const STATUS_COLORS: Record<PipelineRunStatus, string> = {
  pending: "bg-gray-100 text-gray-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-orange-100 text-orange-700",
};

export default function PipelineRunsPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [sortField, setSortField] = useState<"id" | "status" | "pipeline_name">("id");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
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
    } catch {} finally { setLoading(false); }
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

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : (
          <>
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Pipeline Runs</h1>
            <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }} className="border rounded-md px-3 py-1.5 text-sm">
              <option value="">All statuses</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="pending">Pending</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>

          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer select-none hover:text-gray-700" onClick={() => toggleSort("id")}>Run{sortIcon("id")}</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer select-none hover:text-gray-700" onClick={() => toggleSort("pipeline_name")}>Pipeline{sortIcon("pipeline_name")}</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Experiment</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer select-none hover:text-gray-700" onClick={() => toggleSort("status")}>Status{sortIcon("status")}</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Review</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Progress</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Submitter</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Started</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Duration</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {sortedRuns.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => router.push(`/pipelines/runs/${r.id}`)}>
                    <td className="px-4 py-3 text-sm font-mono">#{r.id}</td>
                    <td className="px-4 py-3 text-sm">{r.pipeline_name}</td>
                    <td className="px-4 py-3 text-sm">{r.experiment?.name || "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_COLORS[r.status]}`}>{r.status}</span>
                    </td>
                    <td className="px-4 py-3">
                      {r.review_verdict ? <ReviewBadge verdict={r.review_verdict} /> : <span className="text-xs text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      {r.progress ? (
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <div className="h-full bg-bioaf-500 rounded-full" style={{ width: `${r.progress.percent_complete}%` }} />
                          </div>
                          <span className="text-xs text-gray-500">{Math.round(r.progress.percent_complete)}%</span>
                        </div>
                      ) : <span className="text-xs text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3 text-sm">{r.submitted_by?.name || r.submitted_by?.email || "—"}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{formatDateTime(r.started_at)}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{formatDuration(r.started_at, r.completed_at)}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{r.cost_estimate ? `$${r.cost_estimate.toFixed(2)}` : "—"}</td>
                  </tr>
                ))}
                {runs.length === 0 && (
                  <tr><td colSpan={10} className="px-4 py-12 text-center text-gray-400">No pipeline runs</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {total > 25 && (
            <div className="flex justify-center gap-2 mt-4">
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1} className="border px-3 py-1 rounded text-sm disabled:opacity-50">Previous</button>
              <span className="text-sm text-gray-500 py-1">Page {page} of {Math.ceil(total / 25)}</span>
              <button onClick={() => setPage(page + 1)} disabled={page >= Math.ceil(total / 25)} className="border px-3 py-1 rounded text-sm disabled:opacity-50">Next</button>
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}
