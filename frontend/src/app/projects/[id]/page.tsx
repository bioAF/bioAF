"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { SampleQCBadge } from "@/components/experiments/SampleQCBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";
import { ProvenanceDAGComponent } from "@/components/provenance/ProvenanceDAG";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { FileBrowser } from "@/components/files/FileBrowser";
import { ProjectExportModal } from "@/components/projects/ProjectExportModal";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import type { ProjectDetailResponse, ProjectSampleResponse, ProvenanceDAG, QCStatus } from "@/lib/types";

type Tab = "experiments" | "samples" | "runs" | "analysis" | "provenance" | "data";

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const [project, setProject] = useState<ProjectDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("experiments");
  const [provenance, setProvenance] = useState<ProvenanceDAG | null>(null);
  const [provenanceLoading, setProvenanceLoading] = useState(false);

  // Sample viewing/picker state
  const [viewingSample, setViewingSample] = useState<(ProjectSampleResponse & { experiment_name: string }) | null>(null);
  const [showSamplePicker, setShowSamplePicker] = useState(false);
  const [availableSamples, setAvailableSamples] = useState<Array<{
    id: number;
    sample_id_external: string;
    experiment_name: string;
    experiment_id: number;
    organism: string | null;
    tissue_type: string | null;
    qc_status: QCStatus | null;
  }>>([]);
  const [selectedSampleIds, setSelectedSampleIds] = useState<Set<number>>(new Set());
  const [sampleSearch, setSampleSearch] = useState("");
  const [adding, setAdding] = useState(false);

  const [showProjectExport, setShowProjectExport] = useState(false);

  const user = getCurrentUser();
  const canModify = user?.role_name === "admin" || user?.role_name === "comp_bio";

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadProject();
  }, [router, projectId]);

  useEffect(() => {
    if (activeTab === "provenance" && !provenance) {
      loadProvenance();
    }
  }, [activeTab]);

  const loadProject = async () => {
    setLoading(true);
    try {
      const data = await api.get<ProjectDetailResponse>(`/api/projects/${projectId}`);
      setProject(data);
    } catch {
      router.push("/projects");
    } finally {
      setLoading(false);
    }
  };

  const loadProvenance = async () => {
    setProvenanceLoading(true);
    try {
      const data = await api.get<ProvenanceDAG>(`/api/projects/${projectId}/provenance`);
      setProvenance(data);
    } catch {
      // handled
    } finally {
      setProvenanceLoading(false);
    }
  };

  const loadAvailableSamples = async () => {
    try {
      const data = await api.get<{
        experiments: Array<{
          id: number;
          name: string;
          samples: Array<{
            id: number;
            sample_id_external: string;
            organism: string | null;
            tissue_type: string | null;
            qc_status: QCStatus | null;
          }>;
        }>;
      }>("/api/datasets/browser");
      const flat = data.experiments.flatMap((exp) =>
        exp.samples.map((s) => ({
          ...s,
          experiment_name: exp.name,
          experiment_id: exp.id,
        }))
      );
      setAvailableSamples(flat);
    } catch {
      // Fallback: just show the picker empty
      setAvailableSamples([]);
    }
  };

  const handleAddSamples = async () => {
    if (selectedSampleIds.size === 0) return;
    setAdding(true);
    try {
      await api.post(`/api/projects/${projectId}/samples`, {
        sample_ids: Array.from(selectedSampleIds),
      });
      setShowSamplePicker(false);
      setSelectedSampleIds(new Set());
      loadProject();
    } catch {
      // handled
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveSample = async (sampleId: number) => {
    try {
      await api.delete(`/api/projects/${projectId}/samples/${sampleId}`);
      loadProject();
    } catch {
      // handled
    }
  };

  const openSamplePicker = () => {
    loadAvailableSamples();
    setShowSamplePicker(true);
  };

  const toggleSample = (id: number) => {
    const next = new Set(selectedSampleIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedSampleIds(next);
  };

  // Get existing sample IDs for filtering picker
  const existingSampleIds = new Set(
    project?.samples.flatMap((g) => g.samples.map((s) => s.sample_id)) ?? []
  );

  const filteredAvailable = availableSamples.filter(
    (s) =>
      !existingSampleIds.has(s.id) &&
      (sampleSearch === "" ||
        s.sample_id_external.toLowerCase().includes(sampleSearch.toLowerCase()) ||
        s.experiment_name.toLowerCase().includes(sampleSearch.toLowerCase()) ||
        (s.organism || "").toLowerCase().includes(sampleSearch.toLowerCase()))
  );

  const tabs: { key: Tab; label: string }[] = [
    { key: "experiments", label: "Experiments" },
    { key: "samples", label: "Samples" },
    { key: "runs", label: "Pipeline Runs" },
    { key: "analysis", label: "Analysis" },
    { key: "provenance", label: "Provenance" },
    { key: "data", label: "Data" },
  ];

  if (loading) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex items-center justify-center">
          <LoadingSpinner size="lg" />
        </div>
      </div>
    );
  }

  if (!project) return null;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {/* Project Header */}
          <div className="mb-6">
            <div className="flex items-center gap-3 mb-2">
              <h1 className="text-2xl font-bold">{project.name}</h1>
              {project.code && (
                <span className="text-sm font-mono bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{project.code}</span>
              )}
              <StatusBadge status={project.status || "active"} />
              <div className="ml-auto">
                {canModify && (
                  <button
                    onClick={() => setShowProjectExport(true)}
                    className="bg-gray-100 text-gray-800 px-4 py-2 rounded-md text-sm hover:bg-gray-200"
                  >
                    Export Data
                  </button>
                )}
              </div>
            </div>
            {project.hypothesis && (
              <p className="text-gray-600 italic mb-1">{project.hypothesis}</p>
            )}
            {project.description && (
              <p className="text-gray-500 text-sm">{project.description}</p>
            )}
            <div className="flex gap-6 mt-3 text-sm text-gray-500">
              <span>{project.sample_count} samples</span>
              <span>{project.experiment_count} experiments</span>
              <span>{project.pipeline_run_count} runs</span>
              {project.owner_name && <span>Owner: {project.owner_name}</span>}
            </div>
          </div>

          {/* Tabs */}
          <div className="border-b border-gray-200 mb-6">
            <nav className="flex space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`py-2 px-1 border-b-2 text-sm font-medium ${
                    activeTab === tab.key
                      ? "border-bioaf-600 text-bioaf-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {/* Experiments Tab */}
          {activeTab === "experiments" && (
            <div>
              {project.experiments.length === 0 ? (
                <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">
                  No experiments linked to this project.
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow overflow-hidden">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Samples</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {project.experiments.map((exp) => (
                        <tr
                          key={exp.id}
                          onClick={() => router.push(`/experiments/${exp.id}`)}
                          className="hover:bg-gray-50 cursor-pointer"
                        >
                          <td className="px-6 py-4 text-sm font-medium text-gray-900">{exp.name}</td>
                          <td className="px-6 py-4">
                            <StatusBadge status={exp.status} />
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">{exp.sample_count}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            {exp.created_at ? new Date(exp.created_at).toLocaleDateString() : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Samples Tab */}
          {activeTab === "samples" && (
            <div>
              {canModify && (
                <div className="mb-4">
                  <button
                    onClick={openSamplePicker}
                    className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                  >
                    Add Samples
                  </button>
                </div>
              )}
              {project.samples.length === 0 ? (
                <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">
                  No samples added yet. Click &quot;Add Samples&quot; to get started.
                </div>
              ) : (
                project.samples.map((group) => (
                  <div key={group.experiment_id} className="mb-6">
                    <h3 className="text-sm font-semibold text-gray-700 mb-2">
                      {group.experiment_name}
                    </h3>
                    <div className="bg-white rounded-lg shadow overflow-hidden">
                      <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                          <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Sample ID</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">QC</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Added By</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Added</th>
                            {canModify && (
                              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase"></th>
                            )}
                          </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                          {group.samples.map((s) => (
                            <tr key={s.sample_id} className="hover:bg-gray-50 cursor-pointer" onClick={() => setViewingSample({ ...s, experiment_name: group.experiment_name })}>
                              <td className="px-6 py-4 text-sm font-medium text-gray-900">
                                {s.sample_id_external || `#${s.sample_id}`}
                              </td>
                              <td className="px-6 py-4 text-sm text-gray-500">{s.organism || "—"}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">{s.tissue_type || "—"}</td>
                              <td className="px-6 py-4">
                                {s.qc_status ? <SampleQCBadge status={s.qc_status} /> : "—"}
                              </td>
                              <td className="px-6 py-4 text-sm text-gray-500">{s.added_by || "—"}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">
                                {s.added_at ? new Date(s.added_at).toLocaleDateString() : "—"}
                              </td>
                              {canModify && (
                                <td className="px-6 py-4 text-right" onClick={(e) => e.stopPropagation()}>
                                  <button
                                    onClick={() => handleRemoveSample(s.sample_id)}
                                    className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50"
                                  >
                                    Remove
                                  </button>
                                </td>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {viewingSample && (
            <DetailModal
              title={viewingSample.sample_id_external || `Sample #${viewingSample.sample_id}`}
              onClose={() => setViewingSample(null)}
              fields={[
                { label: "Sample ID", value: viewingSample.sample_id_external || `#${viewingSample.sample_id}` },
                { label: "Experiment", value: viewingSample.experiment_name },
                { label: "Organism", value: viewingSample.organism },
                { label: "Tissue Type", value: viewingSample.tissue_type },
                { label: "QC Status", value: viewingSample.qc_status },
                { label: "Added By", value: viewingSample.added_by },
                { label: "Added", value: viewingSample.added_at ? new Date(viewingSample.added_at).toLocaleDateString() : null },
                { label: "Notes", value: viewingSample.notes },
              ]}
              actions={
                canModify ? (
                  <button
                    onClick={() => { handleRemoveSample(viewingSample.sample_id); setViewingSample(null); }}
                    className="px-3 py-1.5 border border-red-600 text-red-600 rounded text-sm hover:bg-red-50"
                  >
                    Remove from Project
                  </button>
                ) : undefined
              }
            />
          )}

          {/* Pipeline Runs Tab */}
          {activeTab === "runs" && (
            <div>
              {project.pipeline_runs.length === 0 ? (
                <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">
                  No pipeline runs yet.
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow overflow-hidden">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pipeline</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Version</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {project.pipeline_runs.map((run) => (
                        <tr
                          key={run.id}
                          onClick={() => router.push(`/pipelines/runs/${run.id}`)}
                          className="hover:bg-gray-50 cursor-pointer"
                        >
                          <td className="px-6 py-4 text-sm font-medium text-gray-900">{run.pipeline_name}</td>
                          <td className="px-6 py-4 text-sm text-gray-500">{run.pipeline_version || "—"}</td>
                          <td className="px-6 py-4">
                            <StatusBadge status={run.status} />
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500">
                            {run.created_at ? new Date(run.created_at).toLocaleDateString() : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Analysis Tab */}
          {activeTab === "analysis" && (
            <SnapshotTimeline projectId={Number(projectId)} />
          )}

          {/* Provenance Tab */}
          {activeTab === "provenance" && (
            <div>
              {provenanceLoading ? (
                <div className="flex justify-center py-12">
                  <LoadingSpinner size="lg" />
                </div>
              ) : provenance ? (
                <div className="bg-white rounded-lg shadow p-6">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-semibold text-gray-700">
                      Provenance DAG — {provenance.nodes.length} nodes, {provenance.edges.length} edges
                    </h3>
                    <ProvenanceExportMenu entityType="projects" entityId={Number(projectId)} />
                  </div>
                  <div className="min-h-[400px]">
                    <ProvenanceDAGComponent data={provenance} />
                  </div>
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">
                  No provenance data available.
                </div>
              )}
              <div className="mt-6">
                <ProvenanceReportPanel
                  entityType="project"
                  entityId={Number(projectId)}
                  entityName={project.name}
                />
              </div>
            </div>
          )}

          {/* Data Tab */}
          {activeTab === "data" && (
            <div>
              <h2 className="text-lg font-semibold mb-4">Files</h2>
              <FileBrowser projectId={project.id} />
            </div>
          )}
        </main>
      </div>

      {/* Sample Picker Modal */}
      {showSamplePicker && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[80vh] flex flex-col">
            <div className="p-4 border-b">
              <h2 className="text-lg font-bold">Add Samples to Project</h2>
              <input
                type="text"
                placeholder="Search by sample ID, experiment, or organism..."
                value={sampleSearch}
                onChange={(e) => setSampleSearch(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm mt-3"
              />
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {filteredAvailable.length === 0 ? (
                <p className="text-gray-400 text-center py-8">No matching samples available.</p>
              ) : (
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase w-8"></th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Sample ID</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Experiment</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">QC</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {filteredAvailable.map((s) => (
                      <tr
                        key={s.id}
                        onClick={() => toggleSample(s.id)}
                        className={`cursor-pointer ${
                          selectedSampleIds.has(s.id) ? "bg-bioaf-50" : "hover:bg-gray-50"
                        }`}
                      >
                        <td className="px-4 py-2">
                          <input
                            type="checkbox"
                            checked={selectedSampleIds.has(s.id)}
                            onChange={() => toggleSample(s.id)}
                            className="rounded"
                          />
                        </td>
                        <td className="px-4 py-2 text-sm">{s.sample_id_external}</td>
                        <td className="px-4 py-2 text-sm text-gray-500">{s.experiment_name}</td>
                        <td className="px-4 py-2 text-sm text-gray-500">{s.organism || "—"}</td>
                        <td className="px-4 py-2 text-sm text-gray-500">{s.tissue_type || "—"}</td>
                        <td className="px-4 py-2">
                          {s.qc_status ? <SampleQCBadge status={s.qc_status} /> : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="p-4 border-t flex justify-between items-center">
              <span className="text-sm text-gray-500">
                {selectedSampleIds.size} sample{selectedSampleIds.size !== 1 ? "s" : ""} selected
              </span>
              <div className="flex gap-3">
                <button
                  onClick={() => {
                    setShowSamplePicker(false);
                    setSelectedSampleIds(new Set());
                    setSampleSearch("");
                  }}
                  className="px-4 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAddSamples}
                  disabled={selectedSampleIds.size === 0 || adding}
                  className="px-4 py-2 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
                >
                  {adding ? "Adding..." : "Add to Project"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    <ProjectExportModal
      projectId={Number(projectId)}
      projectName={project?.name ?? ""}
      isOpen={showProjectExport}
      onClose={() => setShowProjectExport(false)}
    />
    </div>
  );
}
