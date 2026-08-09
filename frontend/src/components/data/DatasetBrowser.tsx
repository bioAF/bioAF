"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { getCurrentUser } from "@/lib/auth";
import { Button } from "@/components/ui/Button";
import { DetailModal } from "@/components/shared/DetailModal";
import type {
  DatasetExperimentSummary,
  DatasetSearchResult,
  Project,
  ProjectListResponse,
} from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import { ErrorState } from "@/components/shared/ErrorState";

import { clickableRow } from "@/lib/a11y";

export function DatasetBrowser() {
  const toast = useToast();
  const [datasets, setDatasets] = useState<DatasetExperimentSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [viewingDataset, setViewingDataset] = useState<DatasetExperimentSummary | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [organismFilter, setOrganismFilter] = useState("");
  const [moleculeTypeFilter, setMoleculeTypeFilter] = useState("");
  const [instrumentModelFilter, setInstrumentModelFilter] = useState("");
  const [reviewStatusFilter, setReviewStatusFilter] = useState("");
  const [organismOptions, setOrganismOptions] = useState<string[]>([]);
  const pageSize = 20;

  // Multi-select and "Add to Project" state
  const [selectedExperiments, setSelectedExperiments] = useState<Set<number>>(new Set());
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [newProjectName, setNewProjectName] = useState("");
  const [addingToProject, setAddingToProject] = useState(false);

  const user = getCurrentUser();
  const canModify = user?.role_name === "admin" || user?.role_name === "comp_bio";

  const fetchDatasets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (query) params.set("query", query);
      if (statusFilter) params.set("status", statusFilter);
      if (organismFilter) params.set("organism", organismFilter);
      if (moleculeTypeFilter) params.set("molecule_type", moleculeTypeFilter);
      if (instrumentModelFilter) params.set("instrument_model", instrumentModelFilter);
      if (reviewStatusFilter) params.set("review_status", reviewStatusFilter);
      const data = await api.get<DatasetSearchResult>(
        `/api/datasets?${params}`
      );
      setDatasets(data.experiments);
      setTotal(data.total);
    } catch (e) {
      logError("loading datasets", e);
      setLoadError(loadFailureMessage("Datasets"));
    } finally {
      setLoading(false);
    }
  }, [page, query, statusFilter, organismFilter, moleculeTypeFilter, instrumentModelFilter, reviewStatusFilter]);

  useEffect(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  useEffect(() => {
    api.get<{ organisms: string[] }>("/api/datasets/filter-options")
      .then((data) => setOrganismOptions(data.organisms))
      .catch((e) => {
        logError("loading the organism filter", e);
        toast.error(loadFailureMessage("The organism filter"));
      });
  }, []);

  const toggleExperiment = (id: number) => {
    const next = new Set(selectedExperiments);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedExperiments(next);
  };

  const openProjectModal = async () => {
    try {
      const data = await api.get<ProjectListResponse>("/api/projects");
      setProjects(data.projects);
    } catch {
      // ignore
    }
    setShowProjectModal(true);
  };

  const handleAddToProject = async () => {
    setAddingToProject(true);
    try {
      let projectId: number;

      if (selectedProjectId === "new") {
        const resp = await api.post<{ id: number }>("/api/projects", {
          name: newProjectName,
        });
        projectId = resp.id;
      } else {
        projectId = parseInt(selectedProjectId);
      }

      const selectedDs = datasets.filter((ds) => selectedExperiments.has(ds.experiment_id));
      const sampleIds: number[] = [];
      for (const ds of selectedDs) {
        try {
          const expData = await api.get<{ samples: Array<{ id: number }> }>(
            `/api/experiments/${ds.experiment_id}`
          );
          if (expData.samples) {
            sampleIds.push(...expData.samples.map((s) => s.id));
          }
        } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add the datasets to the project.");
    }
      }

      if (sampleIds.length > 0) {
        await api.post(`/api/projects/${projectId}/samples`, {
          sample_ids: sampleIds,
        });
      }

      setShowProjectModal(false);
      setSelectedExperiments(new Set());
      setSelectedProjectId("");
      setNewProjectName("");
    } catch {
      // handled by api client
    } finally {
      setAddingToProject(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-4 flex-wrap">
        <input aria-label="Search datasets"
          type="text"
          placeholder="Search datasets..."
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
          className="flex-1 min-w-[200px] px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
        <select aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Statuses</option>
          <option value="registered">Registered</option>
          <option value="processing">Processing</option>
          <option value="pipeline_complete">Pipeline Complete</option>
          <option value="reviewed">Reviewed</option>
          <option value="analysis">Analysis</option>
          <option value="complete">Complete</option>
        </select>
        <select aria-label="Filter by organism"
          value={organismFilter}
          onChange={(e) => { setOrganismFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Organisms</option>
          {organismOptions.map((org) => (
            <option key={org} value={org}>{org}</option>
          ))}
        </select>
        <select aria-label="Filter by review status"
          value={reviewStatusFilter}
          onChange={(e) => { setReviewStatusFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Review Statuses</option>
          <option value="approved">Approved</option>
          <option value="approved_with_caveats">Approved with Caveats</option>
          <option value="rejected">Rejected</option>
          <option value="revision_requested">Revision Requested</option>
        </select>
        <select aria-label="Filter by instrument model"
          value={instrumentModelFilter}
          onChange={(e) => { setInstrumentModelFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Instruments</option>
          <option value="NovaSeq 6000">NovaSeq 6000</option>
          <option value="NovaSeq X">NovaSeq X</option>
          <option value="NextSeq 2000">NextSeq 2000</option>
          <option value="MiSeq">MiSeq</option>
        </select>
        <select aria-label="Filter by molecule type"
          value={moleculeTypeFilter}
          onChange={(e) => { setMoleculeTypeFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Molecule Types</option>
          <option value="total RNA">Total RNA</option>
          <option value="mRNA">mRNA</option>
          <option value="genomic DNA">Genomic DNA</option>
          <option value="protein">Protein</option>
        </select>
        {canModify && selectedExperiments.size > 0 && (
          <Button onClick={openProjectModal}>
            Add to Project ({selectedExperiments.size})
          </Button>
        )}
      </div>

      {loading ? (
        <p className="text-ink-subtle text-sm">Loading...</p>
      ) : loadError ? (
        <ErrorState message={loadError} onRetry={() => fetchDatasets()} />
      ) : datasets.length === 0 ? (
        <p className="text-ink-subtle text-sm">No datasets found.</p>
      ) : (
        <>
          <div className="bg-surface rounded-lg shadow overflow-x-auto">
            <table className="min-w-full divide-y divide-hairline">
              <thead className="bg-gray-50">
                <tr>
                  {canModify && (
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase w-8"></th>
                  )}
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Experiment
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Organism
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Samples
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Files
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">
                    Total Size
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {datasets.map((ds) => (
                  <tr key={ds.experiment_id} className={`hover:bg-surface-muted cursor-pointer ${selectedExperiments.has(ds.experiment_id) ? "bg-bioaf-50" : ""}`} {...clickableRow(() => setViewingDataset(ds))}>
                    {canModify && (
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${ds.experiment_name}`}
                          checked={selectedExperiments.has(ds.experiment_id)}
                          onChange={() => toggleExperiment(ds.experiment_id)}
                          className="rounded"
                        />
                      </td>
                    )}
                    <td className="px-4 py-3 text-sm font-medium text-ink">
                      {ds.experiment_name}
                    </td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 text-xs rounded-full bg-elevated text-ink-muted">
                        {ds.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {ds.organism || NOT_SET}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {ds.sample_count}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {ds.file_count}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {(ds.total_size_bytes / (1024 ** 3)).toFixed(2)} GB
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex justify-between items-center text-sm text-ink-subtle">
            <span>
              Showing {(page - 1) * pageSize + 1}-
              {Math.min(page * pageSize, total)} of {total}
            </span>
            <div className="space-x-2">
              <Button variant="secondary" size="sm" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}>
                Previous
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setPage((p) => p + 1)} disabled={page * pageSize >= total}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}

      {viewingDataset && (
        <DetailModal
          title={viewingDataset.experiment_name}
          onClose={() => setViewingDataset(null)}
          fields={[
            { label: "Status", value: viewingDataset.status },
            { label: "Organism", value: viewingDataset.organism },
            { label: "Tissue", value: viewingDataset.tissue },
            { label: "Molecule Type", value: viewingDataset.molecule_type },
            { label: "Instrument", value: viewingDataset.instrument_model },
            { label: "Review Status", value: viewingDataset.review_status },
            { label: "Samples", value: viewingDataset.sample_count },
            { label: "Files", value: viewingDataset.file_count },
            { label: "Total Size", value: `${(viewingDataset.total_size_bytes / (1024 ** 3)).toFixed(2)} GB` },
            { label: "Pipeline Runs", value: viewingDataset.pipeline_run_count },
            { label: "QC Dashboard", value: viewingDataset.has_qc_dashboard ? "Yes" : "No" },
            { label: "CELLxGENE", value: viewingDataset.has_cellxgene ? "Yes" : "No" },
            { label: "Owner", value: viewingDataset.owner?.name || viewingDataset.owner?.email },
            { label: "Created", value: new Date(viewingDataset.created_at).toLocaleDateString() },
          ]}
        />
      )}

      {/* Add to Project Modal */}
      {showProjectModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-surface rounded-lg shadow-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-bold mb-4">Add to Project</h2>
            <p className="text-sm text-ink-subtle mb-4">
              Add samples from {selectedExperiments.size} experiment{selectedExperiments.size !== 1 ? "s" : ""} to a project.
            </p>
            <div className="space-y-4">
              <div>
                <label htmlFor="select-project" className="block text-sm font-medium text-ink-muted mb-1">Select Project</label>
                <select id="select-project"
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  <option value="">Choose a project...</option>
                  {projects.map((p) => (
                    <option key={p.id} value={String(p.id)}>{p.name}</option>
                  ))}
                  <option value="new">+ Create New Project</option>
                </select>
              </div>
              {selectedProjectId === "new" && (
                <div>
                  <label htmlFor="new-project-name" className="block text-sm font-medium text-ink-muted mb-1">New Project Name</label>
                  <input id="new-project-name"
                    type="text"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                    placeholder="Project name"
                  />
                </div>
              )}
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <Button
                variant="secondary"
                onClick={() => {
                  setShowProjectModal(false);
                  setSelectedProjectId("");
                  setNewProjectName("");
                }}
              >
                Cancel
              </Button>
              <Button
                onClick={handleAddToProject}
                busy={addingToProject}
                busyLabel="Adding..."
                disabled={
                  !selectedProjectId || (selectedProjectId === "new" && !newProjectName.trim())
                }
              >
                Add to Project
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
