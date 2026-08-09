"use client";

import { NOT_SET } from "@/lib/placeholders";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { Project, ProjectListResponse } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";

import { clickableRow } from "@/lib/a11y";

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newHypothesis, setNewHypothesis] = useState("");
  const [creating, setCreating] = useState(false);

  const user = getCurrentUser();
  const canCreate = user?.role_name === "admin" || user?.role_name === "comp_bio";

  useEffect(() => {
    loadProjects();
  }, [router, search, statusFilter]);

  // The header "+ New > New Project" lands here with ?new=1 to open the create
  // form immediately (only for users who can create projects).
  useEffect(() => {
    if (searchParams?.get("new") === "1" && canCreate) {
      setShowCreateModal(true);
    }
  }, [searchParams, canCreate]);

  const loadProjects = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (statusFilter) params.set("status", statusFilter);
      const data = await api.get<ProjectListResponse>(`/api/projects?${params}`);
      setProjects(data.projects);
      setLoadError(null);
    } catch (e) {
      // The old comment here claimed the api client handled this. It does not:
      // lib/api.ts only throws, and there was no notification layer to catch it.
      logError("loading projects", e);
      setLoadError(loadFailureMessage("Projects"));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.post("/api/projects", {
        name: newName,
        hypothesis: newHypothesis || null,
      });
      setShowCreateModal(false);
      setNewName("");
      setNewHypothesis("");
      loadProjects();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the project.");
    } finally {
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
            <button
              onClick={() => setShowCreateModal(true)}
              className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 transition-colors"
            >
              New Project
            </button>
          )}
        </div>

        <div className="bg-white rounded-lg shadow mb-6 p-4">
          <div className="flex flex-wrap gap-4">
            <input aria-label="Search projects"
              type="text"
              placeholder="Search projects..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm flex-1 min-w-[200px]"
            />
            <select aria-label="Filter by status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="">All Statuses</option>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="complete">Complete</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : loadError ? (
          <ErrorState
            message={loadError}
            onRetry={() => {
              setLoading(true);
              loadProjects();
            }}
          />
        ) : projects.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-12 text-center">
            <h2 className="text-lg font-semibold text-gray-500 mb-2">No projects found</h2>
            <p className="text-gray-500 mb-4">Create a project to organize cross-experiment analysis.</p>
            {canCreate && (
              <button
                onClick={() => setShowCreateModal(true)}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
              >
                New Project
              </button>
            )}
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-x-auto">
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
          </div>
        )}
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
              onClick={handleCreate}
              disabled={!newName.trim()}
              busy={creating}
              busyLabel="Creating..."
            >
              Create Project
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input id="name"
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              placeholder="e.g., GBM vs. Healthy Integration Atlas"
            />
          </div>
          <div>
            <label htmlFor="hypothesis-optional" className="block text-sm font-medium text-gray-700 mb-1">Hypothesis (optional)</label>
            <textarea id="hypothesis-optional"
              value={newHypothesis}
              onChange={(e) => setNewHypothesis(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              rows={3}
              placeholder="What are you investigating?"
            />
          </div>
        </div>
      </Modal>
    </>
  );
}
