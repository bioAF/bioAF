"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { SampleQCBadge } from "@/components/experiments/SampleQCBadge";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";
import { DataExportModal } from "@/components/experiments/DataExportModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlotModal } from "@/components/shared/PlotModal";
import { DetailModal } from "@/components/shared/DetailModal";
import { ExportPdfButton } from "@/components/shared/ExportPdfButton";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { FileBrowser } from "@/components/files/FileBrowser";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { ExtensibleVocabularySelect } from "@/components/shared/ExtensibleVocabularySelect";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api, fileContentUrl } from "@/lib/api";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import type {
  ExperimentDetail,
  ExperimentUpdateRequest,
  FieldDefaultValue,
  Sample,
  Batch,
  AuditLogResponse,
  AuditLogEntry,
  SampleCreateRequest,
  SampleUpdateRequest,
  SampleBulkUpdateRequest,
  BatchCreateRequest,
  ExperimentStatus,
  QCStatus,
  NotebookSession,
  SessionListResponse,
  PipelineRun,
  PipelineRunListResponse,
  PipelineRunStatus,
  QCDashboardSummary,
  QCDashboardResponse,
  CellxgenePublicationResponse,
  PlotArchiveResponse,
  PlotArchiveListResponse,
} from "@/lib/types";

type Tab = "overview" | "samples" | "batches" | "files" | "analysis" | "pipelines" | "results" | "provenance" | "audit";

