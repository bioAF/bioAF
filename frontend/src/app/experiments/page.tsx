"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import type { Experiment, ExperimentListResponse, ExperimentStatus, ProjectListResponse } from "@/lib/types";

export default function ExperimentsPage() {
  const router = useRouter();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const pageSize = 25;

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    api.get<ProjectListResponse>("/api/projects").then((data) => {
      setProjects(data.projects.map((p) => ({ id: p.id, name: p.name })));
    }).catch(() => {});
  }, [router]);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);

    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (search) params.set("search", search);
    if (statusFilter) params.set("status", statusFilter);
    if (projectFilter) params.set("project_id", projectFilter);

    api.get<ExperimentListResponse>(`/api/experiments?${params}`)
      .then((data) => {
        setExperiments(data.experiments);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, search, statusFilter, projectFilter]);

  const totalPages = Math.ceil(total / pageSize);

  const statuses: ExperimentStatus[] = [
    "registered", "library_prep", "sequencing", "fastq_uploaded",
    "processing", "analysis", "complete",
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Experiments</h1>
            <Link
              href="/experiments/new"
              className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 transition-colors"
            >
              New Experiment
            </Link>
          </div>

          <div className="bg-white rounded-lg shadow mb-6 p-4">
            <div className="flex flex-wrap gap-4">
              <input
                type="text"
                placeholder="Search experiments..."
                value={search}
                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm flex-1 min-w-[200px]"
              />
              <select
                value={statusFilter}
                onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="">All Statuses</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
                ))}
              </select>
              <select
                value={projectFilter}
                onChange={(e) => { setProjectFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="">All Projects</option>
                {projects.map((p) => (
                  <option key={p.id} value={String(p.id)}>{p.name}</option>
                ))}
              </select>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner size="lg" />
            </div>
          ) : experiments.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-12 text-center">
              <h2 className="text-lg font-semibold text-gray-400 mb-2">No experiments found</h2>
              <p className="text-gray-400 mb-4">Get started by creating your first experiment.</p>
              <Link
                href="/experiments/new"
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
              >
                New Experiment
              </Link>
            </div>
          ) : (
            <>
              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Code</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Project</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Owner</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Samples</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {experiments.map((exp) => (
                      <tr
                        key={exp.id}
                        onClick={() => router.push(`/experiments/${exp.id}`)}
                        className="hover:bg-gray-50 cursor-pointer"
                      >
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{exp.name}</td>
                        <td className="px-6 py-4 text-sm text-gray-500 font-mono">{exp.code || "—"}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">{exp.project?.name || "—"}</td>
                        <td className="px-6 py-4">
                          <ExperimentStatusBadge status={exp.status} />
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500">{exp.owner?.name || exp.owner?.email || "—"}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">{exp.sample_count}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">
                          {new Date(exp.created_at).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="flex justify-between items-center mt-4">
                  <p className="text-sm text-gray-500">
                    Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setPage(Math.max(1, page - 1))}
                      disabled={page === 1}
                      className="px-3 py-1 border rounded text-sm disabled:opacity-50"
                    >
                      Previous
                    </button>
                    <button
                      onClick={() => setPage(Math.min(totalPages, page + 1))}
                      disabled={page === totalPages}
                      className="px-3 py-1 border rounded text-sm disabled:opacity-50"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
