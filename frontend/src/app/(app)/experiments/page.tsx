"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { buttonClass } from "@/components/ui/Button";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useResource } from "@/hooks/useResource";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { loadProjectOptions, PROJECTS_DENIED_MESSAGE } from "@/lib/projectOptions";
import type { Experiment, ExperimentListResponse, ExperimentStatus } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";
import { useToast } from "@/components/shared/Toast";
import { Card } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Input";
import { ListContent } from "@/components/shared/ListContent";
import { EmptyState } from "@/components/shared/EmptyState";
import { Pagination } from "@/components/shared/Pagination";
import { formatRecordedTime } from "@/lib/recordedTime";

export default function ExperimentsPage() {
  const toast = useToast();
  const router = useRouter();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [projectsDenied, setProjectsDenied] = useState(false);
  const pageSize = 25;

  useEffect(() => {
    loadProjectOptions("/api/projects").then((options) => {
      setProjects(options.projects.map((p) => ({ id: p.id, name: p.name })));
      setProjectsDenied(options.status === "denied");
      if (options.status === "failed") toast.error(loadFailureMessage("The project filter"));
    });
  }, [router]);

  // Keyed by the settled query, page, status, and project: typing waits for a pause, and only
  // the current key's answer is shown.
  const settledSearch = useDebouncedValue(search, 300);
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (settledSearch) params.set("search", settledSearch);
  if (statusFilter) params.set("status", statusFilter);
  if (projectFilter) params.set("project_id", projectFilter);
  const listPath = `/api/experiments?${params}`;
  const list = useResource(
    isAuthenticated() ? listPath : null,
    useCallback((signal: AbortSignal) => api.get<ExperimentListResponse>(listPath, { signal }), [listPath]),
    { onError: (e) => logError("loading experiments", e) },
  );
  const experiments: Experiment[] = list.data?.experiments ?? [];
  const total = list.data?.total ?? 0;
  const loading = list.loading;
  const loadError = list.error && !list.stale ? loadFailureMessage("Experiments") : null;

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
        <Link href="/experiments/new" className={buttonClass()}>
          New Experiment
        </Link>
      </div>

      <Card padding="sm" className="mb-6">
        <div className="flex flex-wrap gap-4">
          <Input aria-label="Search experiments"
            type="text"
            width="auto"
            placeholder="Search experiments..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="flex-1 min-w-[200px]"
          />
          <Select aria-label="Filter by status"
            width="auto"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          >
            <option value="">All Statuses</option>
            {statuses.map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </Select>
          <Select aria-label="Filter by project"
            width="auto"
            value={projectFilter}
            onChange={(e) => { setProjectFilter(e.target.value); setPage(1); }}
            disabled={projectsDenied}
            title={projectsDenied ? PROJECTS_DENIED_MESSAGE : undefined}
          >
            <option value="">All Projects</option>
            {projects.map((p) => (
              <option key={p.id} value={String(p.id)}>{p.name}</option>
            ))}
          </Select>
          {projectsDenied && (
            <p className="text-xs text-ink-subtle self-center">{PROJECTS_DENIED_MESSAGE}</p>
          )}
        </div>
      </Card>

      {list.stale && <StaleNotice what="Experiments" onRetry={() => void list.refresh()} />}

      <ListContent
        loading={loading}
        error={loadError}
        onRetry={() => void list.refresh()}
        empty={experiments.length === 0}
        emptyState={
          <EmptyState
            title="No experiments found"
            message="Get started by creating your first experiment."
            action={
              <Link href="/experiments/new" className={buttonClass()}>
                New Experiment
              </Link>
            }
          />
        }
      >
        <Card padding="none" className="overflow-x-auto">
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
                    {formatRecordedTime(exp.created_at, "date")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} />
      </ListContent>
    </main>
  );
}
