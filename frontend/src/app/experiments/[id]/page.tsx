"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { SampleQCBadge } from "@/components/experiments/SampleQCBadge";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import type {
  ExperimentDetail,
  ExperimentUpdateRequest,
  Sample,
  Batch,
  AuditLogResponse,
  AuditLogEntry,
  SampleCreateRequest,
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

type Tab = "overview" | "samples" | "batches" | "analysis" | "pipelines" | "results" | "audit";

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
  const [editingOverview, setEditingOverview] = useState(false);
  const [overviewForm, setOverviewForm] = useState<ExperimentUpdateRequest>({});
  const [overviewError, setOverviewError] = useState("");
  const [showSampleForm, setShowSampleForm] = useState(false);
  const [showBatchForm, setShowBatchForm] = useState(false);
  const [sampleForm, setSampleForm] = useState<SampleCreateRequest>({});
  const [sampleFormError, setSampleFormError] = useState("");
  const [batchForm, setBatchForm] = useState<BatchCreateRequest>({ name: "" });

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
    });
    setOverviewError("");
    setEditingOverview(true);
  }

  async function handleSaveOverview() {
    setOverviewError("");
    try {
      await api.patch(`/api/experiments/${id}`, overviewForm);
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
    { key: "analysis", label: "Analysis" },
    { key: "pipelines", label: "Pipeline Runs" },
    { key: "results", label: "Results" },
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
            {(() => {
              const user = getCurrentUser();
              const role = (user?.role as string) || "viewer";
              return ["admin", "comp_bio"].includes(role) ? (
                <button
                  onClick={() => setShowGeoExport(true)}
                  className="ml-auto bg-indigo-600 text-white px-4 py-2 rounded-md text-sm hover:bg-indigo-700"
                >
                  Export to GEO
                </button>
              ) : null;
            })()}
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
              </div>
            </div>
          )}

          {activeTab === "samples" && (
            <div>
              <div className="flex items-center gap-4 mb-4">
                <button
                  onClick={() => setShowSampleForm(!showSampleForm)}
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

              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
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
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {samples.map((s) => (
                      <tr key={s.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm">{s.sample_id_external || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.organism || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.tissue_type || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.molecule_type || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.treatment_condition || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.library_prep_method || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.library_layout || "—"}</td>
                        <td className="px-4 py-3 text-sm">{s.batch?.name || "—"}</td>
                        <td className="px-4 py-3">
                          <select
                            value={s.qc_status ?? ""}
                            onChange={(e) => { if (e.target.value) handleUpdateQC(s.id, e.target.value); }}
                            className="text-xs border rounded px-2 py-1"
                          >
                            <option value="">—</option>
                            <option value="pass">Pass</option>
                            <option value="warning">Warning</option>
                            <option value="fail">Fail</option>
                          </select>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">{s.status.replace(/_/g, " ")}</td>
                      </tr>
                    ))}
                    {samples.length === 0 && (
                      <tr><td colSpan={10} className="px-4 py-8 text-center text-gray-400">No samples yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
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

          {activeTab === "audit" && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold">Audit Trail ({auditTotal} entries)</h2>
                <div className="flex gap-2">
                  <button
                    onClick={async () => {
                      const blob = await api.post<Blob>(`/api/experiments/${id}/audit/export`, { format: "csv" });
                    }}
                    className="border px-3 py-1.5 rounded text-sm hover:bg-gray-50"
                  >
                    Export CSV
                  </button>
                  <button
                    onClick={async () => {
                      const blob = await api.post<Blob>(`/api/experiments/${id}/audit/export`, { format: "json" });
                    }}
                    className="border px-3 py-1.5 rounded text-sm hover:bg-gray-50"
                  >
                    Export JSON
                  </button>
                </div>
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
          <GeoExportModal
            experimentId={Number(id)}
            isOpen={showGeoExport}
            onClose={() => setShowGeoExport(false)}
            userRole={(() => {
              const user = getCurrentUser();
              return (user?.role as string) || "viewer";
            })()}
          />
        </main>
      </div>
    </div>
  );
}

/* ─── Experiment Results Tab ─── */

function ExperimentResultsTab({ experimentId }: { experimentId: number }) {
  const [qcDashboards, setQcDashboards] = useState<QCDashboardSummary[]>([]);
  const [selectedQc, setSelectedQc] = useState<QCDashboardResponse | null>(null);
  const [cellxgenePubs, setCellxgenePubs] = useState<CellxgenePublicationResponse[]>([]);
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [loading, setLoading] = useState(true);

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
      // ignore
    }
  };

  const qualityColor = (rating: string) => {
    switch (rating) {
      case "excellent": return "bg-green-100 text-green-700";
      case "good": return "bg-blue-100 text-blue-700";
      case "acceptable": return "bg-yellow-100 text-yellow-700";
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
          <div className="bg-white rounded-lg shadow p-6">
            <button onClick={() => setSelectedQc(null)} className="text-blue-600 text-sm hover:underline mb-3">
              Back to list
            </button>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-bold">Run #{selectedQc.pipeline_run_id}</h3>
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${qualityColor(selectedQc.metrics.quality_rating)}`}>
                {selectedQc.metrics.quality_rating}
              </span>
            </div>
            {selectedQc.summary_text && <p className="text-sm text-gray-600 mb-4">{selectedQc.summary_text}</p>}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {selectedQc.metrics.cell_count != null && (
                <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Cell Count</p><p className="font-semibold">{selectedQc.metrics.cell_count.toLocaleString()}</p></div>
              )}
              {selectedQc.metrics.median_genes_per_cell != null && (
                <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Median Genes/Cell</p><p className="font-semibold">{selectedQc.metrics.median_genes_per_cell.toLocaleString()}</p></div>
              )}
              {selectedQc.metrics.mito_pct_median != null && (
                <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Mito %</p><p className="font-semibold">{selectedQc.metrics.mito_pct_median.toFixed(1)}%</p></div>
              )}
              {selectedQc.metrics.saturation != null && (
                <div className="bg-gray-50 rounded p-3"><p className="text-xs text-gray-500">Saturation</p><p className="font-semibold">{(selectedQc.metrics.saturation * 100).toFixed(1)}%</p></div>
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
                  {plot.thumbnail_url ? (
                    <img src={plot.thumbnail_url ?? undefined} alt={plot.title ?? undefined} className="w-full h-full object-cover" />
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
    </div>
  );
}
