"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import { DetailModal } from "@/components/shared/DetailModal";
import type {
  DatasetExperimentSummary,
  DatasetSearchResult,
  Project,
  ProjectListResponse,
} from "@/lib/types";

export function DatasetBrowser() {
  const [datasets, setDatasets] = useState<DatasetExperimentSummary[]>([]);
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
  const canModify = user?.role === "admin" || user?.role === "comp_bio";

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
    } catch {
      // ignore
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
      .catch(() => {});
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
        } catch {
          // skip
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
        <input
          type="text"
          placeholder="Search datasets..."
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
          className="flex-1 min-w-[200px] px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
        <select
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
        <select
          value={organismFilter}
          onChange={(e) => { setOrganismFilter(e.target.value); setPage(1); }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All Organisms</option>
          {organismOptions.map((org) => (
            <option key={org} value={org}>{org}</option>
          ))}
        </select>
        <select
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
        <select
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
        <select
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
          <button
            onClick={openProjectModal}
            className="px-4 py-2 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700"
          >
            Add to Project ({selectedExperiments.size})
          </button>
        )}
      </div>

      {loading ? (
        <p className="text-gray-400 text-sm">Loading...</p>
      ) : datasets.length === 0 ? (
        <p className="text-gray-400 text-sm">No datasets found.</p>
      ) : (
        <>
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {canModify && (
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase w-8"></th>
                  )}
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Experiment
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Organism
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Samples
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Files
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Total Size
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {datasets.map((ds) => (
                  <tr key={ds.experiment_id} className={`hover:bg-gray-50 cursor-pointer ${selectedExperiments.has(ds.experiment_id) ? "bg-bioaf-50" : ""}`} onClick={() => setViewingDataset(ds)}>
                    {canModify && (
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedExperiments.has(ds.experiment_id)}
                          onChange={() => toggleExperiment(ds.experiment_id)}
                          className="rounded"
                        />
                      </td>
                    )}
                    <td className="px-4 py-3 text-sm font-medium text-gray-900">
                      {ds.experiment_name}
                    </td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 text-xs rounded-full bg-gray-100 text-gray-700">
                        {ds.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {ds.organism || "\u2014"}
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

          <div className="flex justify-between items-center text-sm text-gray-500">
            <span>
              Showing {(page - 1) * pageSize + 1}-
              {Math.min(page * pageSize, total)} of {total}
            </span>
            <div className="space-x-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Previous
              </button>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page * pageSize >= total}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Next
              </button>
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
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-bold mb-4">Add to Project</h2>
            <p className="text-sm text-gray-500 mb-4">
              Add samples from {selectedExperiments.size} experiment{selectedExperiments.size !== 1 ? "s" : ""} to a project.
            </p>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Select Project</label>
                <select
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
                  <label className="block text-sm font-medium text-gray-700 mb-1">New Project Name</label>
                  <input
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
              <button
                onClick={() => {
                  setShowProjectModal(false);
                  setSelectedProjectId("");
                  setNewProjectName("");
                }}
                className="px-4 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleAddToProject}
                disabled={
                  addingToProject ||
                  (!selectedProjectId || (selectedProjectId === "new" && !newProjectName.trim()))
                }
                className="px-4 py-2 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
              >
                {addingToProject ? "Adding..." : "Add to Project"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