export default function ExperimentDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const [experiment, setExperiment] = useState<ExperimentDetail | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [loading, setLoading] = useState(true);

  const [notebookSessions, setNotebookSessions] = useState<NotebookSession[]>([]);
  const [pipelineRuns, setPipelineRuns] = useState<PipelineRun[]>([]);
  const [showGeoExport, setShowGeoExport] = useState(false);
  const [showDataExport, setShowDataExport] = useState(false);
  const [editingOverview, setEditingOverview] = useState(false);
  const [overviewForm, setOverviewForm] = useState<ExperimentUpdateRequest>({});
  const [overviewError, setOverviewError] = useState("");
  const [showSampleForm, setShowSampleForm] = useState(false);
  const [showBatchForm, setShowBatchForm] = useState(false);
  const [sampleForm, setSampleForm] = useState<SampleCreateRequest>({});
  const [sampleFormError, setSampleFormError] = useState("");
  const [batchForm, setBatchForm] = useState<BatchCreateRequest>({ name: "" });
  const [editFieldDefaults, setEditFieldDefaults] = useState<FieldDefaultValue[]>([]);

  // Sample viewing/editing state
  const [viewingSample, setViewingSample] = useState<Sample | null>(null);
  const [selectedSampleIds, setSelectedSampleIds] = useState<Set<number>>(new Set());
  const [editingSampleId, setEditingSampleId] = useState<number | null>(null);
  const [editSampleForm, setEditSampleForm] = useState<SampleUpdateRequest>({});
  const [editSampleError, setEditSampleError] = useState("");
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkEditForm, setBulkEditForm] = useState<SampleUpdateRequest>({});
  const [bulkEditError, setBulkEditError] = useState("");

  const DEFAULTABLE_FIELDS = [
    { name: "organism", label: "Organism", type: "text" as const },
    { name: "tissue_type", label: "Tissue Type", type: "text" as const },
    { name: "donor_source", label: "Donor ID", type: "text" as const },
    { name: "treatment_condition", label: "Treatment Condition", type: "text" as const },
    { name: "chemistry_version", label: "Chemistry Version", type: "text" as const },
    { name: "molecule_type", label: "Molecule Type", type: "vocabulary" as const },
    { name: "library_prep_method", label: "Library Prep Method", type: "vocabulary" as const },
    { name: "library_layout", label: "Library Layout", type: "vocabulary" as const },
  ];

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadExperiment();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, router]);

  useEffect(() => {
    if (activeTab === "samples") loadSamples();
    if (activeTab === "batches") loadBatches();
    if (activeTab === "analysis") loadNotebookSessions();
    if (activeTab === "pipelines") loadPipelineRuns();
    if (activeTab === "audit") loadAudit();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, id]);

  async function loadExperiment() {
    try {
      const data = await api.get<ExperimentDetail>(`/api/experiments/${id}`);
      setExperiment(data);
    } catch {
      // handled
    } finally {
      setLoading(false);
    }
  }

  async function loadSamples() {
    try {
      const data = await api.get<Sample[]>(`/api/experiments/${id}/samples`);
      setSamples(data);
    } catch {}
  }

  async function loadBatches() {
    try {
      const data = await api.get<Batch[]>(`/api/experiments/${id}/batches`);
      setBatches(data);
    } catch {}
  }

  async function loadAudit(page = 1) {
    try {
      const data = await api.get<AuditLogResponse>(`/api/experiments/${id}/audit?page=${page}`);
      setAuditEntries(data.entries);
      setAuditTotal(data.total);
    } catch {}
  }

  async function loadPipelineRuns() {
    try {
      const data = await api.get<PipelineRunListResponse>(`/api/pipeline-runs?experiment_id=${id}`);
      setPipelineRuns(data.runs);
    } catch {}
  }

  async function loadNotebookSessions() {
    try {
      const data = await api.get<SessionListResponse>("/api/notebooks/sessions");
      setNotebookSessions(data.sessions.filter(s => s.experiment?.id === Number(id)));
    } catch {}
  }

  async function handleLaunchNotebook(sessionType: "jupyter" | "rstudio") {
    try {
      await api.post("/api/notebooks/sessions", {
        session_type: sessionType,
        resource_profile: "small",
        experiment_id: Number(id),
      });
      loadNotebookSessions();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to launch session");
    }
  }

  async function handleAddSample() {
    setSampleFormError("");
    try {
      await api.post(`/api/experiments/${id}/samples`, sampleForm);
      setSampleForm({});
      setShowSampleForm(false);
      loadSamples();
      loadExperiment();
    } catch (err) {
      setSampleFormError(err instanceof Error ? err.message : "Failed to save sample");
    }
  }

  async function handleAddBatch() {
    try {
      await api.post(`/api/experiments/${id}/batches`, batchForm);
      setBatchForm({ name: "" });
      setShowBatchForm(false);
      loadBatches();
      loadExperiment();
    } catch {}
  }

  function startEditOverview() {
    if (!experiment) return;
    setOverviewForm({
      name: experiment.name,
      hypothesis: experiment.hypothesis,
      description: experiment.description,
      start_date: experiment.start_date,
      expected_sample_count: experiment.expected_sample_count,
      design_type: experiment.design_type,
    });
    setEditFieldDefaults(
      experiment.field_defaults.map((fd) => ({
        field_name: fd.field_name,
        default_value: fd.default_value,
        is_required: fd.is_required,
      }))
    );
    setOverviewError("");
    setEditingOverview(true);
  }

  function updateEditFieldDefault(fieldName: string, value: string | null, isRequired: boolean | null) {
    setEditFieldDefaults((prev) => {
      const existing = prev.find((d) => d.field_name === fieldName);
      if (existing) {
        if (!value && isRequired === null) {
          return prev.filter((d) => d.field_name !== fieldName);
        }
        return prev.map((d) => d.field_name === fieldName ? { ...d, default_value: value, is_required: isRequired } : d);
      }
      if (value || isRequired !== null) {
        return [...prev, { field_name: fieldName, default_value: value, is_required: isRequired }];
      }
      return prev;
    });
  }

  async function handleSaveOverview() {
    setOverviewError("");
    try {
      const payload = { ...overviewForm, field_defaults: editFieldDefaults };
      await api.patch(`/api/experiments/${id}`, payload);
      setEditingOverview(false);
      loadExperiment();
    } catch (err) {
      setOverviewError(err instanceof Error ? err.message : "Failed to save experiment");
    }
  }

  async function handleUpdateQC(sampleId: number, qcStatus: string) {
    try {
      await api.patch(`/api/samples/${sampleId}/qc`, { qc_status: qcStatus });
      loadSamples();
    } catch {}
  }

  async function handleCsvUpload(file: File) {
    try {
      await api.upload(`/api/experiments/${id}/samples/upload`, file);
      loadSamples();
      loadExperiment();
    } catch {}
  }

  function startEditSample(sample: Sample) {
    setEditingSampleId(sample.id);
    setEditSampleForm({
      sample_id_external: sample.sample_id_external,
      organism: sample.organism,
      tissue_type: sample.tissue_type,
      donor_source: sample.donor_source,
      treatment_condition: sample.treatment_condition,
      chemistry_version: sample.chemistry_version,
      viability_pct: sample.viability_pct,
      cell_count: sample.cell_count,
      molecule_type: sample.molecule_type,
      library_prep_method: sample.library_prep_method,
      library_layout: sample.library_layout,
    });
    setEditSampleError("");
  }

  async function handleSaveSampleEdit() {
    if (!editingSampleId) return;
    setEditSampleError("");
    try {
      await api.patch(`/api/samples/${editingSampleId}`, editSampleForm);
      setEditingSampleId(null);
      setEditSampleForm({});
      loadSamples();
    } catch (err) {
      setEditSampleError(err instanceof Error ? err.message : "Failed to save");
    }
  }

  async function handleBulkEdit() {
    if (selectedSampleIds.size === 0) return;
    setBulkEditError("");
    // Only send fields that have a value
    const update: SampleUpdateRequest = {};
    for (const [key, val] of Object.entries(bulkEditForm)) {
      if (val !== undefined && val !== null && val !== "") {
        (update as Record<string, unknown>)[key] = val;
      }
    }
    if (Object.keys(update).length === 0) {
      setBulkEditError("Set at least one field to update");
      return;
    }
    try {
      const payload: SampleBulkUpdateRequest = {
        sample_ids: Array.from(selectedSampleIds),
        update,
      };
      await api.patch("/api/samples/bulk/update", payload);
      setShowBulkEdit(false);
      setBulkEditForm({});
      setSelectedSampleIds(new Set());
      loadSamples();
    } catch (err) {
      setBulkEditError(err instanceof Error ? err.message : "Failed to save");
    }
  }

  function toggleSampleSelection(sampleId: number) {
    setSelectedSampleIds((prev) => {
      const next = new Set(prev);
      if (next.has(sampleId)) next.delete(sampleId);
      else next.add(sampleId);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedSampleIds.size === samples.length) {
      setSelectedSampleIds(new Set());
    } else {
      setSelectedSampleIds(new Set(samples.map((s) => s.id)));
    }
  }

  async function handleStatusUpdate(newStatus: string) {
    try {
      await api.patch(`/api/experiments/${id}/status`, { status: newStatus });
      loadExperiment();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!experiment) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center">
            <p className="text-gray-500">Experiment not found</p>
          </main>
        </div>
      </div>
    );
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "samples", label: `Samples (${experiment.sample_count})` },
    { key: "batches", label: `Batches (${experiment.batch_count})` },
    { key: "files", label: "Files" },
    { key: "analysis", label: "Analysis" },
    { key: "pipelines", label: "Pipeline Runs" },
    { key: "results", label: "Results" },
    { key: "provenance", label: "Provenance" },
    { key: "audit", label: "Audit Trail" },
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center gap-4 mb-6">
            <button onClick={() => router.push("/experiments")} className="text-gray-500 hover:text-gray-700">
              ← Back
            </button>
            <h1 className="text-2xl font-bold">{experiment.name}</h1>
            <ExperimentStatusBadge status={experiment.status} />
            <div className="ml-auto flex items-center gap-2">
              <ProvenanceExportMenu entityType="experiments" entityId={Number(id)} />
              {(() => {
                const user = getCurrentUser();
                const role = (user?.role_name as string) || "viewer";
                return ["admin", "comp_bio"].includes(role) ? (
                  <>
                    <button
                      onClick={() => setShowDataExport(true)}
                      className="bg-gray-100 text-gray-800 px-4 py-2 rounded-md text-sm hover:bg-gray-200"
                    >
                      Export Data
                    </button>
                    <button
                      onClick={() => setShowGeoExport(true)}
                      className="bg-indigo-600 text-white px-4 py-2 rounded-md text-sm hover:bg-indigo-700"
                    >
                      Export to GEO
                    </button>
                  </>
                ) : null;
              })()}
            </div>
          </div>

          <div className="border-b border-gray-200 mb-6">
            <nav className="flex -mb-px space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`py-2 px-1 border-b-2 text-sm font-medium ${
                    activeTab === tab.key
                      ? "border-bioaf-500 text-bioaf-600"
                      : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {activeTab === "overview" && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="bg-white rounded-lg shadow p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold">Experiment Details</h2>
                  {!editingOverview && (
                    <button onClick={startEditOverview} className="text-sm text-bioaf-600 hover:underline">Edit</button>
                  )}
                </div>

                {editingOverview ? (
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm text-gray-500 mb-1">Name</label>
                      <input value={overviewForm.name ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, name: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm" />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-500 mb-1">Design Type</label>
                      <ExtensibleVocabularySelect
                        fieldName="design_type"
                        value={overviewForm.design_type ?? null}
                        onChange={(v) => setOverviewForm({ ...overviewForm, design_type: v })}
                        placeholder="Select design type..."
                        className="w-full border rounded px-3 py-1.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-500 mb-1">Hypothesis</label>
                      <textarea value={overviewForm.hypothesis ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, hypothesis: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
                    </div>
                    <div>
                      <label className="block text-sm text-gray-500 mb-1">Description</label>
                      <textarea value={overviewForm.description ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, description: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-sm text-gray-500 mb-1">Start Date</label>
                        <input type="date" value={overviewForm.start_date ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, start_date: e.target.value || null })} className="w-full border rounded px-3 py-1.5 text-sm" />
                      </div>
                      <div>
                        <label className="block text-sm text-gray-500 mb-1">Expected Samples</label>
                        <input type="number" min={0} value={overviewForm.expected_sample_count ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, expected_sample_count: e.target.value ? Number(e.target.value) : null })} className="w-full border rounded px-3 py-1.5 text-sm" />
                      </div>
                    </div>
                    <div className="border-t pt-3 mt-3">
                      <h3 className="text-sm font-medium text-gray-700 mb-2">Sample Field Defaults</h3>
                      <p className="text-xs text-gray-400 mb-2">Default values applied to new samples. Per-sample values override these.</p>
                      <div className="space-y-2">
                        {DEFAULTABLE_FIELDS.map((field) => {
                          const current = editFieldDefaults.find((d) => d.field_name === field.name);
                          return (
                            <div key={field.name} className="grid grid-cols-3 gap-2 items-center">
                              <span className="text-xs text-gray-600">{field.label}</span>
                              {field.type === "vocabulary" ? (
                                <VocabularySelect
                                  fieldName={field.name}
                                  value={current?.default_value ?? null}
                                  onChange={(v) => updateEditFieldDefault(field.name, v, current?.is_required ?? null)}
                                  placeholder={`Default...`}
                                />
                              ) : (
                                <input
                                  value={current?.default_value ?? ""}
                                  onChange={(e) => updateEditFieldDefault(field.name, e.target.value || null, current?.is_required ?? null)}
                                  placeholder="Default..."
                                  className="border rounded px-2 py-1 text-sm"
                                />
                              )}
                              <label className="flex items-center gap-1 text-xs text-gray-500">
                                <input
                                  type="checkbox"
                                  checked={current?.is_required ?? false}
                                  onChange={(e) => updateEditFieldDefault(field.name, current?.default_value ?? null, e.target.checked || null)}
                                  className="rounded border-gray-300"
                                />
                                Required
                              </label>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                    {overviewError && <p className="text-red-600 text-sm">{overviewError}</p>}
                    <div className="flex gap-2 pt-1">
                      <button onClick={handleSaveOverview} className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm">Save</button>
                      <button onClick={() => { setEditingOverview(false); setOverviewError(""); }} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <dl className="space-y-3">
                    <div><dt className="text-sm text-gray-500">Project</dt><dd className="text-sm">{experiment.project?.name || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Template</dt><dd className="text-sm">{experiment.template_name || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Design Type</dt><dd className="text-sm">{experiment.design_type || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Owner</dt><dd className="text-sm">{experiment.owner?.name || experiment.owner?.email || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Hypothesis</dt><dd className="text-sm">{experiment.hypothesis || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Description</dt><dd className="text-sm">{experiment.description || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Start Date</dt><dd className="text-sm">{experiment.start_date || "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Expected Samples</dt><dd className="text-sm">{experiment.expected_sample_count ?? "—"}</dd></div>
                    <div><dt className="text-sm text-gray-500">Actual Samples</dt><dd className="text-sm">{experiment.sample_count}</dd></div>
                    <div><dt className="text-sm text-gray-500">Created</dt><dd className="text-sm">{new Date(experiment.created_at).toLocaleString()}</dd></div>
                  </dl>
                )}
              </div>

              <div className="bg-white rounded-lg shadow p-6">
                <h2 className="text-lg font-semibold mb-4">Status</h2>
                <div className="space-y-3">
                  <div className="flex items-center gap-4">
                    <ExperimentStatusBadge status={experiment.status} />
                    <select
                      onChange={(e) => { if (e.target.value) handleStatusUpdate(e.target.value); e.target.value = ""; }}
                      className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
                      defaultValue=""
                    >
                      <option value="" disabled>Advance status...</option>
                      {(["registered", "library_prep", "sequencing", "fastq_uploaded", "processing", "pipeline_complete", "reviewed", "analysis", "complete"] as ExperimentStatus[])
                        .filter((s) => s !== experiment.status)
                        .map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
                    </select>
                  </div>
                </div>

                {experiment.custom_fields.length > 0 && (
                  <>
                    <h3 className="text-md font-semibold mt-6 mb-3">Custom Fields</h3>
                    {experiment.template_name && (
                      <p className="text-xs text-gray-400 mb-3">Controlled by template: {experiment.template_name}</p>
                    )}
                    <dl className="space-y-2">
                      {experiment.custom_fields.map((cf) => (
                        <div key={cf.id}>
                          <dt className="text-sm text-gray-400">{cf.field_name}</dt>
                          <dd className="text-sm text-gray-500">{cf.field_value || "—"}</dd>
                        </div>
                      ))}
                    </dl>
                  </>
                )}

                {experiment.field_defaults.length > 0 && (
                  <>
                    <h3 className="text-md font-semibold mt-6 mb-3">Sample Field Defaults</h3>
                    <p className="text-xs text-gray-400 mb-3">Applied to new samples unless overridden per-sample.</p>
                    <dl className="space-y-2">
                      {experiment.field_defaults.map((fd) => {
                        const label = DEFAULTABLE_FIELDS.find((f) => f.name === fd.field_name)?.label ?? fd.field_name;
                        return (
                          <div key={fd.id} className="flex items-center gap-2">
                            <dt className="text-sm text-gray-400">{label}</dt>
                            <dd className="text-sm text-gray-600">{fd.default_value || "—"}</dd>
                            {fd.is_required && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">required</span>}
                          </div>
                        );
                      })}
                    </dl>
                  </>
                )}
              </div>
            </div>
          )}

          {activeTab === "samples" && (
            <div>
              <div className="flex items-center gap-4 mb-4">
                <button
                  onClick={() => {
                    if (!showSampleForm && experiment) {
                      const prefill: Record<string, string> = {};
                      for (const fd of experiment.field_defaults) {
                        if (fd.default_value) prefill[fd.field_name] = fd.default_value;
                      }
                      setSampleForm(prefill as unknown as SampleCreateRequest);
                    }
                    setShowSampleForm(!showSampleForm);
                  }}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                >
                  Add Sample
                </button>
                <label className="bg-white border border-gray-300 px-4 py-2 rounded-md text-sm cursor-pointer hover:bg-gray-50">
                  Upload CSV
                  <input
                    type="file"
                    accept=".csv,.tsv,.txt"
                    className="hidden"
                    onChange={(e) => { if (e.target.files?.[0]) handleCsvUpload(e.target.files[0]); }}
                  />
                </label>
                {selectedSampleIds.size > 0 && (
                  <button
                    onClick={() => { setShowBulkEdit(true); setBulkEditForm({}); setBulkEditError(""); }}
                    className="bg-amber-600 text-white px-4 py-2 rounded-md text-sm hover:bg-amber-700"
                  >
                    Edit Selected ({selectedSampleIds.size})
                  </button>
                )}
              </div>

              {showSampleForm && (
                <div className="bg-white rounded-lg shadow p-4 mb-4">
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    <input placeholder="External Sample ID" value={sampleForm.sample_id_external ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, sample_id_external: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Organism" value={sampleForm.organism ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, organism: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Tissue Type" value={sampleForm.tissue_type ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, tissue_type: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Donor ID" value={sampleForm.donor_source ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, donor_source: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Treatment Condition" value={sampleForm.treatment_condition ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, treatment_condition: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Chemistry Version (e.g. NextGEM v3.1)" value={sampleForm.chemistry_version ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, chemistry_version: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input type="number" placeholder="Cell Count" min={0} value={sampleForm.cell_count ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, cell_count: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm" />
                    <input type="number" placeholder="Viability %" min={0} max={100} step={0.1} value={sampleForm.viability_pct ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, viability_pct: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm" />
                    <VocabularySelect fieldName="molecule_type" value={sampleForm.molecule_type} onChange={(v) => setSampleForm({ ...sampleForm, molecule_type: v })} placeholder="Molecule Type..." />
                    <VocabularySelect fieldName="library_prep_method" value={sampleForm.library_prep_method} onChange={(v) => setSampleForm({ ...sampleForm, library_prep_method: v })} placeholder="Library Prep Method..." />
                    <VocabularySelect fieldName="library_layout" value={sampleForm.library_layout} onChange={(v) => setSampleForm({ ...sampleForm, library_layout: v })} placeholder="Library Layout..." />
                  </div>
                  {sampleFormError && (
                    <p className="text-red-600 text-sm mt-2">{sampleFormError}</p>
                  )}
                  <div className="flex gap-2 mt-3">
                    <button onClick={handleAddSample} className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm">Save</button>
                    <button onClick={() => { setShowSampleForm(false); setSampleFormError(""); }} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                  </div>
                </div>
              )}

              {showBulkEdit && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg shadow p-4 mb-4">
                  <h3 className="text-sm font-semibold mb-2">Bulk Edit {selectedSampleIds.size} Sample{selectedSampleIds.size > 1 ? "s" : ""}</h3>
                  <p className="text-xs text-gray-500 mb-3">Only fields you fill in will be updated. Blank fields are left unchanged.</p>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    <input placeholder="Organism" value={bulkEditForm.organism ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, organism: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Tissue Type" value={bulkEditForm.tissue_type ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, tissue_type: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Donor ID" value={bulkEditForm.donor_source ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, donor_source: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Treatment Condition" value={bulkEditForm.treatment_condition ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, treatment_condition: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Chemistry Version" value={bulkEditForm.chemistry_version ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, chemistry_version: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                    <VocabularySelect fieldName="molecule_type" value={bulkEditForm.molecule_type} onChange={(v) => setBulkEditForm({ ...bulkEditForm, molecule_type: v || undefined })} placeholder="Molecule Type..." />
                    <VocabularySelect fieldName="library_prep_method" value={bulkEditForm.library_prep_method} onChange={(v) => setBulkEditForm({ ...bulkEditForm, library_prep_method: v || undefined })} placeholder="Library Prep Method..." />
                    <VocabularySelect fieldName="library_layout" value={bulkEditForm.library_layout} onChange={(v) => setBulkEditForm({ ...bulkEditForm, library_layout: v || undefined })} placeholder="Library Layout..." />
                  </div>
                  {bulkEditError && (
                    <p className="text-red-600 text-sm mt-2">{bulkEditError}</p>
                  )}
                  <div className="flex gap-2 mt-3">
                    <button onClick={handleBulkEdit} className="bg-amber-600 text-white px-4 py-1.5 rounded text-sm">Apply to Selected</button>
                    <button onClick={() => setShowBulkEdit(false)} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                  </div>
                </div>
              )}

              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-2 py-3 text-center">
                        <input
                          type="checkbox"
                          checked={samples.length > 0 && selectedSampleIds.size === samples.length}
                          onChange={toggleSelectAll}
                          className="rounded border-gray-300"
                        />
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">External ID</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Molecule</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Treatment</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Library Prep</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Library Layout</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Batch</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">QC</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {samples.map((s) => (
                      <tr key={s.id} className={`hover:bg-gray-50 cursor-pointer ${selectedSampleIds.has(s.id) ? "bg-blue-50/50" : ""}`} onClick={() => setViewingSample(s)}>
                        <td className="px-2 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedSampleIds.has(s.id)}
                            onChange={() => toggleSampleSelection(s.id)}
                            className="rounded border-gray-300"
                          />
                        </td>
                        <td className="px-4 py-3 text-sm">{s.sample_id_external || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.organism || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.tissue_type || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.molecule_type || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.treatment_condition || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.library_prep_method || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.library_layout || "---"}</td>
                        <td className="px-4 py-3 text-sm">{s.batch?.name || "---"}</td>
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <select
                            value={s.qc_status ?? ""}
                            onChange={(e) => { if (e.target.value) handleUpdateQC(s.id, e.target.value); }}
                            className="text-xs border rounded px-2 py-1"
                          >
                            <option value="">---</option>
                            <option value="pass">Pass</option>
                            <option value="warning">Warning</option>
                            <option value="fail">Fail</option>
                          </select>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">{s.status.replace(/_/g, " ")}</td>
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <button
                            onClick={() => startEditSample(s)}
                            className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50"
                          >
                            Edit
                          </button>
                        </td>
                      </tr>
                    ))}
                    {samples.length === 0 && (
                      <tr><td colSpan={12} className="px-4 py-8 text-center text-gray-400">No samples yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* View Sample Modal */}
              {viewingSample && (
                <DetailModal
                  title={viewingSample.sample_id_external || `Sample #${viewingSample.id}`}
                  onClose={() => setViewingSample(null)}
                  fields={[
                    { label: "External ID", value: viewingSample.sample_id_external },
                    { label: "Status", value: viewingSample.status.replace(/_/g, " ") },
                    { label: "Organism", value: viewingSample.organism },
                    { label: "Tissue Type", value: viewingSample.tissue_type },
                    { label: "Molecule Type", value: viewingSample.molecule_type },
                    { label: "Treatment", value: viewingSample.treatment_condition },
                    { label: "Library Prep", value: viewingSample.library_prep_method },
                    { label: "Library Layout", value: viewingSample.library_layout },
                    { label: "Donor ID", value: viewingSample.donor_source },
                    { label: "Chemistry Version", value: viewingSample.chemistry_version },
                    { label: "Cell Count", value: viewingSample.cell_count?.toLocaleString() },
                    { label: "Viability %", value: viewingSample.viability_pct != null ? `${viewingSample.viability_pct}%` : null },
                    { label: "Batch", value: viewingSample.batch?.name },
                    { label: "QC Status", value: viewingSample.qc_status },
                    { label: "QC Notes", value: viewingSample.qc_notes },
                    { label: "Prep Notes", value: viewingSample.prep_notes },
                    { label: "Created", value: new Date(viewingSample.created_at).toLocaleString() },
                    { label: "Updated", value: new Date(viewingSample.updated_at).toLocaleString() },
                  ]}
                  actions={
                    <button
                      onClick={() => { setViewingSample(null); startEditSample(viewingSample); }}
                      className="px-3 py-1.5 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50"
                    >
                      Edit
                    </button>
                  }
                />
              )}

              {/* Edit Sample Modal */}
              {editingSampleId !== null && (
                <div className="fixed inset-0 z-50 flex items-center justify-center">
                  <div className="fixed inset-0 bg-black/40" onClick={() => { setEditingSampleId(null); setEditSampleError(""); }} />
                  <div className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-lg font-semibold">Edit Sample</h3>
                      <button onClick={() => { setEditingSampleId(null); setEditSampleError(""); }} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">External Sample ID</label>
                        <input value={editSampleForm.sample_id_external ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, sample_id_external: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Organism</label>
                        <input value={editSampleForm.organism ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, organism: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Tissue Type</label>
                        <input value={editSampleForm.tissue_type ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, tissue_type: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Donor ID</label>
                        <input value={editSampleForm.donor_source ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, donor_source: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Treatment Condition</label>
                        <input value={editSampleForm.treatment_condition ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, treatment_condition: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Chemistry Version</label>
                        <input value={editSampleForm.chemistry_version ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, chemistry_version: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Cell Count</label>
                        <input type="number" min={0} value={editSampleForm.cell_count ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, cell_count: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Viability %</label>
                        <input type="number" min={0} max={100} step={0.1} value={editSampleForm.viability_pct ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, viability_pct: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm w-full" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Molecule Type</label>
                        <VocabularySelect fieldName="molecule_type" value={editSampleForm.molecule_type} onChange={(v) => setEditSampleForm({ ...editSampleForm, molecule_type: v })} placeholder="Molecule Type..." />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 mb-1">Library Prep Method</label>
                        <VocabularySelect fieldName="library_prep_method" value={editSampleForm.library_prep_method} onChange={(v) => setEditSampleForm({ ...editSampleForm, library_prep_method: v })} placeholder="Library Prep Method..." />
                      </div>
                      <div className="col-span-2">
                        <label className="block text-xs font-medium text-gray-500 mb-1">Library Layout</label>
                        <VocabularySelect fieldName="library_layout" value={editSampleForm.library_layout} onChange={(v) => setEditSampleForm({ ...editSampleForm, library_layout: v })} placeholder="Library Layout..." />
                      </div>
                    </div>
                    {editSampleError && (
                      <p className="text-red-600 text-sm mt-3">{editSampleError}</p>
                    )}
                    <div className="flex justify-end gap-2 mt-4">
                      <button onClick={() => { setEditingSampleId(null); setEditSampleError(""); }} className="border px-4 py-2 rounded text-sm">Cancel</button>
                      <button onClick={handleSaveSampleEdit} className="bg-bioaf-600 text-white px-4 py-2 rounded text-sm hover:bg-bioaf-700">Save Changes</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === "batches" && (
            <div>
              <button
                onClick={() => setShowBatchForm(!showBatchForm)}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700 mb-4"
              >
                Create Batch
              </button>

              {showBatchForm && (
                <div className="bg-white rounded-lg shadow p-4 mb-4">
                  <div className="grid grid-cols-2 gap-3">
                    <input placeholder="Batch Name *" value={batchForm.name} onChange={(e) => setBatchForm({ ...batchForm, name: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                    <input type="date" placeholder="Prep Date" value={batchForm.prep_date ?? ""} onChange={(e) => setBatchForm({ ...batchForm, prep_date: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                    <input placeholder="Sequencer Run ID" value={batchForm.sequencer_run_id ?? ""} onChange={(e) => setBatchForm({ ...batchForm, sequencer_run_id: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                    <VocabularySelect fieldName="instrument_model" value={batchForm.instrument_model} onChange={(v) => setBatchForm({ ...batchForm, instrument_model: v })} placeholder="Instrument Model..." />
                    <VocabularySelect fieldName="quality_score_encoding" value={batchForm.quality_score_encoding} onChange={(v) => setBatchForm({ ...batchForm, quality_score_encoding: v })} placeholder="Quality Encoding..." />
                    <input placeholder="Notes" value={batchForm.notes ?? ""} onChange={(e) => setBatchForm({ ...batchForm, notes: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                  </div>
                  <div className="flex gap-2 mt-3">
                    <button onClick={handleAddBatch} className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm">Save</button>
                    <button onClick={() => setShowBatchForm(false)} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                  </div>
                </div>
              )}

              <div className="grid gap-4">
                {batches.map((b) => (
                  <div key={b.id} className="bg-white rounded-lg shadow p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold">{b.name}</h3>
                        <p className="text-sm text-gray-500">{b.sample_count} samples</p>
                      </div>
                      <div className="text-sm text-gray-500 flex flex-wrap gap-x-4">
                        {b.prep_date && <span>Prep: {b.prep_date}</span>}
                        {b.sequencer_run_id && <span>Run: {b.sequencer_run_id}</span>}
                        {b.instrument_model && <span>{b.instrument_model}</span>}
                        {b.instrument_platform && <span className="text-xs bg-gray-100 px-2 py-0.5 rounded">{b.instrument_platform}</span>}
                      </div>
                    </div>
                    {b.notes && <p className="text-sm text-gray-500 mt-2">{b.notes}</p>}
                  </div>
                ))}
                {batches.length === 0 && (
                  <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">No batches yet</div>
                )}
              </div>
            </div>
          )}

          {activeTab === "files" && (
            <div>
              <h2 className="text-lg font-semibold mb-4">Files</h2>
              <FileBrowser experimentId={Number(id)} />
            </div>
          )}

          {activeTab === "analysis" && (
            <div className="space-y-6">
              <div className="bg-white rounded-lg shadow p-6">
                <h2 className="text-lg font-semibold mb-4">Launch Notebook</h2>
                <p className="text-sm text-gray-500 mb-4">
                  Start a Jupyter or RStudio session pre-linked to this experiment.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => handleLaunchNotebook("jupyter")}
                    className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700"
                  >
                    Launch Jupyter
                  </button>
                  <button
                    onClick={() => handleLaunchNotebook("rstudio")}
                    className="bg-blue-600 text-white px-6 py-2 rounded-md text-sm hover:bg-blue-700"
                  >
                    Launch RStudio
                  </button>
                </div>
              </div>

              {notebookSessions.length > 0 && (
                <div className="bg-white rounded-lg shadow">
                  <div className="p-6 border-b">
                    <h2 className="text-lg font-semibold">Linked Sessions</h2>
                  </div>
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Profile</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {notebookSessions.map((s) => (
                        <tr key={s.id}>
                          <td className="px-4 py-3 text-sm capitalize">{s.session_type}</td>
                          <td className="px-4 py-3 text-sm">{s.status}</td>
                          <td className="px-4 py-3 text-sm capitalize">{s.resource_profile}</td>
                          <td className="px-4 py-3 text-sm text-gray-500">{new Date(s.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <SnapshotTimeline experimentId={Number(id)} />
            </div>
          )}

          {activeTab === "pipelines" && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold">Pipeline Runs</h2>
                <button
                  onClick={() => router.push(`/pipelines?experiment=${id}`)}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                >
                  Launch Pipeline
                </button>
              </div>
              {pipelineRuns.length === 0 ? (
                <div className="bg-white rounded-lg shadow p-12 text-center">
                  <p className="text-gray-400">No pipeline runs for this experiment yet.</p>
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow overflow-hidden">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pipeline</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Progress</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Started</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {pipelineRuns.map((r) => {
                        const statusColors: Record<string, string> = {
                          pending: "bg-gray-100 text-gray-700", running: "bg-blue-100 text-blue-700",
                          completed: "bg-green-100 text-green-700", failed: "bg-red-100 text-red-700",
                          cancelled: "bg-orange-100 text-orange-700",
                        };
                        return (
                          <tr key={r.id} className="hover:bg-gray-50">
                            <td className="px-4 py-3 text-sm">{r.pipeline_name} {r.pipeline_version ? `v${r.pipeline_version}` : ""}</td>
                            <td className="px-4 py-3"><span className={`px-2 py-0.5 text-xs rounded-full ${statusColors[r.status] || ""}`}>{r.status}</span></td>
                            <td className="px-4 py-3">
                              {r.progress ? (
                                <div className="flex items-center gap-2">
                                  <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                                    <div className="h-full bg-bioaf-500 rounded-full" style={{ width: `${r.progress.percent_complete}%` }} />
                                  </div>
                                  <span className="text-xs">{Math.round(r.progress.percent_complete)}%</span>
                                </div>
                              ) : "—"}
                            </td>
                            <td className="px-4 py-3 text-sm text-gray-500">{r.started_at ? new Date(r.started_at).toLocaleString() : "—"}</td>
                            <td className="px-4 py-3">
                              <button onClick={() => router.push(`/pipelines/runs/${r.id}`)} className="text-bioaf-600 text-sm hover:underline">View</button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {activeTab === "results" && (
            <ExperimentResultsTab experimentId={Number(id)} />
          )}

          {activeTab === "provenance" && experiment && (
            <ProvenanceReportPanel
              entityType="experiment"
              entityId={Number(id)}
              entityName={experiment.name}
            />
          )}

          {activeTab === "audit" && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold">Audit Trail ({auditTotal} entries)</h2>
              </div>
              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Timestamp</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Entity</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Action</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Details</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {auditEntries.map((e) => (
                      <tr key={e.id}>
                        <td className="px-4 py-3 text-sm text-gray-500">{new Date(e.timestamp).toLocaleString()}</td>
                        <td className="px-4 py-3 text-sm">{e.entity_type} #{e.entity_id}</td>
                        <td className="px-4 py-3 text-sm">{e.action}</td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {e.details ? (
                            <details>
                              <summary className="cursor-pointer">View</summary>
                              <pre className="text-xs mt-1 bg-gray-50 p-2 rounded overflow-auto max-w-md">
                                {JSON.stringify(e.details, null, 2)}
                              </pre>
                            </details>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                    {auditEntries.length === 0 && (
                      <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-400">No audit entries</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <DataExportModal
            experimentId={Number(id)}
            experimentName={experiment?.name ?? ""}
            isOpen={showDataExport}
            onClose={() => setShowDataExport(false)}
          />
          <GeoExportModal
            experimentId={Number(id)}
            isOpen={showGeoExport}
            onClose={() => setShowGeoExport(false)}
            userRole={(() => {
              const user = getCurrentUser();
              return (user?.role_name as string) || "viewer";
            })()}
          />
        </main>
      </div>
    </div>
  );
}

/* ─── Experiment Results Tab ─── */

function ResultsPlotImage({ fileId, title, onExpand }: { fileId: number; title: string; onExpand: (url: string) => void }) {
  const [error, setError] = useState(false);
  const url = fileContentUrl(fileId);

  return (
    <div className="relative bg-gray-100 rounded min-h-[10rem] flex items-center justify-center group">
      {error ? (
        <span className="text-gray-400 text-sm">Failed to load plot</span>
      ) : (
        <>
          <img src={url} alt={title} className="w-full rounded" onError={() => setError(true)} />
          <button
            onClick={() => onExpand(url)}
            className="absolute top-2 right-2 p-1.5 bg-white/80 rounded shadow opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white"
            title="Expand plot"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5" />
            </svg>
          </button>
        </>
      )}
    </div>
  );
}

function ExperimentPlotThumbnail({
  fileId,
  title,
  onExpand,
}: {
  fileId: number;
  title: string;
  onExpand: (url: string) => void;
}) {
  const [error, setError] = useState(false);
  const url = fileContentUrl(fileId);

  if (error) return <span className="text-gray-400 text-xs">Failed to load</span>;
  return (
    <img
      src={url}
      alt={title}
      className="w-full h-full object-cover cursor-pointer"
      onClick={() => onExpand(url)}
      onError={() => setError(true)}
    />
  );
}

function ExperimentResultsTab({ experimentId }: { experimentId: number }) {
  const [qcDashboards, setQcDashboards] = useState<QCDashboardSummary[]>([]);
  const [selectedQc, setSelectedQc] = useState<QCDashboardResponse | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [cellxgenePubs, setCellxgenePubs] = useState<CellxgenePublicationResponse[]>([]);
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedPlot, setExpandedPlot] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [qc, pubs, plotData] = await Promise.all([
          api.get<QCDashboardSummary[]>(`/api/qc-dashboards?experiment_id=${experimentId}`),
          api.get<CellxgenePublicationResponse[]>(`/api/cellxgene?experiment_id=${experimentId}`),
          api.get<PlotArchiveListResponse>(`/api/plots?experiment_id=${experimentId}&page_size=12`),
        ]);
        setQcDashboards(qc);
        setCellxgenePubs(pubs);
        setPlots(plotData.plots);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    })();
  }, [experimentId]);

  const viewQcDashboard = async (id: number) => {
    try {
      const data = await api.get<QCDashboardResponse>(`/api/qc-dashboards/${id}`);
      setSelectedQc(data);
    } catch {
      // Dashboard may have been regenerated (old ID deleted). Refresh the list.
      try {
        const updated = await api.get<QCDashboardSummary[]>(`/api/qc-dashboards?experiment_id=${experimentId}`);
        setQcDashboards(updated);
      } catch {
        // ignore
      }
    }
  };

  const regenerateQc = async (runId: number) => {
    setRegenerating(true);
    try {
      const data = await api.post<QCDashboardResponse>(`/api/qc-dashboards/regenerate/${runId}`, {});
      setSelectedQc(data);
      // Refresh the list
      const updated = await api.get<QCDashboardSummary[]>(`/api/qc-dashboards?experiment_id=${experimentId}`);
      setQcDashboards(updated);
    } catch {
      // ignore
    } finally {
      setRegenerating(false);
    }
  };

  const qualityColor = (rating: string) => {
    switch (rating) {
      case "excellent": return "bg-green-100 text-green-700";
      case "good": return "bg-blue-100 text-blue-700";
      case "acceptable": return "bg-yellow-100 text-yellow-700";
      case "pending_review": return "bg-gray-100 text-gray-700";
      default: return "bg-red-100 text-red-700";
    }
  };

  if (loading) return <p className="text-gray-400 text-sm">Loading results...</p>;

  return (
    <div className="space-y-8">
      {/* QC Dashboards */}
      <section>
        <h2 className="text-lg font-semibold mb-3">QC Dashboards</h2>
        {selectedQc ? (
          <div>
            <div className="flex items-center justify-between mb-3">
              <button onClick={() => setSelectedQc(null)} className="text-blue-600 text-sm hover:underline">
                Back to list
              </button>
              <ExportPdfButton
                targetId="experiment-qc-content"
                filename={`qc-dashboard-run-${selectedQc.pipeline_run_id}.pdf`}
              />
            </div>
            <div id="experiment-qc-content" className="bg-white rounded-lg shadow p-6">
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-bold">Run #{selectedQc.pipeline_run_id}</h3>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => regenerateQc(selectedQc.pipeline_run_id)}
                    disabled={regenerating}
                    className="px-3 py-1 text-xs font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50"
                  >
                    {regenerating ? "Regenerating..." : "Regenerate"}
                  </button>
                  <span className={`px-3 py-1 rounded-full text-sm font-medium ${qualityColor(selectedQc.metrics.quality_rating)}`}>
                    {selectedQc.metrics.quality_rating}
                  </span>
                </div>
              </div>
              {selectedQc.summary_text && <p className="text-sm text-gray-600 mb-4" dangerouslySetInnerHTML={{ __html: selectedQc.summary_text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") }} />}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                {selectedQc.metrics.cell_count != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Cell Count</p><p className="font-semibold">{selectedQc.metrics.cell_count.toLocaleString()}</p></div>
                )}
                {selectedQc.metrics.median_genes_per_cell != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Median Genes/Cell</p><p className="font-semibold">{selectedQc.metrics.median_genes_per_cell.toLocaleString()}</p></div>
                )}
                {selectedQc.metrics.median_umi_per_cell != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Median UMI/Cell</p><p className="font-semibold">{selectedQc.metrics.median_umi_per_cell.toLocaleString()}</p></div>
                )}
                {selectedQc.metrics.mito_pct_median != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Mito %</p><p className="font-semibold">{selectedQc.metrics.mito_pct_median.toFixed(1)}%</p></div>
                )}
                {selectedQc.metrics.median_reads_per_cell != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Median Reads/Cell</p><p className="font-semibold">{selectedQc.metrics.median_reads_per_cell.toLocaleString()}</p></div>
                )}
                {selectedQc.metrics.saturation != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Saturation</p><p className="font-semibold">{(selectedQc.metrics.saturation * 100).toFixed(1)}%</p></div>
                )}
                {selectedQc.metrics.total_sequences != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Total Sequences</p><p className="font-semibold">{selectedQc.metrics.total_sequences.toLocaleString()}</p></div>
                )}
                {selectedQc.metrics.total_samples != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Samples</p><p className="font-semibold">{selectedQc.metrics.total_samples}</p></div>
                )}
                {selectedQc.metrics.percent_duplicates != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Duplication</p><p className="font-semibold">{selectedQc.metrics.percent_duplicates.toFixed(1)}%</p></div>
                )}
                {selectedQc.metrics.percent_gc != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">GC Content</p><p className="font-semibold">{selectedQc.metrics.percent_gc.toFixed(0)}%</p></div>
                )}
                {selectedQc.metrics.avg_sequence_length != null && (
                  <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Avg Read Length</p><p className="font-semibold">{selectedQc.metrics.avg_sequence_length.toFixed(0)} bp</p></div>
                )}
              </div>
              {selectedQc.plots.length > 0 && (
                <div>
                  <h4 className="font-medium mb-3">Plots</h4>
                  <div className="grid grid-cols-2 gap-4">
                    {selectedQc.plots.map((plot, i) => (
                      <div key={i} className="border rounded-lg p-3">
                        <p className="text-sm font-medium mb-2">{plot.title}</p>
                        <ResultsPlotImage
                          fileId={plot.file_id}
                          title={plot.title}
                          onExpand={(url) => setExpandedPlot({ url, title: plot.title })}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : qcDashboards.length === 0 ? (
          <p className="text-gray-400 text-sm">No QC dashboards for this experiment.</p>
        ) : (
          <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
            {qcDashboards.map((d) => (
              <div key={d.id} onClick={() => viewQcDashboard(d.id)} className="p-4 flex items-center justify-between hover:bg-gray-50 cursor-pointer">
                <div>
                  <p className="font-medium text-sm">Run #{d.pipeline_run_id}</p>
                  <p className="text-xs text-gray-400">{d.cell_count != null ? `${d.cell_count.toLocaleString()} cells` : ""}</p>
                </div>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${qualityColor(d.quality_rating)}`}>{d.quality_rating}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* cellxgene Publications */}
      <section>
        <h2 className="text-lg font-semibold mb-3">cellxgene Datasets</h2>
        {cellxgenePubs.length === 0 ? (
          <p className="text-gray-400 text-sm">No published datasets for this experiment.</p>
        ) : (
          <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
            {cellxgenePubs.map((pub) => (
              <div key={pub.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium text-sm">{pub.dataset_name}</p>
                  <p className="text-xs text-gray-400">Status: {pub.status}</p>
                </div>
                {pub.stable_url && pub.status === "running" && (
                  <a href={pub.stable_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 text-sm hover:underline">Open</a>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Plot Archive */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Plots</h2>
        {plots.length === 0 ? (
          <p className="text-gray-400 text-sm">No plots for this experiment.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {plots.map((plot) => (
              <div key={plot.id} className="bg-white rounded-lg shadow overflow-hidden">
                <div className="aspect-square bg-gray-100 flex items-center justify-center">
                  {plot.file ? (
                    <ExperimentPlotThumbnail
                      fileId={plot.file.id}
                      title={plot.title ?? "Plot"}
                      onExpand={(url) => setExpandedPlot({ url, title: plot.title ?? "Plot" })}
                    />
                  ) : (
                    <span className="text-gray-400 text-xs">No preview</span>
                  )}
                </div>
                <div className="p-2">
                  <p className="text-xs font-medium truncate">{plot.title}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {expandedPlot && (
        <PlotModal
          url={expandedPlot.url}
          title={expandedPlot.title}
          onClose={() => setExpandedPlot(null)}
        />
      )}
    </div>
  );
}
