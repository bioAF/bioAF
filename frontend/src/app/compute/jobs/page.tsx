"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import type { SlurmJob, SlurmJobListResponse, SlurmJobStatus } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-800",
  running: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  cancelled: "bg-orange-100 text-orange-800",
  timeout: "bg-yellow-100 text-yellow-800",
};

export default function JobBrowserPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<SlurmJob[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [partitionFilter, setPartitionFilter] = useState<string>("");
  const [expandedJob, setExpandedJob] = useState<number | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadJobs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, statusFilter, partitionFilter, router]);

  async function loadJobs() {
    setLoading(true);
    try {
      let url = `/api/compute/jobs?page=${page}&page_size=25`;
      if (statusFilter) url += `&status=${statusFilter}`;
      if (partitionFilter) url += `&partition=${partitionFilter}`;
      const data = await api.get<SlurmJobListResponse>(url);
      setJobs(data.jobs);
      setTotal(data.total);
    } catch {
    } finally {
      setLoading(false);
    }
  }

  async function handleCancel(jobId: number) {
    try {
      await api.post(`/api/compute/jobs/${jobId}/cancel`);
      loadJobs();
    } catch {}
  }

  async function handleResubmit(jobId: number) {
    try {
      await api.post(`/api/compute/jobs/${jobId}/resubmit`);
      loadJobs();
    } catch {}
  }

  const totalPages = Math.ceil(total / 25);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center gap-4 mb-6">
            <Link href="/infrastructure/compute" className="text-gray-500 hover:text-gray-700">← Cluster</Link>
            <h1 className="text-2xl font-bold">Job Browser</h1>
            <span className="text-sm text-gray-500">({total} jobs)</span>
          </div>

          {/* Filters */}
          <div className="flex gap-4 mb-4">
            <select
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
              className="border rounded px-3 py-2 text-sm"
            >
              <option value="">All Statuses</option>
              <option value="pending">Pending</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
            <select
              value={partitionFilter}
              onChange={(e) => { setPartitionFilter(e.target.value); setPage(1); }}
              className="border rounded px-3 py-2 text-sm"
            >
              <option value="">All Partitions</option>
              <option value="standard">Standard</option>
              <option value="interactive">Interactive</option>
            </select>
          </div>

          {loading ? (
            <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
          ) : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Job ID</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Partition</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">CPU/Mem</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Cost</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {jobs.map((job) => (
                    <>
                      <tr
                        key={job.id}
                        className="hover:bg-gray-50 cursor-pointer"
                        onClick={() => setExpandedJob(expandedJob === job.id ? null : job.id)}
                      >
                        <td className="px-4 py-3 text-sm font-mono">{job.slurm_job_id}</td>
                        <td className="px-4 py-3 text-sm">{job.job_name || "—"}</td>
                        <td className="px-4 py-3 text-sm">{job.user?.name || job.user?.email || "—"}</td>
                        <td className="px-4 py-3 text-sm capitalize">{job.partition}</td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-1 rounded ${STATUS_COLORS[job.status] || "bg-gray-100"}`}>
                            {job.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm">
                          {job.cpu_requested && `${job.cpu_requested} CPU / ${job.memory_gb_requested}GB`}
                        </td>
                        <td className="px-4 py-3 text-sm">
                          {job.cost_estimate != null ? `$${job.cost_estimate.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            {(job.status === "pending" || job.status === "running") && (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleCancel(job.id); }}
                                className="text-xs text-red-600 hover:text-red-800"
                              >
                                Cancel
                              </button>
                            )}
                            {(job.status === "completed" || job.status === "failed") && (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleResubmit(job.id); }}
                                className="text-xs text-bioaf-600 hover:text-bioaf-800"
                              >
                                Resubmit
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {expandedJob === job.id && (
                        <tr key={`${job.id}-detail`}>
                          <td colSpan={8} className="px-4 py-4 bg-gray-50">
                            <div className="grid grid-cols-3 gap-4 text-sm">
                              <div>
                                <span className="text-gray-500">Requested:</span>{" "}
                                {job.cpu_requested} CPU, {job.memory_gb_requested}GB
                              </div>
                              <div>
                                <span className="text-gray-500">Used:</span>{" "}
                                {job.cpu_used != null ? `${job.cpu_used} CPU, ${job.memory_gb_used}GB` : "—"}
                              </div>
                              <div>
                                <span className="text-gray-500">Exit Code:</span>{" "}
                                {job.exit_code ?? "—"}
                              </div>
                              <div>
                                <span className="text-gray-500">Submitted:</span>{" "}
                                {new Date(job.submitted_at).toLocaleString()}
                              </div>
                              <div>
                                <span className="text-gray-500">Started:</span>{" "}
                                {job.started_at ? new Date(job.started_at).toLocaleString() : "—"}
                              </div>
                              <div>
                                <span className="text-gray-500">Completed:</span>{" "}
                                {job.completed_at ? new Date(job.completed_at).toLocaleString() : "—"}
                              </div>
                              {job.experiment && (
                                <div>
                                  <span className="text-gray-500">Experiment:</span>{" "}
                                  <Link href={`/experiments/${job.experiment.id}`} className="text-bioaf-600 hover:underline">
                                    {job.experiment.name}
                                  </Link>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                  {jobs.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">No jobs found</td></tr>
                  )}
                </tbody>
              </table>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between p-4 border-t">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page <= 1}
                    className="px-3 py-1 text-sm border rounded disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <span className="text-sm text-gray-500">Page {page} of {totalPages}</span>
                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page >= totalPages}
                    className="px-3 py-1 text-sm border rounded disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
