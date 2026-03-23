"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ReferenceStatusBadge } from "@/components/references/ReferenceStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type {
  ReferenceDatasetDetail,
  ReferenceDataset,
  ReferenceDatasetListResponse,
  ImpactSummary,
} from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

type Tab = "files" | "impact" | "details";

export default function DataReferenceDetailPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;
  const user = getCurrentUser();
  const isAdmin = user?.role_name === "admin";
  const isCompBio = user?.role_name === "comp_bio";
  const canDeprecate = isAdmin || isCompBio;

  const [reference, setReference] = useState<ReferenceDatasetDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("files");

  const [impact, setImpact] = useState<ImpactSummary | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);

  const [showDeprecateModal, setShowDeprecateModal] = useState(false);
  const [deprecationNote, setDeprecationNote] = useState("");
  const [supersededById, setSupersededById] = useState<string>("");
  const [activeRefs, setActiveRefs] = useState<ReferenceDataset[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadReference();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, router]);

  useEffect(() => {
    if (activeTab === "impact") loadImpact();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, id]);

  async function loadReference() {
    try {
      const data = await api.get<ReferenceDatasetDetail>(`/api/references/${id}`);
      setReference(data);
    } catch {
      // handled
    } finally {
      setLoading(false);
    }
  }

  async function loadImpact() {
    setImpactLoading(true);
    try {
      const data = await api.get<ImpactSummary>(`/api/references/${id}/impact`);
      setImpact(data);
    } catch {
      // handled
    } finally {
      setImpactLoading(false);
    }
  }

  async function handleDeprecate() {
    setSubmitting(true);
    try {
      await api.post(`/api/references/${id}/deprecate`, {
        deprecation_note: deprecationNote || null,
        superseded_by_id: supersededById ? Number(supersededById) : null,
      });
      setShowDeprecateModal(false);
      setDeprecationNote("");
      setSupersededById("");
      loadReference();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to deprecate");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleApproveDeprecation() {
    try {
      await api.post(`/api/references/${id}/approve-deprecation`);
      loadReference();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to approve deprecation");
    }
  }

  async function openDeprecateModal() {
    setShowDeprecateModal(true);
    try {
      const [refsData, impactData] = await Promise.all([
        api.get<ReferenceDatasetListResponse>("/api/references?status=active"),
        api.get<ImpactSummary>(`/api/references/${id}/impact`),
      ]);
      setActiveRefs(refsData.references.filter((r) => r.id !== Number(id)));
      setImpact(impactData);
    } catch {
      // ignore
    }
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!reference) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center">
            <p className="text-gray-500">Reference dataset not found</p>
          </main>
        </div>
      </div>
    );
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "files", label: `Files (${reference.files.length})` },
    { key: "impact", label: "Impact" },
    { key: "details", label: "Details" },
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center gap-4 mb-4">
            <button onClick={() => router.push("/data/references")} className="text-gray-500 hover:text-gray-700">
              &larr; Back
            </button>
            <h1 className="text-2xl font-bold">{reference.name}</h1>
            <span className="text-sm text-gray-500">{reference.version?.startsWith("v") ? reference.version : `v${reference.version}`}</span>
            <ReferenceStatusBadge status={reference.status} size="md" />
          </div>

          <div className="flex items-center gap-3 mb-6 text-sm text-gray-500">
            <span className="capitalize">{reference.category}</span>
            <span>&middot;</span>
            <span className="capitalize">{reference.scope}</span>
            {canDeprecate && reference.status === "active" && (
              <button
                onClick={openDeprecateModal}
                className="ml-auto bg-red-50 text-red-700 border border-red-200 px-3 py-1.5 rounded-md text-sm hover:bg-red-100 transition-colors"
              >
                Deprecate
              </button>
            )}
          </div>

          {reference.status === "deprecated" && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
              <p className="text-red-800 font-medium">This reference dataset has been deprecated.</p>
              {reference.deprecation_note && (
                <p className="text-red-700 text-sm mt-1">{reference.deprecation_note}</p>
              )}
              {reference.superseded_by_id && (
                <p className="text-red-700 text-sm mt-1">
                  Superseded by{" "}
                  <button
                    onClick={() => router.push(`/data/references/${reference.superseded_by_id}`)}
                    className="underline hover:text-red-900"
                  >
                    reference #{reference.superseded_by_id}
                  </button>
                </p>
              )}
            </div>
          )}

          {reference.status === "pending_approval" && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-6 flex items-center justify-between">
              <div>
                <p className="text-yellow-800 font-medium">This reference dataset is pending deprecation approval.</p>
                {reference.deprecation_note && (
                  <p className="text-yellow-700 text-sm mt-1">{reference.deprecation_note}</p>
                )}
              </div>
              {isAdmin && (
                <button
                  onClick={handleApproveDeprecation}
                  className="bg-yellow-600 text-white px-4 py-2 rounded-md text-sm hover:bg-yellow-700 transition-colors"
                >
                  Approve Deprecation
                </button>
              )}
            </div>
          )}

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

          {activeTab === "files" && (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Filename</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">MD5 Checksum</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {reference.files.map((file) => (
                    <tr key={file.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 text-sm font-medium text-gray-900">{file.filename}</td>
                      <td className="px-6 py-4 text-sm text-gray-500">{file.file_type || "—"}</td>
                      <td className="px-6 py-4 text-sm text-gray-500">{formatBytes(file.size_bytes)}</td>
                      <td className="px-6 py-4 text-sm text-gray-400 font-mono text-xs">
                        {file.md5_checksum || "—"}
                      </td>
                    </tr>
                  ))}
                  {reference.files.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-6 py-8 text-center text-gray-400">
                        No files uploaded
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {activeTab === "impact" && (
            <div>
              {impactLoading ? (
                <div className="flex justify-center py-12">
                  <LoadingSpinner size="lg" />
                </div>
              ) : impact ? (
                <div className="space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="bg-white rounded-lg shadow p-6">
                      <p className="text-sm text-gray-500">Total Pipeline Runs</p>
                      <p className="text-3xl font-bold mt-1">{impact.total_pipeline_runs}</p>
                    </div>
                    <div className="bg-white rounded-lg shadow p-6">
                      <p className="text-sm text-gray-500">Total Experiments</p>
                      <p className="text-3xl font-bold mt-1">{impact.total_experiments}</p>
                    </div>
                  </div>

                  {impact.pipeline_runs.length > 0 && (
                    <div className="bg-white rounded-lg shadow overflow-hidden">
                      <div className="px-6 py-4 border-b">
                        <h3 className="font-semibold">Pipeline Runs Using This Reference</h3>
                      </div>
                      <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                          <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pipeline</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Version</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Experiment</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Review</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Completed</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                          {impact.pipeline_runs.map((run) => (
                            <tr
                              key={run.pipeline_run_id}
                              className="hover:bg-gray-50 cursor-pointer"
                              onClick={() => router.push(`/pipelines/runs/${run.pipeline_run_id}`)}
                            >
                              <td className="px-6 py-4 text-sm font-medium text-gray-900">{run.pipeline_name}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">{run.pipeline_version || "—"}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">{run.experiment_name || "—"}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">{run.status}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">{run.review_verdict || "—"}</td>
                              <td className="px-6 py-4 text-sm text-gray-500">
                                {run.completed_at ? new Date(run.completed_at).toLocaleDateString() : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {impact.pipeline_runs.length === 0 && (
                    <div className="bg-white rounded-lg shadow p-12 text-center">
                      <p className="text-gray-400">No pipeline runs are using this reference dataset.</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="bg-white rounded-lg shadow p-12 text-center">
                  <p className="text-gray-400">Unable to load impact data.</p>
                </div>
              )}
            </div>
          )}

          {activeTab === "details" && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Reference Details</h2>
              <dl className="space-y-3">
                <div>
                  <dt className="text-sm text-gray-500">Uploaded By</dt>
                  <dd className="text-sm">
                    {reference.uploaded_by
                      ? reference.uploaded_by.name || reference.uploaded_by.email
                      : "—"}
                  </dd>
                </div>
                {reference.approved_by && (
                  <div>
                    <dt className="text-sm text-gray-500">Approved By</dt>
                    <dd className="text-sm">
                      {reference.approved_by.name || reference.approved_by.email}
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-sm text-gray-500">Source URL</dt>
                  <dd className="text-sm">
                    {reference.source_url ? (
                      <a
                        href={reference.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-bioaf-600 hover:underline"
                      >
                        {reference.source_url}
                      </a>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-gray-500">GCS Prefix</dt>
                  <dd className="text-sm font-mono text-xs text-gray-600">{reference.gcs_prefix}</dd>
                </div>
                <div>
                  <dt className="text-sm text-gray-500">Total Size</dt>
                  <dd className="text-sm">{formatBytes(reference.total_size_bytes)}</dd>
                </div>
                <div>
                  <dt className="text-sm text-gray-500">File Count</dt>
                  <dd className="text-sm">{reference.file_count ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-sm text-gray-500">Created</dt>
                  <dd className="text-sm">{new Date(reference.created_at).toLocaleString()}</dd>
                </div>
              </dl>
            </div>
          )}

          {/* Deprecation Modal */}
          {showDeprecateModal && (
            <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
                <h2 className="text-lg font-semibold mb-4">Deprecate Reference Dataset</h2>
                <p className="text-sm text-gray-500 mb-4">
                  This will mark &quot;{reference.name}&quot; as pending deprecation approval.
                </p>
                {impact && impact.total_pipeline_runs > 0 && (
                  <div className="bg-amber-50 border border-amber-200 rounded-md p-3 mb-4">
                    <p className="text-sm text-amber-800 font-medium">
                      {impact.total_pipeline_runs} pipeline run{impact.total_pipeline_runs !== 1 ? "s" : ""} across {impact.total_experiments} experiment{impact.total_experiments !== 1 ? "s" : ""} use this reference and will be impacted.
                    </p>
                  </div>
                )}
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Deprecation Note
                    </label>
                    <textarea
                      value={deprecationNote}
                      onChange={(e) => setDeprecationNote(e.target.value)}
                      rows={3}
                      className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                      placeholder="Reason for deprecation..."
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Superseded By (optional)
                    </label>
                    <select
                      value={supersededById}
                      onChange={(e) => setSupersededById(e.target.value)}
                      className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                    >
                      <option value="">None</option>
                      {activeRefs.map((r) => (
                        <option key={r.id} value={String(r.id)}>
                          {r.name} v{r.version}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="flex justify-end gap-3 mt-6">
                  <button
                    onClick={() => {
                      setShowDeprecateModal(false);
                      setDeprecationNote("");
                      setSupersededById("");
                    }}
                    className="border border-gray-300 px-4 py-2 rounded-md text-sm hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleDeprecate}
                    disabled={submitting}
                    className="bg-red-600 text-white px-4 py-2 rounded-md text-sm hover:bg-red-700 disabled:opacity-50"
                  >
                    {submitting ? "Submitting..." : "Deprecate"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
