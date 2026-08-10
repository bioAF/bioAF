"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { Experiment, ExperimentListResponse, ExperimentStatus, ProjectListResponse } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";
import { useToast } from "@/components/shared/Toast";
import { Card } from "@/components/ui/Card";

export default function ExperimentsPage() {
  const toast = useToast();
  const router = useRouter();
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by Retry: the load lives in an effect keyed on the filters, so this
  // is what re-triggers it without changing what the user asked for.
  const [reloadKey, setReloadKey] = useState(0);
  const pageSize = 25;

  useEffect(() => {
    api.get<ProjectListResponse>("/api/projects").then((data) => {
      setProjects(data.projects.map((p) => ({ id: p.id, name: p.name })));
    }).catch((e) => {
      logError("loading the project filter", e);
      toast.error(loadFailureMessage("The project filter"));
    });
  }, [router]);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);

    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (search) params.set("search", search);
    if (statusFilter) params.set("status", statusFilter);
    if (projectFilter) params.set("project_id", projectFilter);

    setLoadError(null);
    api.get<ExperimentListResponse>(`/api/experiments?${params}`)
      .then((data) => {
        setExperiments(data.experiments);
        setTotal(data.total);
      })
      .catch((e) => {
        logError("loading experiments", e);
        setLoadError(loadFailureMessage("Experiments"));
      })
      .finally(() => setLoading(false));
  }, [page, search, statusFilter, projectFilter, reloadKey]);

  const totalPages = Math.ceil(total / pageSize);

  const statuses: ExperimentStatus[] = [
    "registered", "library_prep", "sequencing", "fastq_uploaded",
    "processing", "analysis", "complete",
  ];

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Experiment List</h1>
          <p data-testid="page-description" className="text-sm text-ink-subtle mt-1">
            Every experiment registered in this instance, with its status, project and linked data.
          </p>
        </div>
        <Link
          href="/experiments/new"
          className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 transition-colors"
        >
          New Experiment
        </Link>
      </div>

      <div className="bg-surface rounded-lg shadow mb-6 p-4">
        <div className="flex flex-wrap gap-4">
          <input aria-label="Search experiments"
            type="text"
            placeholder="Search experiments..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded-md px-3 py-2 text-sm flex-1 min-w-[200px]"
          />
          <select aria-label="Filter by status"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
            className="border border-gray-300 rounded-md px-3 py-2 text-sm"
          >
            <option value="">All Statuses</option>
            {statuses.map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </select>
          <select aria-label="Filter by project"
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
      ) : loadError ? (
        <Card padding="none">
          <ErrorState
            message={loadError}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        </Card>
      ) : experiments.length === 0 ? (
        <div className="bg-surface rounded-lg shadow p-12 text-center">
          <h2 className="text-lg font-semibold text-ink-subtle mb-2">No experiments found</h2>
          <p className="text-ink-subtle mb-4">Get started by creating your first experiment.</p>
          <Link
            href="/experiments/new"
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
          >
            New Experiment
          </Link>
        </div>
      ) : (
        <>
          <div className="bg-surface rounded-lg shadow overflow-x-auto">
            <table className="min-w-full divide-y divide-hairline">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Name</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">ID</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Project</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Owner</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Samples</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Created</th>
                </tr>
              </thead>
              <tbody className="bg-surface divide-y divide-hairline">
                {experiments.map((exp) => (
                  <tr
                    key={exp.id}
                    {...clickableRow(() => router.push(`/experiments/${exp.id}`))}
                    className="hover:bg-surface-muted cursor-pointer"
                  >
                    <td className="px-6 py-4 text-sm font-medium text-ink">{exp.name}</td>
                    <td className="px-6 py-4 text-sm text-ink-subtle font-mono">{exp.external_id || exp.code || "-"}</td>
                    <td className="px-6 py-4 text-sm text-ink-subtle">{exp.project?.name || NOT_SET}</td>
                    <td className="px-6 py-4">
                      <ExperimentStatusBadge status={exp.status} />
                    </td>
                    <td className="px-6 py-4 text-sm text-ink-subtle">{exp.owner?.name || exp.owner?.email || NOT_SET}</td>
                    <td className="px-6 py-4 text-sm text-ink-subtle">{exp.sample_count}</td>
                    <td className="px-6 py-4 text-sm text-ink-subtle">
                      {new Date(exp.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex justify-between items-center mt-4">
              <p className="text-sm text-ink-subtle">
                Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage(Math.max(1, page - 1))}
                  disabled={page === 1}
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage(Math.min(totalPages, page + 1))}
                  disabled={page === totalPages}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </main>
  );
}
