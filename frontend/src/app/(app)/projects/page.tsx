"use client";

import { NOT_SET } from "@/lib/placeholders";
import { Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { usePermissions } from "@/hooks/usePermissions";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useResource } from "@/hooks/useResource";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { api } from "@/lib/api";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Input, Select, Textarea } from "@/components/ui/Input";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { Project, ProjectListResponse } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";

import { clickableRow } from "@/lib/a11y";
import { Card } from "@/components/ui/Card";
import { ListContent } from "@/components/shared/ListContent";
import { EmptyState } from "@/components/shared/EmptyState";

export default function ProjectsPage() {
  return (
    <Suspense fallback={null}>
      <ProjectsPageInner />
    </Suspense>
  );
}

function ProjectsPageInner() {
  const toast = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newHypothesis, setNewHypothesis] = useState("");
  const [creating, setCreating] = useState(false);
  // `creating` drives what the button shows, but it only changes on the next
  // render. The ref closes the gap: a second submit before that render (Enter
  // pressed twice, a double click) finds the create already in flight.
  const createInFlight = useRef(false);
  const createFormId = useId();

  const { canAccess } = usePermissions();
  const canCreate = canAccess("projects", "create");

  // The list is keyed by the settled query and status: typing waits for a pause, and only the
  // current key's answer is shown.
  const settledSearch = useDebouncedValue(search, 300);
  const params = new URLSearchParams();
  if (settledSearch) params.set("search", settledSearch);
  if (statusFilter) params.set("status", statusFilter);
  const listPath = `/api/projects?${params}`;
  const list = useResource(
    listPath,
    useCallback((signal: AbortSignal) => api.get<ProjectListResponse>(listPath, { signal }), [listPath]),
    {
      // The old comment here claimed the api client handled this. It does not:
      // lib/api.ts only throws, and there was no notification layer to catch it.
      onError: (e) => logError("loading projects", e),
    },
  );
  const projects: Project[] = list.data?.projects ?? [];
  const loading = list.loading;
  const loadError = list.error && !list.stale ? loadFailureMessage("Projects") : null;
  const loadProjects = list.refresh;

  // The header "+ New > New Project" lands here with ?new=1 to open the create
  // form immediately (only for users who can create projects).
  useEffect(() => {
    if (searchParams?.get("new") === "1" && canCreate) {
      setShowCreateModal(true);
    }
  }, [searchParams, canCreate]);

  const handleCreate = async (e: FormEvent<HTMLFormElement>) => {
    // Always, before anything else: a form with no action would otherwise make
    // the browser submit it as a GET to this page, putting the fields in the URL.
    e.preventDefault();
    if (!newName.trim() || createInFlight.current) return;
    createInFlight.current = true;
    setCreating(true);
    try {
      await api.post("/api/projects", {
        name: newName,
        hypothesis: newHypothesis || null,
      });
      setShowCreateModal(false);
      setNewName("");
      setNewHypothesis("");
      void loadProjects();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the project.");
    } finally {
      createInFlight.current = false;
      setCreating(false);
    }
  };

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Project List</h1>
            <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
              Projects group related experiments, and the samples and files produced under them.
            </p>
          </div>
          {canCreate && (
            <Button
              onClick={() => setShowCreateModal(true)}>
              New Project
            </Button>
          )}
        </div>

        <Card padding="sm" className="mb-6">
          <div className="flex flex-wrap gap-4">
            <Input aria-label="Search projects"
              type="text"
              width="auto"
              placeholder="Search projects..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 min-w-[200px]"
            />
            <Select aria-label="Filter by status"
              width="auto"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="complete">Complete</option>
            </Select>
          </div>
        </Card>

        {list.stale && <StaleNotice what="Projects" onRetry={() => void loadProjects()} />}

        <ListContent
          loading={loading}
          error={loadError}
          onRetry={() => void loadProjects()}
          empty={projects.length === 0}
          emptyState={
            <EmptyState
              title="No projects found"
              message="Create a project to organize cross-experiment analysis."
              action={canCreate && <Button onClick={() => setShowCreateModal(true)}>New Project</Button>}
            />
          }
        >
          <Card padding="none" className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Owner</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Samples</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Experiments</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Runs</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {projects.map((p) => (
                  <tr
                    key={p.id}
                    {...clickableRow(() => router.push(`/projects/${p.id}`))}
                    className="hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-6 py-4 text-sm font-medium text-gray-900">{p.name}</td>
                    <td className="px-6 py-4 text-sm text-gray-500 font-mono">{p.external_id || p.code || "-"}</td>
                    <td className="px-6 py-4">
                      <StatusBadge status={p.status || "active"} />
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">{p.owner_name || NOT_SET}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{p.sample_count}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{p.experiment_count}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{p.pipeline_run_count}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {new Date(p.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </ListContent>
      </main>

      <Modal
        open={showCreateModal}
        title="New Project"
        onClose={() => setShowCreateModal(false)}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowCreateModal(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              form={createFormId}
              disabled={!newName.trim()}
              busy={creating}
              busyLabel="Creating..."
            >
              Create Project
            </Button>
          </>
        }
      >
        <form id={createFormId} onSubmit={handleCreate} className="space-y-4">
          <Field label="Name">
            {(control) => (
              <Input
                {...control}
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                placeholder="e.g., GBM vs. Healthy Integration Atlas"
              />
            )}
          </Field>
          <Field label="Hypothesis (optional)">
            {(control) => (
              <Textarea
                {...control}
                value={newHypothesis}
                onChange={(e) => setNewHypothesis(e.target.value)}
                rows={3}
                placeholder="What are you investigating?"
              />
            )}
          </Field>
        </form>
      </Modal>
    </>
  );
}
