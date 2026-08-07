"use client";

import { useEffect, useState, Suspense } from "react";
import { Modal } from "@/components/shared/Modal";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { SampleQCBadge } from "@/components/experiments/SampleQCBadge";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";
import { DataExportModal } from "@/components/experiments/DataExportModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlotModal } from "@/components/shared/PlotModal";
import { DetailModal } from "@/components/shared/DetailModal";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { FileBrowser } from "@/components/files/FileBrowser";
import { LiteratureTabPanel } from "@/components/literature/LiteratureTabPanel";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { AssaySelect } from "@/components/shared/AssaySelect";
import { CsvUploadModal } from "@/components/experiments/CsvUploadModal";
import { AutoRunConfigSection } from "@/components/experiments/AutoRunConfigSection";
import { ExtensibleVocabularySelect } from "@/components/shared/ExtensibleVocabularySelect";
import { NamingProfileSelect } from "@/components/naming/NamingProfileSelect";
import { getCurrentUser } from "@/lib/auth";
import { api, fileContentUrl, plotThumbnailContentUrl } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import { AgentReviewTab } from "@/components/agent-reviews/AgentReviewTab";
import { AgentReviewButtons } from "@/components/agent-reviews/AgentReviewButtons";
import { QCReportModal } from "@/components/qc/QCReportModal";
import { QCDashboardListItem } from "@/components/qc/QCDashboardListItem";
import { PlotThumbnail, StorageDeletedPlaceholder } from "@/components/plots/PlotThumbnail";
import { ExperimentTabKey, resolveExperimentTab } from "@/lib/experimentTabs";
import { statusBadgeClass } from "@/lib/statusStyles";
import { SAMPLE_ASSAY_OPTIONS } from "@/lib/types";
import type {
  ExperimentDetail,
  ExperimentUpdateRequest,
  FieldDefaultValue,
  NamingProfile,
  Sample,
  SampleBatch,
  SequencingBatch,
  AuditLogResponse,
  AuditLogEntry,
  SampleCreateRequest,
  SampleUpdateRequest,
  SampleBulkUpdateRequest,
  SampleBatchCreateRequest,
  ExperimentStatus,
  QCStatus,
  NotebookSession,
  SessionListResponse,
  PipelineRun,
  PipelineRunListResponse,
  PipelineRunStatus,
  QCDashboardSummary,
  CellxgenePublicationResponse,
  PlotArchiveResponse,
  PlotArchiveListResponse,
} from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";

import { clickableRow } from "@/lib/a11y";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

type Tab = ExperimentTabKey;

export default function ExperimentDetailPage() {
  return (
    <Suspense fallback={null}>
      <ExperimentDetailPageInner />
    </Suspense>
  );
}

function ExperimentDetailPageInner() {
  const toast = useToast();
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const { canAccess } = usePermissions();
  const id = params.id as string;
  const canViewResults =
    canAccess("experiments", "view") || canAccess("pipelines", "view");

  const [experiment, setExperiment] = useState<ExperimentDetail | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [batches, setBatches] = useState<SampleBatch[]>([]);
  const [seqBatches, setSeqBatches] = useState<SequencingBatch[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [activeTab, setActiveTab] = useState<Tab>(() => resolveExperimentTab(searchParams?.get("tab")));
  const [loading, setLoading] = useState(true);
  const [aiReviewSignal, setAiReviewSignal] = useState(0);

  const [notebookSessions, setNotebookSessions] = useState<NotebookSession[]>([]);
  const [pipelineRuns, setPipelineRuns] = useState<PipelineRun[]>([]);
  const [showGeoExport, setShowGeoExport] = useState(false);
  const [showDataExport, setShowDataExport] = useState(false);
  const [editingOverview, setEditingOverview] = useState(false);
  const [overviewForm, setOverviewForm] = useState<ExperimentUpdateRequest>({});
  const [overviewError, setOverviewError] = useState("");
  const [namingProfiles, setNamingProfiles] = useState<NamingProfile[]>([]);
  const [showSampleForm, setShowSampleForm] = useState(false);
  const [showCsvUpload, setShowCsvUpload] = useState(false);
  const [showBatchForm, setShowBatchForm] = useState(false);
  const [sampleForm, setSampleForm] = useState<SampleCreateRequest>({});
  const [sampleFormError, setSampleFormError] = useState("");
  const [sampleCustomFieldValues, setSampleCustomFieldValues] = useState<Record<string, string>>({});
  const [batchForm, setBatchForm] = useState<SampleBatchCreateRequest>({ name: "" });
  const [editFieldDefaults, setEditFieldDefaults] = useState<FieldDefaultValue[]>([]);
  const [editCustomFields, setEditCustomFields] = useState<{ field_name: string; field_value: string; is_required: boolean }[]>([]);

  // Sample viewing/editing state
  const [viewingSample, setViewingSample] = useState<Sample | null>(null);
  const [selectedSampleIds, setSelectedSampleIds] = useState<Set<number>>(new Set());
  const [editingSampleId, setEditingSampleId] = useState<number | null>(null);
  useDismissOnEscape(editingSampleId !== null, () => { setEditingSampleId(null); setEditSampleError(""); });
  const [editSampleForm, setEditSampleForm] = useState<SampleUpdateRequest>({});
  const [editSampleError, setEditSampleError] = useState("");
  const [editSampleCustomFields, setEditSampleCustomFields] = useState<Record<string, string>>({});
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkEditForm, setBulkEditForm] = useState<SampleUpdateRequest>({});
  const [bulkEditError, setBulkEditError] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const DEFAULTABLE_FIELDS = [
    { name: "organism", label: "Organism", type: "text" as const },
    { name: "tissue_type", label: "Tissue Type", type: "text" as const },
    { name: "donor_source", label: "Donor ID", type: "text" as const },
    { name: "treatment_condition", label: "Treatment Condition", type: "text" as const },
    { name: "chemistry_version", label: "Chemistry Version", type: "text" as const },
    { name: "sample_batch_code", label: "Sample Batch", type: "text" as const },
    { name: "sequencing_batch_code", label: "Sequencing Batch", type: "text" as const },
    { name: "molecule_type", label: "Molecule Type", type: "vocabulary" as const },
    { name: "library_prep_method", label: "Library Prep Method", type: "vocabulary" as const },
    { name: "library_layout", label: "Library Layout", type: "vocabulary" as const },
    { name: "assay", label: "Assay", type: "assay" as const },
  ];

  useEffect(() => {
    loadExperiment();
    api
      .get<NamingProfile[]>("/api/naming-profiles?status=active")
      .then(setNamingProfiles)
      .catch(() => setNamingProfiles([]));
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
    } catch (e) {
      // An empty samples table is a statement about the experiment. Say when it
      // is really a statement about the request.
      logError("loading the samples", e);
      toast.error(loadFailureMessage("Samples"));
    }
  }

  async function loadBatches() {
    try {
      const [sampleBatchData, seqBatchData] = await Promise.all([
        api.get<SampleBatch[]>(`/api/experiments/${id}/sample-batches`),
        api.get<SequencingBatch[]>(`/api/experiments/${id}/sequencing-batches`),
      ]);
      setBatches(sampleBatchData);
      setSeqBatches(seqBatchData);
    } catch (e) {
      logError("loading the batches", e);
      toast.error(loadFailureMessage("Batches"));
    }
  }

  async function loadAudit(page = 1) {
    try {
      const data = await api.get<AuditLogResponse>(`/api/experiments/${id}/audit?page=${page}`);
      setAuditEntries(data.entries);
      setAuditTotal(data.total);
    } catch (e) {
      logError("loading the audit trail", e);
      toast.error(loadFailureMessage("The audit trail"));
    }
  }

  async function loadPipelineRuns() {
    try {
      const data = await api.get<PipelineRunListResponse>(`/api/pipeline-runs?experiment_id=${id}`);
      setPipelineRuns(data.runs);
    } catch (e) {
      logError("loading the pipeline runs", e);
      toast.error(loadFailureMessage("Pipeline runs"));
    }
  }

  async function loadNotebookSessions() {
    try {
      const data = await api.get<SessionListResponse>("/api/notebooks/sessions");
      setNotebookSessions(data.sessions.filter(s => s.experiment?.id === Number(id)));
    } catch (e) {
      logError("loading the notebook sessions", e);
      toast.error(loadFailureMessage("Notebook sessions"));
    }
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
      toast.error(err instanceof Error ? err.message : "Failed to launch session");
    }
  }

  async function handleAddSample() {
    setSampleFormError("");
    try {
      const cfPayload = Object.entries(sampleCustomFieldValues)
        .filter(([, v]) => v.trim())
        .map(([name, value]) => ({ field_name: name, field_value: value }));
      const payload = { ...sampleForm, custom_fields: cfPayload.length > 0 ? cfPayload : undefined };
      await api.post(`/api/experiments/${id}/samples`, payload);
      setSampleForm({});
      setSampleCustomFieldValues({});
      setShowSampleForm(false);
      loadSamples();
      loadExperiment();
    } catch (err) {
      setSampleFormError(err instanceof Error ? err.message : "Failed to save sample");
    }
  }

  async function handleAddBatch() {
    try {
      await api.post(`/api/experiments/${id}/sample-batches`, batchForm);
      setBatchForm({ name: "" });
      setShowBatchForm(false);
      loadBatches();
      loadExperiment();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add the batch.");
    }
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
      naming_profile_id: experiment.naming_profile_id,
    });
    setEditFieldDefaults(
      experiment.field_defaults.map((fd) => ({
        field_name: fd.field_name,
        default_value: fd.default_value,
        is_required: fd.is_required,
      }))
    );
    setEditCustomFields(
      experiment.custom_fields.map((cf) => ({
        field_name: cf.field_name,
        field_value: cf.field_value ?? "",
        is_required: cf.is_required,
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
      const customFields = editCustomFields
        .filter((f) => f.field_name.trim())
        .map((f) => ({ field_name: f.field_name.trim(), field_value: f.field_value.trim(), field_type: "string", is_required: f.is_required }));
      const payload = { ...overviewForm, field_defaults: editFieldDefaults, custom_fields: customFields };
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
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update the QC status.");
    }
  }

  function handleCsvUploadSuccess() {
    loadSamples();
    loadExperiment();
  }

  function startEditSample(sample: Sample) {
    setEditingSampleId(sample.id);
    setEditSampleForm({
      external_id: sample.external_id,
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
      assay: sample.assay,
      sample_batch_code: sample.sample_batch?.name ?? null,
      sequencing_batch_code: sample.sequencing_batch?.code ?? null,
    });
    const cfValues: Record<string, string> = {};
    for (const cf of sample.custom_fields ?? []) {
      cfValues[cf.field_name] = cf.field_value ?? "";
    }
    setEditSampleCustomFields(cfValues);
    setEditSampleError("");
  }

  async function handleSaveSampleEdit() {
    if (!editingSampleId) return;
    setEditSampleError("");
    try {
      const cfPayload = Object.entries(editSampleCustomFields)
        .filter(([, v]) => v.trim())
        .map(([name, value]) => ({ field_name: name, field_value: value }));
      const payload = { ...editSampleForm, custom_fields: cfPayload };
      await api.patch(`/api/samples/${editingSampleId}`, payload);
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

  async function handleBulkDelete() {
    if (selectedSampleIds.size === 0) return;
    setDeleting(true);
    try {
      await api.post("/api/samples/bulk/delete", {
        sample_ids: Array.from(selectedSampleIds),
      });
      setShowDeleteConfirm(false);
      setSelectedSampleIds(new Set());
      loadSamples();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete the selected samples. Nothing was removed.");
    } finally {
      setDeleting(false);
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
      toast.error(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  if (loading) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LoadingSpinner size="lg" />
      </main>
    );
  }

  if (!experiment) {
    return (
      <main className="flex-1 flex items-center justify-center">
        <p className="text-ink-subtle">Experiment not found</p>
      </main>
    );
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "samples", label: `Samples (${experiment.sample_count})` },
    { key: "batches", label: "Batches" },
    { key: "files", label: "Files" },
    { key: "literature", label: "Literature" },
    { key: "analysis", label: "Analysis" },
    { key: "pipelines", label: "Pipeline Runs" },
    ...(canViewResults ? [{ key: "results" as Tab, label: "Results" }] : []),
    { key: "provenance", label: "Provenance" },
    { key: "audit", label: "Audit Trail" },
    { key: "agent_review", label: "AI Review" },
  ];

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => router.push("/experiments")} className="text-ink-subtle hover:text-ink-muted">
          ← Back
        </button>
        <h1 className="text-2xl font-bold">{experiment.name}</h1>
        {experiment.code && (
          <span className="text-sm font-mono bg-elevated text-gray-600 px-2 py-0.5 rounded" title="Internal ID">{experiment.code}</span>
        )}
        {experiment.external_id && (
          <span className="text-sm font-mono bg-blue-50 text-blue-700 px-2 py-0.5 rounded" title="External ID">{experiment.external_id}</span>
        )}
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
                  className="bg-elevated text-gray-800 px-4 py-2 rounded-md text-sm hover:bg-gray-200"
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

      <div className="border-b border-hairline mb-6">
        <nav className="flex -mb-px space-x-8">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`py-2 px-1 border-b-2 text-sm font-medium ${
                activeTab === tab.key
                  ? "border-bioaf-500 text-bioaf-600"
                  : "border-transparent text-ink-subtle hover:text-ink-muted hover:border-gray-300"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === "overview" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-surface rounded-lg shadow p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Experiment Details</h2>
              {!editingOverview && (
                <button onClick={startEditOverview} className="text-sm text-bioaf-600 hover:underline">Edit</button>
              )}
            </div>

            {editingOverview ? (
              <div className="space-y-3">
                <div>
                  <label htmlFor="name" className="block text-sm text-ink-subtle mb-1">Name</label>
                  <input id="name" value={overviewForm.name ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, name: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm" />
                </div>
                <div>
                  <label className="block text-sm text-ink-subtle mb-1">Design Type</label>
                  <ExtensibleVocabularySelect
                    fieldName="design_type"
                    value={overviewForm.design_type ?? null}
                    onChange={(v) => setOverviewForm({ ...overviewForm, design_type: v })}
                    placeholder="Select design type..."
                    className="w-full border rounded px-3 py-1.5 text-sm"
                  />
                </div>
                <div>
                  <label htmlFor="hypothesis" className="block text-sm text-ink-subtle mb-1">Hypothesis</label>
                  <textarea id="hypothesis" value={overviewForm.hypothesis ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, hypothesis: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
                </div>
                <div>
                  <label htmlFor="description" className="block text-sm text-ink-subtle mb-1">Description</label>
                  <textarea id="description" value={overviewForm.description ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, description: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
                </div>
                {(() => {
                  const templateProfile =
                    experiment.template_naming_profile_id != null
                      ? namingProfiles.find((p) => p.id === experiment.template_naming_profile_id)
                      : null;
                  const overriding = overviewForm.naming_profile_id != null;
                  const hint = overriding
                    ? "Override: this experiment uses the selected profile."
                    : templateProfile
                      ? `Inherited from template '${experiment.template_name}': ${templateProfile.name}.`
                      : "No profile set on this experiment or its template.";
                  return (
                    <NamingProfileSelect
                      id="exp-naming-profile"
                      label="Naming profile (override)"
                      hint={hint}
                      emptyLabel={templateProfile ? "Inherit from template" : "No profile"}
                      value={overviewForm.naming_profile_id ?? null}
                      onChange={(v) => setOverviewForm({ ...overviewForm, naming_profile_id: v })}
                      profiles={namingProfiles}
                    />
                  );
                })()}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="start-date" className="block text-sm text-ink-subtle mb-1">Start Date</label>
                    <input id="start-date" type="date" value={overviewForm.start_date ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, start_date: e.target.value || null })} className="w-full border rounded px-3 py-1.5 text-sm" />
                  </div>
                  <div>
                    <label htmlFor="expected-samples" className="block text-sm text-ink-subtle mb-1">Expected Samples</label>
                    <input id="expected-samples" type="number" min={0} value={overviewForm.expected_sample_count ?? ""} onChange={(e) => setOverviewForm({ ...overviewForm, expected_sample_count: e.target.value ? Number(e.target.value) : null })} className="w-full border rounded px-3 py-1.5 text-sm" />
                  </div>
                </div>
                <div className="border-t pt-3 mt-3">
                  <h3 className="text-sm font-medium text-ink-muted mb-2">Sample Field Defaults</h3>
                  <p className="text-xs text-ink-subtle mb-2">Default values applied to new samples. Per-sample values override these.</p>
                  <div className="space-y-2">
                    {DEFAULTABLE_FIELDS.map((field) => {
                      const current = editFieldDefaults.find((d) => d.field_name === field.name);
                      return (
                        <div key={field.name} className="grid grid-cols-3 gap-2 items-center">
                          <span className="text-xs text-gray-600">{field.label}</span>
                          {field.type === "assay" ? (
                            <AssaySelect
                              value={current?.default_value ?? null}
                              onChange={(v) => updateEditFieldDefault(field.name, v, current?.is_required ?? null)}
                              placeholder="Default..."
                            />
                          ) : field.type === "vocabulary" ? (
                            <VocabularySelect
                              fieldName={field.name}
                              value={current?.default_value ?? null}
                              onChange={(v) => updateEditFieldDefault(field.name, v, current?.is_required ?? null)}
                              placeholder={`Default...`}
                            />
                          ) : (
                            <input aria-label="Default"
                              value={current?.default_value ?? ""}
                              onChange={(e) => updateEditFieldDefault(field.name, e.target.value || null, current?.is_required ?? null)}
                              placeholder="Default..."
                              className="border rounded px-2 py-1 text-sm"
                            />
                          )}
                          <label className="flex items-center gap-1 text-xs text-ink-subtle">
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
                <div className="border-t pt-3 mt-3">
                  <h3 className="text-sm font-medium text-ink-muted mb-2">Custom Fields</h3>
                  <div className="space-y-2">
                    {editCustomFields.map((cf, idx) => (
                      <div key={idx} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] gap-2 items-center">
                        <input aria-label="Field name"
                          value={cf.field_name}
                          onChange={(e) => setEditCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, field_name: e.target.value } : f))}
                          placeholder="Field name"
                          className="w-full border rounded px-2 py-1 text-sm"
                        />
                        <div className="min-w-0">
                          <input aria-label="Custom field value"
                            value={cf.field_value}
                            onChange={(e) => setEditCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, field_value: e.target.value } : f))}
                            placeholder="Value"
                            className="w-full border rounded px-2 py-1 text-sm"
                          />
                        </div>
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          <label className="flex items-center gap-1 text-xs text-gray-600">
                            <input
                              type="checkbox"
                              checked={cf.is_required}
                              onChange={(e) => setEditCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, is_required: e.target.checked } : f))}
                              className="rounded border-gray-300"
                            />
                            Required
                          </label>
                          <button
                            type="button"
                            onClick={() => setEditCustomFields((prev) => prev.filter((_, i) => i !== idx))}
                            className="text-red-400 hover:text-red-600 text-xs ml-1"
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => setEditCustomFields((prev) => [...prev, { field_name: "", field_value: "", is_required: false }])}
                      className="text-sm text-bioaf-600 hover:underline"
                    >
                      + Add Field
                    </button>
                  </div>
                </div>
                {overviewError && <p className="text-red-600 text-sm">{overviewError}</p>}
                <div className="flex gap-2 pt-1">
                  <Button size="sm" onClick={handleSaveOverview}>Save</Button>
                  <button onClick={() => { setEditingOverview(false); setOverviewError(""); }} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                </div>
              </div>
            ) : (
              <dl className="space-y-3">
                <div><dt className="text-sm text-ink-subtle">Project</dt><dd className="text-sm">{experiment.project?.name || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Template</dt><dd className="text-sm">{experiment.template_name || "—"}</dd></div>
                <div>
                  <dt className="text-sm text-ink-subtle">Naming Profile</dt>
                  <dd className="text-sm">
                    {(() => {
                      const eff = experiment.effective_naming_profile_id;
                      if (eff == null) return "—";
                      const profile = namingProfiles.find((p) => p.id === eff);
                      const label = profile?.name ?? `#${eff}`;
                      const overriding = experiment.naming_profile_id != null;
                      return `${label}${overriding ? " (override)" : " (from template)"}`;
                    })()}
                  </dd>
                </div>
                <div><dt className="text-sm text-ink-subtle">Design Type</dt><dd className="text-sm">{experiment.design_type || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Owner</dt><dd className="text-sm">{experiment.owner?.name || experiment.owner?.email || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Hypothesis</dt><dd className="text-sm">{experiment.hypothesis || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Description</dt><dd className="text-sm">{experiment.description || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Start Date</dt><dd className="text-sm">{experiment.start_date || "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Expected Samples</dt><dd className="text-sm">{experiment.expected_sample_count ?? "—"}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Actual Samples</dt><dd className="text-sm">{experiment.sample_count}</dd></div>
                <div><dt className="text-sm text-ink-subtle">Created</dt><dd className="text-sm">{new Date(experiment.created_at).toLocaleString()}</dd></div>
              </dl>
            )}
          </div>

          <div className="bg-surface rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold mb-4">Status</h2>
            <div className="space-y-3">
              <div className="flex items-center gap-4">
                <ExperimentStatusBadge status={experiment.status} />
                <select
                  aria-label="Change experiment status"
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
                  <p className="text-xs text-ink-subtle mb-3">Controlled by template: {experiment.template_name}</p>
                )}
                <dl className="space-y-2">
                  {experiment.custom_fields.map((cf) => (
                    <div key={cf.id} className="flex items-center gap-2">
                      <dt className="text-sm text-ink-subtle">{cf.field_name}</dt>
                      <dd className="text-sm text-ink-subtle">{cf.field_value || "—"}</dd>
                      {cf.is_required && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">required</span>}
                    </div>
                  ))}
                </dl>
              </>
            )}

            {experiment.field_defaults.length > 0 && (
              <>
                <h3 className="text-md font-semibold mt-6 mb-3">Sample Field Defaults</h3>
                <p className="text-xs text-ink-subtle mb-3">Applied to new samples unless overridden per-sample.</p>
                <dl className="space-y-2">
                  {experiment.field_defaults.map((fd) => {
                    const label = DEFAULTABLE_FIELDS.find((f) => f.name === fd.field_name)?.label ?? fd.field_name;
                    return (
                      <div key={fd.id} className="flex items-center gap-2">
                        <dt className="text-sm text-ink-subtle">{label}</dt>
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
                  // Initialize custom field values from experiment-level defaults
                  const cfDefaults: Record<string, string> = {};
                  for (const cf of experiment.custom_fields) {
                    cfDefaults[cf.field_name] = cf.field_value ?? "";
                  }
                  setSampleCustomFieldValues(cfDefaults);
                }
                setShowSampleForm(!showSampleForm);
              }}
              className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
            >
              Add Sample
            </button>
            <button
              onClick={() => setShowCsvUpload(true)}
              className="bg-surface border border-gray-300 px-4 py-2 rounded-md text-sm hover:bg-surface-muted"
            >
              Import Samples
            </button>
            {selectedSampleIds.size > 0 && (
              <>
                <button
                  onClick={() => { setShowBulkEdit(true); setBulkEditForm({}); setBulkEditError(""); }}
                  className="bg-amber-600 text-white px-4 py-2 rounded-md text-sm hover:bg-amber-700"
                >
                  Edit Selected ({selectedSampleIds.size})
                </button>
                <Button variant="danger"
                  onClick={() => setShowDeleteConfirm(true)}
                >
                  Delete Selected ({selectedSampleIds.size})
                </Button>
              </>
            )}
          </div>

          {showSampleForm && (
            <div className="bg-surface rounded-lg shadow p-4 mb-4">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                <input aria-label="External Sample ID" placeholder="External Sample ID" value={sampleForm.external_id ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, external_id: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Organism" placeholder="Organism" value={sampleForm.organism ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, organism: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Tissue Type" placeholder="Tissue Type" value={sampleForm.tissue_type ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, tissue_type: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Donor ID" placeholder="Donor ID" value={sampleForm.donor_source ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, donor_source: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Treatment Condition" placeholder="Treatment Condition" value={sampleForm.treatment_condition ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, treatment_condition: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Chemistry Version" placeholder="Chemistry Version (e.g. NextGEM v3.1)" value={sampleForm.chemistry_version ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, chemistry_version: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Cell Count" type="number" placeholder="Cell Count" min={0} value={sampleForm.cell_count ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, cell_count: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Viability %" type="number" placeholder="Viability %" min={0} max={100} step={0.1} value={sampleForm.viability_pct ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, viability_pct: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm" />
                <VocabularySelect fieldName="molecule_type" value={sampleForm.molecule_type} onChange={(v) => setSampleForm({ ...sampleForm, molecule_type: v })} placeholder="Molecule Type..." />
                <VocabularySelect fieldName="library_prep_method" value={sampleForm.library_prep_method} onChange={(v) => setSampleForm({ ...sampleForm, library_prep_method: v })} placeholder="Library Prep Method..." />
                <VocabularySelect fieldName="library_layout" value={sampleForm.library_layout} onChange={(v) => setSampleForm({ ...sampleForm, library_layout: v })} placeholder="Library Layout..." />
                <AssaySelect value={sampleForm.assay} onChange={(v) => setSampleForm({ ...sampleForm, assay: v })} />
                <input aria-label="Sample Batch" placeholder="Sample Batch" value={sampleForm.sample_batch_code ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, sample_batch_code: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Sequencing Batch" placeholder="Sequencing Batch" value={sampleForm.sequencing_batch_code ?? ""} onChange={(e) => setSampleForm({ ...sampleForm, sequencing_batch_code: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                {experiment?.custom_fields.map((cf) => (
                  <input aria-label={cf.field_name}
                    key={cf.id}
                    placeholder={cf.field_name}
                    value={sampleCustomFieldValues[cf.field_name] ?? ""}
                    onChange={(e) => setSampleCustomFieldValues((prev) => ({ ...prev, [cf.field_name]: e.target.value }))}
                    className="border rounded px-3 py-2 text-sm"
                  />
                ))}
              </div>
              {sampleFormError && (
                <p className="text-red-600 text-sm mt-2">{sampleFormError}</p>
              )}
              <div className="flex gap-2 mt-3">
                <Button size="sm" onClick={handleAddSample}>Save</Button>
                <button onClick={() => { setShowSampleForm(false); setSampleFormError(""); }} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
              </div>
            </div>
          )}

          {showBulkEdit && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg shadow p-4 mb-4">
              <h3 className="text-sm font-semibold mb-2">Bulk Edit {selectedSampleIds.size} Sample{selectedSampleIds.size > 1 ? "s" : ""}</h3>
              <p className="text-xs text-ink-subtle mb-3">Only fields you fill in will be updated. Blank fields are left unchanged.</p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                <input aria-label="Organism" placeholder="Organism" value={bulkEditForm.organism ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, organism: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Tissue Type" placeholder="Tissue Type" value={bulkEditForm.tissue_type ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, tissue_type: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Donor ID" placeholder="Donor ID" value={bulkEditForm.donor_source ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, donor_source: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Treatment Condition" placeholder="Treatment Condition" value={bulkEditForm.treatment_condition ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, treatment_condition: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                <input aria-label="Chemistry Version" placeholder="Chemistry Version" value={bulkEditForm.chemistry_version ?? ""} onChange={(e) => setBulkEditForm({ ...bulkEditForm, chemistry_version: e.target.value || undefined })} className="border rounded px-3 py-2 text-sm" />
                <VocabularySelect fieldName="molecule_type" value={bulkEditForm.molecule_type} onChange={(v) => setBulkEditForm({ ...bulkEditForm, molecule_type: v || undefined })} placeholder="Molecule Type..." />
                <VocabularySelect fieldName="library_prep_method" value={bulkEditForm.library_prep_method} onChange={(v) => setBulkEditForm({ ...bulkEditForm, library_prep_method: v || undefined })} placeholder="Library Prep Method..." />
                <VocabularySelect fieldName="library_layout" value={bulkEditForm.library_layout} onChange={(v) => setBulkEditForm({ ...bulkEditForm, library_layout: v || undefined })} placeholder="Library Layout..." />
                <AssaySelect value={bulkEditForm.assay} onChange={(v) => setBulkEditForm({ ...bulkEditForm, assay: v || undefined })} />
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

          <div className="bg-surface rounded-lg shadow overflow-hidden">
            <table className="min-w-full divide-y divide-hairline">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-2 py-3 text-center">
                    <input aria-label="Select all samples"
                      type="checkbox"
                      checked={samples.length > 0 && selectedSampleIds.size === samples.length}
                      onChange={toggleSelectAll}
                      className="rounded border-gray-300"
                    />
                  </th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">ID</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Organism</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Tissue</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Molecule</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Treatment</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Library Prep</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Library Layout</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Files</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">QC</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                  <th scope="col" className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {samples.map((s) => (
                  <tr key={s.id} className={`hover:bg-surface-muted cursor-pointer ${selectedSampleIds.has(s.id) ? "bg-blue-50/50" : ""}`} {...clickableRow(() => setViewingSample(s))}>
                    <td className="px-2 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select sample ${s.external_id ?? s.id}`}
                        checked={selectedSampleIds.has(s.id)}
                        onChange={() => toggleSampleSelection(s.id)}
                        className="rounded border-gray-300"
                      />
                    </td>
                    <td className="px-4 py-3 text-sm">{s.external_id || `#${s.id}`}</td>
                    <td className="px-4 py-3 text-sm">{s.organism || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.tissue_type || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.molecule_type || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.treatment_condition || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.library_prep_method || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.library_layout || "---"}</td>
                    <td className="px-4 py-3 text-sm">{s.file_count}</td>
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      <select
                        aria-label={`QC status for sample ${s.external_id ?? s.id}`}
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
                    <td className="px-4 py-3 text-sm text-ink-subtle">{s.status.replace(/_/g, " ")}</td>
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
                  <tr><td colSpan={10} className="px-4 py-8 text-center text-ink-subtle">No samples yet</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* View Sample Modal */}
          {viewingSample && (
            <DetailModal
              title={viewingSample.external_id || `Sample #${viewingSample.id}`}
              onClose={() => setViewingSample(null)}
              fields={[
                { label: "Internal ID", value: `#${viewingSample.id}` },
                { label: "External ID", value: viewingSample.external_id },
                { label: "Status", value: viewingSample.status.replace(/_/g, " ") },
                { label: "Organism", value: viewingSample.organism },
                { label: "Tissue Type", value: viewingSample.tissue_type },
                { label: "Molecule Type", value: viewingSample.molecule_type },
                { label: "Assay", value: viewingSample.assay ? (SAMPLE_ASSAY_OPTIONS.find((o) => o.value === viewingSample.assay)?.label ?? viewingSample.assay) : null },
                { label: "Treatment", value: viewingSample.treatment_condition },
                { label: "Library Prep", value: viewingSample.library_prep_method },
                { label: "Library Layout", value: viewingSample.library_layout },
                { label: "Donor ID", value: viewingSample.donor_source },
                { label: "Chemistry Version", value: viewingSample.chemistry_version },
                { label: "Cell Count", value: viewingSample.cell_count?.toLocaleString() },
                { label: "Viability %", value: viewingSample.viability_pct != null ? `${viewingSample.viability_pct}%` : null },
                { label: "Sample Batch", value: viewingSample.sample_batch?.name },
                { label: "Sequencing Batch", value: viewingSample.sequencing_batch?.code },
                { label: "Batch Position", value: viewingSample.sequencing_batch_position },
                { label: "QC Status", value: viewingSample.qc_status },
                { label: "QC Notes", value: viewingSample.qc_notes },
                { label: "Prep Notes", value: viewingSample.prep_notes },
                ...(viewingSample.custom_fields ?? []).map((cf) => ({
                  label: cf.field_name,
                  value: cf.field_value,
                })),
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
            <Modal
              open
              title="Edit Sample"
              onClose={() => { setEditingSampleId(null); setEditSampleError(""); }}
              size="md"
              footer={
                <>
                <button onClick={() => { setEditingSampleId(null); setEditSampleError(""); }} className="border px-4 py-2 rounded text-sm">Cancel</button>
                <Button onClick={handleSaveSampleEdit}>Save Changes</Button>
                </>
              }
            >
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="external-sample-id" className="block text-xs font-medium text-ink-subtle mb-1">External Sample ID</label>
                  <input id="external-sample-id" value={editSampleForm.external_id ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, external_id: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="organism" className="block text-xs font-medium text-ink-subtle mb-1">Organism</label>
                  <input id="organism" value={editSampleForm.organism ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, organism: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="tissue-type" className="block text-xs font-medium text-ink-subtle mb-1">Tissue Type</label>
                  <input id="tissue-type" value={editSampleForm.tissue_type ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, tissue_type: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="donor-id" className="block text-xs font-medium text-ink-subtle mb-1">Donor ID</label>
                  <input id="donor-id" value={editSampleForm.donor_source ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, donor_source: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="treatment-condition" className="block text-xs font-medium text-ink-subtle mb-1">Treatment Condition</label>
                  <input id="treatment-condition" value={editSampleForm.treatment_condition ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, treatment_condition: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="chemistry-version" className="block text-xs font-medium text-ink-subtle mb-1">Chemistry Version</label>
                  <input id="chemistry-version" value={editSampleForm.chemistry_version ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, chemistry_version: e.target.value })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="cell-count" className="block text-xs font-medium text-ink-subtle mb-1">Cell Count</label>
                  <input id="cell-count" type="number" min={0} value={editSampleForm.cell_count ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, cell_count: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="viability" className="block text-xs font-medium text-ink-subtle mb-1">Viability %</label>
                  <input id="viability" type="number" min={0} max={100} step={0.1} value={editSampleForm.viability_pct ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, viability_pct: e.target.value ? Number(e.target.value) : null })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-ink-subtle mb-1">Molecule Type</label>
                  <VocabularySelect fieldName="molecule_type" value={editSampleForm.molecule_type} onChange={(v) => setEditSampleForm({ ...editSampleForm, molecule_type: v })} placeholder="Molecule Type..." />
                </div>
                <div>
                  <label className="block text-xs font-medium text-ink-subtle mb-1">Library Prep Method</label>
                  <VocabularySelect fieldName="library_prep_method" value={editSampleForm.library_prep_method} onChange={(v) => setEditSampleForm({ ...editSampleForm, library_prep_method: v })} placeholder="Library Prep Method..." />
                </div>
                <div>
                  <label className="block text-xs font-medium text-ink-subtle mb-1">Library Layout</label>
                  <VocabularySelect fieldName="library_layout" value={editSampleForm.library_layout} onChange={(v) => setEditSampleForm({ ...editSampleForm, library_layout: v })} placeholder="Library Layout..." />
                </div>
                <div>
                  <label className="block text-xs font-medium text-ink-subtle mb-1">Assay</label>
                  <AssaySelect value={editSampleForm.assay} onChange={(v) => setEditSampleForm({ ...editSampleForm, assay: v })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="sample-batch" className="block text-xs font-medium text-ink-subtle mb-1">Sample Batch</label>
                  <input id="sample-batch" value={editSampleForm.sample_batch_code ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, sample_batch_code: e.target.value || null })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                <div>
                  <label htmlFor="sequencing-batch" className="block text-xs font-medium text-ink-subtle mb-1">Sequencing Batch</label>
                  <input id="sequencing-batch" value={editSampleForm.sequencing_batch_code ?? ""} onChange={(e) => setEditSampleForm({ ...editSampleForm, sequencing_batch_code: e.target.value || null })} className="border rounded px-3 py-2 text-sm w-full" />
                </div>
                {experiment?.custom_fields.map((cf) => (
                  <div key={cf.id}>
                    <label id="lbl-page-1" className="block text-xs font-medium text-ink-subtle mb-1">{cf.field_name}{cf.is_required ? " *" : ""}</label>
                    <input aria-labelledby="lbl-page-1"
                      value={editSampleCustomFields[cf.field_name] ?? ""}
                      onChange={(e) => setEditSampleCustomFields((prev) => ({ ...prev, [cf.field_name]: e.target.value }))}
                      className="border rounded px-3 py-2 text-sm w-full"
                    />
                  </div>
                ))}
              </div>
              {editSampleError && (
                <p className="text-red-600 text-sm mt-3">{editSampleError}</p>
              )}
            </Modal>
          )}
        </div>
      )}

      {activeTab === "batches" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Sample Batches */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold">Sample Batches</h3>
              <Button size="sm"
                onClick={() => setShowBatchForm(!showBatchForm)}
              >
                Create Sample Batch
              </Button>
            </div>

            {showBatchForm && (
              <div className="bg-surface rounded-lg shadow p-4 mb-4">
                <div className="grid grid-cols-2 gap-3">
                  <input aria-label="Batch Name" placeholder="Batch Name *" value={batchForm.name} onChange={(e) => setBatchForm({ ...batchForm, name: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                  <input aria-label="Prep Date" type="date" placeholder="Prep Date" value={batchForm.prep_date ?? ""} onChange={(e) => setBatchForm({ ...batchForm, prep_date: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                  <input aria-label="Notes" placeholder="Notes" value={batchForm.notes ?? ""} onChange={(e) => setBatchForm({ ...batchForm, notes: e.target.value || null })} className="border rounded px-3 py-2 text-sm col-span-2" />
                </div>
                <div className="flex gap-2 mt-3">
                  <Button size="sm" onClick={handleAddBatch}>Save</Button>
                  <button onClick={() => setShowBatchForm(false)} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
                </div>
              </div>
            )}

            <div className="grid gap-3">
              {batches.map((b) => (
                <div key={b.id} className="bg-surface rounded-lg shadow p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="font-semibold">{b.name}</h4>
                      <p className="text-sm text-ink-subtle">{b.sample_count} samples</p>
                    </div>
                    <div className="text-sm text-ink-subtle">
                      {b.prep_date && <span>Prep: {b.prep_date}</span>}
                    </div>
                  </div>
                  {b.notes && <p className="text-sm text-ink-subtle mt-2">{b.notes}</p>}
                </div>
              ))}
              {batches.length === 0 && (
                <div className="bg-surface rounded-lg shadow p-6 text-center text-ink-subtle text-sm">No sample batches yet</div>
              )}
            </div>
          </div>

          {/* Sequencing Batches */}
          <div>
            <h3 className="text-lg font-semibold mb-4">Sequencing Batches</h3>
            <div className="grid gap-3">
              {seqBatches.map((sb) => {
                const progress = sb.expected_file_count ? Math.round((sb.ingested_file_count / sb.expected_file_count) * 100) : 0;

                return (
                  <div key={sb.id} className="bg-surface rounded-lg shadow p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <h4 className="font-semibold">{sb.code}</h4>
                        {sb.instrument_model && <p className="text-sm text-ink-subtle">{sb.instrument_model}</p>}
                      </div>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass("sampleBatch", sb.status)}`}>
                        {sb.status.replace("_", " ")}
                      </span>
                    </div>
                    {sb.expected_file_count && (
                      <div className="mt-2">
                        <div className="flex justify-between text-xs text-ink-subtle mb-1">
                          <span>Files: {sb.ingested_file_count}/{sb.expected_file_count}</span>
                          <span>{progress}%</span>
                        </div>
                        <div className="w-full bg-gray-200 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full ${sb.status === "complete" ? "bg-green-500" : sb.status === "failed" ? "bg-red-500" : "bg-blue-500"}`}
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      </div>
                    )}
                    {sb.manifest_received_at && (
                      <p className="text-xs text-ink-subtle mt-2">Received: {new Date(sb.manifest_received_at).toLocaleString()}</p>
                    )}
                  </div>
                );
              })}
              {seqBatches.length === 0 && (
                <div className="bg-surface rounded-lg shadow p-6 text-center text-ink-subtle text-sm">No sequencing batches linked to this experiment</div>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === "files" && (
        <div>
          <h2 className="text-lg font-semibold mb-4">Files</h2>
          <FileBrowser experimentId={Number(id)} showSearch showUpload />
        </div>
      )}

      {activeTab === "literature" && (
        <div>
          <h2 className="text-lg font-semibold mb-4">Literature</h2>
          <LiteratureTabPanel experimentId={Number(id)} />
        </div>
      )}

      {activeTab === "analysis" && (
        <div className="space-y-6">
          <div className="bg-surface rounded-lg shadow p-6">
            <h2 className="text-lg font-semibold mb-4">Launch Notebook</h2>
            <p className="text-sm text-ink-subtle mb-4">
              Start a Jupyter or RStudio session pre-linked to this experiment.
            </p>
            <div className="flex gap-3">
              <Button
                onClick={() => handleLaunchNotebook("jupyter")}
              >
                Launch Jupyter
              </Button>
              <Button
                onClick={() => handleLaunchNotebook("rstudio")}
              >
                Launch RStudio
              </Button>
            </div>
          </div>

          {notebookSessions.length > 0 && (
            <div className="bg-surface rounded-lg shadow">
              <div className="p-6 border-b">
                <h2 className="text-lg font-semibold">Linked Sessions</h2>
              </div>
              <table className="min-w-full divide-y divide-hairline">
                <thead className="bg-gray-50">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Type</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Profile</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {notebookSessions.map((s) => (
                    <tr key={s.id}>
                      <td className="px-4 py-3 text-sm capitalize">{s.session_type}</td>
                      <td className="px-4 py-3 text-sm">{s.status}</td>
                      <td className="px-4 py-3 text-sm capitalize">{s.resource_profile}</td>
                      <td className="px-4 py-3 text-sm text-ink-subtle">{new Date(s.created_at).toLocaleString()}</td>
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
            <Button
              onClick={() => router.push(`/pipelines?experiment=${id}`)}
            >
              Launch Pipeline
            </Button>
          </div>
          {pipelineRuns.length === 0 ? (
            <div className="bg-surface rounded-lg shadow p-12 text-center">
              <p className="text-ink-subtle">No pipeline runs for this experiment yet.</p>
            </div>
          ) : (
            <div className="bg-surface rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-hairline">
                <thead className="bg-gray-50">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Pipeline</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Progress</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Started</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {pipelineRuns.map((r) => {
                    return (
                      <tr key={r.id} className="hover:bg-surface-muted">
                        <td className="px-4 py-3 text-sm">{r.pipeline_name} {r.pipeline_version ? `v${r.pipeline_version}` : ""}</td>
                        <td className="px-4 py-3"><span className={`px-2 py-0.5 text-xs rounded-full ${statusBadgeClass("pipelineRun", r.status)}`}>{r.status}</span></td>
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
                        <td className="px-4 py-3 text-sm text-ink-subtle">{r.started_at ? new Date(r.started_at).toLocaleString() : "—"}</td>
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

          <AutoRunConfigSection experimentId={Number(id)} />
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
          <div className="bg-surface rounded-lg shadow overflow-hidden">
            <table className="min-w-full divide-y divide-hairline">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Timestamp</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Entity</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Action</th>
                  <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {auditEntries.map((e) => (
                  <tr key={e.id}>
                    <td className="px-4 py-3 text-sm text-ink-subtle">{new Date(e.timestamp).toLocaleString()}</td>
                    <td className="px-4 py-3 text-sm">{e.entity_type} #{e.entity_id}</td>
                    <td className="px-4 py-3 text-sm">{e.action}</td>
                    <td className="px-4 py-3 text-sm text-ink-subtle">
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
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-ink-subtle">No audit entries</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "agent_review" && experiment && (
        <div className="space-y-4">
          <AgentReviewButtons
            mode="experiment"
            experimentId={experiment.id}
            onTriggered={() => setAiReviewSignal((v) => v + 1)}
          />
          <AgentReviewTab
            entityType="experiment"
            entityId={experiment.id}
            refreshSignal={aiReviewSignal}
          />
        </div>
      )}
      {showCsvUpload && (
        <CsvUploadModal
          experimentId={Number(id)}
          existingCustomFields={experiment?.custom_fields?.map((cf) => cf.field_name) ?? []}
          onClose={() => setShowCsvUpload(false)}
          onSuccess={handleCsvUploadSuccess}
        />
      )}
      <Modal
        open={showDeleteConfirm}
        title="Delete Samples"
        onClose={() => setShowDeleteConfirm(false)}
        size="sm"
        footer={
          <>
          <button
            onClick={() => setShowDeleteConfirm(false)}
            disabled={deleting}
            className="border px-4 py-2 rounded-md text-sm"
          >
            Cancel
          </button>
          <Button
            variant="danger"
            onClick={handleBulkDelete}
            busy={deleting}
            busyLabel="Deleting..."
          >
            Delete
          </Button>
          </>
        }
      >
        <p className="text-sm text-gray-600 mb-1">
          You are about to delete <span className="font-semibold">{selectedSampleIds.size}</span> sample{selectedSampleIds.size > 1 ? "s" : ""}.
        </p>
        <p className="text-sm text-red-600 mb-4">
          This action cannot be undone. File links and pending auto-runs for these samples will be removed. Existing pipeline runs will be kept for audit purposes.
        </p>
      </Modal>
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
  );
}

/* ─── Experiment Results Tab ─── */

function ExperimentResultsTab({ experimentId }: { experimentId: number }) {
  const [qcDashboards, setQcDashboards] = useState<QCDashboardSummary[]>([]);
  const [modalDashboardId, setModalDashboardId] = useState<number | null>(null);
  const [cellxgenePubs, setCellxgenePubs] = useState<CellxgenePublicationResponse[]>([]);
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null);
  const [expandedTitle, setExpandedTitle] = useState("");
  const [expandedPlot, setExpandedPlot] = useState<PlotArchiveResponse | null>(null);

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

  const handleExpand = async (plot: PlotArchiveResponse) => {
    const isPdf = plot.file?.file_type?.toLowerCase() === "pdf";
    const url =
      isPdf && plot.thumbnail_url
        ? await plotThumbnailContentUrl(plot.id)
        : plot.file
          ? await fileContentUrl(plot.file.id)
          : "";
    setExpandedUrl(url);
    setExpandedTitle(plot.title ?? "Plot");
    setExpandedPlot(plot);
  };

  if (loading) return <p className="text-ink-subtle text-sm">Loading results...</p>;

  return (
    <div className="space-y-8">
      {/* QC Dashboards */}
      <section>
        <h2 className="text-lg font-semibold mb-3">QC Dashboards</h2>
        {qcDashboards.length === 0 ? (
          <p className="text-ink-subtle text-sm">No QC dashboards for this experiment.</p>
        ) : (
          <div className="bg-surface rounded-lg shadow divide-y divide-hairline">
            {qcDashboards.map((d) => (
              <QCDashboardListItem
                key={d.id}
                dashboard={d}
                onClick={() => setModalDashboardId(d.id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* cellxgene Publications */}
      <section>
        <h2 className="text-lg font-semibold mb-3">cellxgene Datasets</h2>
        {cellxgenePubs.length === 0 ? (
          <p className="text-ink-subtle text-sm">No published datasets for this experiment.</p>
        ) : (
          <div className="bg-surface rounded-lg shadow divide-y divide-hairline">
            {cellxgenePubs.map((pub) => (
              <div key={pub.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium text-sm">{pub.dataset_name}</p>
                  <p className="text-xs text-ink-subtle">Status: {pub.status}</p>
                </div>
                {pub.stable_url && pub.status === "running" && (
                  <a href={pub.stable_url} target="_blank" rel="noopener noreferrer" className="text-bioaf-600 text-sm hover:underline">Open</a>
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
          <p className="text-ink-subtle text-sm">No plots for this experiment.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {plots.map((plot) => {
              const deleted = plot.file?.storage_deleted === true;
              return (
                <div
                  key={plot.id}
                  className={`bg-surface rounded-lg shadow overflow-hidden transition-shadow ${deleted ? "opacity-60" : "hover:shadow-md"}`}
                >
                  <div className="aspect-square bg-elevated flex items-center justify-center relative">
                    {deleted ? (
                      <StorageDeletedPlaceholder />
                    ) : plot.file ? (
                      <PlotThumbnail plot={plot} onClick={() => handleExpand(plot)} />
                    ) : (
                      <span className="text-ink-subtle text-xs">No preview</span>
                    )}
                    {plot.file && (
                      <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 bg-black/70 text-white text-[10px] font-semibold uppercase rounded">
                        {plot.file.file_type}
                      </span>
                    )}
                  </div>
                  <div className="p-2">
                    <p
                      className={`text-[11px] leading-tight font-medium line-clamp-2 ${deleted ? "text-ink-subtle" : ""}`}
                      title={plot.title ?? undefined}
                    >
                      {plot.title}
                    </p>
                    {plot.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {plot.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="px-1.5 py-0.5 bg-elevated text-gray-600 rounded text-[10px]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {modalDashboardId !== null && (
        <QCReportModal
          dashboardId={modalDashboardId}
          onClose={() => setModalDashboardId(null)}
        />
      )}

      {expandedUrl && expandedPlot && (
        <PlotModal
          url={expandedUrl}
          title={expandedTitle}
          metadata={{
            experimentName: expandedPlot.experiment_name,
            projectName: expandedPlot.project_name,
            pipelineRunId: expandedPlot.pipeline_run_id,
            pipelineRunName: expandedPlot.pipeline_run_name,
            notebookSessionId: expandedPlot.notebook_session_id,
            notebookSessionType: expandedPlot.notebook_session_type,
            sourceType: expandedPlot.source_type,
            tags: expandedPlot.tags,
            indexedAt: expandedPlot.indexed_at,
            file: expandedPlot.file,
          }}
          onClose={() => {
            setExpandedUrl(null);
            setExpandedPlot(null);
          }}
        />
      )}
    </div>
  );
}
