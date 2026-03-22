"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { ReviewPanel } from "@/components/experiments/ReviewPanel";
import { ReferenceStatusBadge } from "@/components/references/ReferenceStatusBadge";
import { isAuthenticated } from "@/lib/auth";
import { getToken } from "@/lib/auth";
import { api } from "@/lib/api";
import type { PipelineRunDetail, PipelineRunStatus, PipelineProcessStatus, ReferenceDataset } from "@/lib/types";

const STATUS_COLORS: Record<PipelineRunStatus | PipelineProcessStatus, string> = {
  pending: "bg-gray-100 text-gray-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-orange-100 text-orange-700",
  cached: "bg-purple-100 text-purple-700",
};

type Tab = "logs" | "report" | "parameters" | "provenance" | "review";

function getUserRole(): string {
  try {
    const token = getToken();
    if (!token) return "viewer";
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.role || "viewer";
  } catch {
    return "viewer";
  }
}

export default function PipelineRunDetailPage() {
  const router = useRouter();
  const params = useParams();
  const runId = params.id as string;

  const [run, setRun] = useState<PipelineRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("logs");
  const [report, setReport] = useState<string>("");
  const [reportLoading, setReportLoading] = useState(false);
  const [logs, setLogs] = useState<{ stdout: string; stderr: string }>({ stdout: "", stderr: "" });
  const [logsLoading, setLogsLoading] = useState(false);
  const [selectedProcess, setSelectedProcess] = useState<string>("");
  const [provenance, setProvenance] = useState<Record<string, unknown> | null>(null);
  const [references, setReferences] = useState<ReferenceDataset[]>([]);

  const loadRun = useCallback(async () => {
    try {
      const data = await api.get<PipelineRunDetail>(`/api/pipeline-runs/${runId}`);
      setRun(data);
    } catch {} finally { setLoading(false); }
  }, [runId]);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadRun();
    loadReferences();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, loadRun]);

  // Auto-refresh while active
  useEffect(() => {
    if (!run || !["running", "pending"].includes(run.status)) return;
    const interval = setInterval(loadRun, 10000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status, loadRun]);

  async function handleCancel() {
    if (!confirm("Cancel this pipeline run?")) return;
    try {
      await api.post(`/api/pipeline-runs/${runId}/cancel`);
      loadRun();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Cancel failed");
    }
  }

  async function handleReproduce() {
    try {
      const newRun = await api.post<{ id: number }>(`/api/pipeline-runs/${runId}/reproduce`);
      router.push(`/pipelines/runs/${newRun.id}`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Reproduce failed");
    }
  }

  async function loadReport() {
    setReportLoading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/pipeline-runs/${runId}/report`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("bioaf_token")}` },
      });
      setReport(await res.text());
    } catch {} finally { setReportLoading(false); }
  }

  async function loadLogs(processName?: string) {
    setLogsLoading(true);
    try {
      const url = processName
        ? `/api/pipeline-runs/${runId}/logs/${encodeURIComponent(processName)}`
        : `/api/pipeline-runs/${runId}/logs`;
      const data = await api.get<{ stdout: string; stderr: string }>(url);
      setLogs(data);
    } catch {} finally { setLogsLoading(false); }
  }

  async function loadProvenance() {
    try {
      const data = await api.get<Record<string, unknown>>(`/api/pipeline-runs/${runId}/provenance`);
      setProvenance(data);
    } catch {}
  }

  async function loadReferences() {
    try {
      const data = await api.get<ReferenceDataset[]>(`/api/pipeline-runs/${runId}/references`);
      setReferences(data);
    } catch {}
  }

  useEffect(() => {
    if (activeTab === "report" && !["running", "pending"].includes(run?.status ?? "")) loadReport();
    if (activeTab === "provenance") loadProvenance();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, runId, run?.status]);

  useEffect(() => {
    if (activeTab !== "logs") return;
    if (run?.k8s_job_name && !run.processes?.length) {
      loadLogs();
    } else if (selectedProcess) {
      loadLogs(selectedProcess);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProcess, activeTab, run?.k8s_job_name]);

  if (!loading && !run) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center"><p className="text-gray-500">Run not found</p></main>
        </div>
      </div>
    );
  }

  function formatDateTime(dateStr: string | null | undefined): string {
    if (!dateStr) return "—";
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) + " " +
      d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
  }

  function formatDuration(startedAt: string | null | undefined, completedAt: string | null | undefined): string {
    if (!startedAt) return "—";
    const start = new Date(startedAt).getTime();
    const end = completedAt ? new Date(completedAt).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  const isActive = ["running", "pending"].includes(run?.status ?? "");
  const tabs: { key: Tab; label: string }[] = [
    { key: "logs", label: "Logs" },
    { key: "report", label: "Report" },
    { key: "parameters", label: "Parameters" },
    { key: "provenance", label: "Provenance" },
    { key: "review", label: "Review" },
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : run && (
          <>
          <div className="flex items-center gap-4 mb-6">
            <button onClick={() => router.push("/pipelines/runs")} className="text-gray-500 hover:text-gray-700">← Back</button>
            <h1 className="text-2xl font-bold">Run #{run.id} — {run.pipeline_name}</h1>
            <span className={`px-2 py-0.5 text-xs rounded-full ${STATUS_COLORS[run.status]}`}>{run.status}</span>
            {isActive && (
              <button onClick={handleCancel} className="ml-auto bg-red-600 text-white px-4 py-1.5 rounded text-sm hover:bg-red-700">Cancel</button>
            )}
            {!isActive && (
              <button onClick={handleReproduce} className="ml-auto bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700">Reproduce</button>
            )}
          </div>

          {/* Timing metadata */}
          <div className="bg-white rounded-lg shadow p-4 mb-6 flex gap-6">
            <div><span className="text-xs text-gray-500">Started</span><p className="text-sm font-medium">{formatDateTime(run.started_at)}</p></div>
            {run.completed_at && (
              <div><span className="text-xs text-gray-500">Completed</span><p className="text-sm font-medium">{formatDateTime(run.completed_at)}</p></div>
            )}
            <div><span className="text-xs text-gray-500">Duration</span><p className="text-sm font-medium">{formatDuration(run.started_at, run.completed_at)}</p></div>
          </div>

          {/* Overall progress */}
          {run.progress && (
            <div className="bg-white rounded-lg shadow p-4 mb-6">
              <div className="flex items-center gap-4">
                <div className="flex-1">
                  <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full bg-bioaf-500 rounded-full transition-all" style={{ width: `${run.progress.percent_complete}%` }} />
                  </div>
                </div>
                <span className="text-sm font-medium">{Math.round(run.progress.percent_complete)}%</span>
                <span className="text-xs text-gray-500">
                  {run.progress.completed + run.progress.cached}/{run.progress.total_processes} processes
                </span>
              </div>
              {run.error_message && <p className="text-sm text-red-600 mt-2">{run.error_message}</p>}
            </div>
          )}

          {/* MINSEQE metadata */}
          {(run.reference_genome || run.alignment_algorithm) && (
            <div className="bg-white rounded-lg shadow p-4 mb-6 flex gap-6">
              {run.reference_genome && (
                <div><span className="text-xs text-gray-500">Reference Genome</span><p className="text-sm font-medium">{run.reference_genome}</p></div>
              )}
              {run.alignment_algorithm && (
                <div><span className="text-xs text-gray-500">Alignment Algorithm</span><p className="text-sm font-medium">{run.alignment_algorithm}</p></div>
              )}
            </div>
          )}

          {/* K8s metadata */}
          {run.k8s_job_name && (
            <div className="bg-white rounded-lg shadow p-4 mb-6 flex gap-6" data-testid="k8s-metadata">
              <div><span className="text-xs text-gray-500">K8s Job</span><p className="text-sm font-mono">{run.k8s_job_name}</p></div>
              {run.k8s_pod_name && (
                <div><span className="text-xs text-gray-500">Pod</span><p className="text-sm font-mono">{run.k8s_pod_name}</p></div>
              )}
              {run.k8s_namespace && (
                <div><span className="text-xs text-gray-500">Namespace</span><p className="text-sm font-mono">{run.k8s_namespace}</p></div>
              )}
            </div>
          )}

          {/* Tabs */}
          <div className="border-b border-gray-200 mb-6">
            <nav className="flex -mb-px space-x-8">
              {tabs.map((tab) => (
                <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                  className={`py-2 px-1 border-b-2 text-sm font-medium ${activeTab === tab.key ? "border-bioaf-500 text-bioaf-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
                >{tab.label}</button>
              ))}
            </nav>
          </div>

          {/* Parameters tab */}
          {activeTab === "parameters" && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Parameters</h2>
              {run.parameters ? (
                <pre className="text-sm bg-gray-50 p-4 rounded overflow-auto max-h-96">{JSON.stringify(run.parameters, null, 2)}</pre>
              ) : <p className="text-gray-400">No parameters recorded</p>}
            </div>
          )}

          {/* Provenance tab */}
          {activeTab === "provenance" && (
            <div className="space-y-6">
              <div className="bg-white rounded-lg shadow p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold">Provenance</h2>
                  <div className="flex gap-2">
                    <a href={`/api/pipeline-runs/${runId}/provenance?format=json`} target="_blank" className="border px-3 py-1.5 rounded text-sm hover:bg-gray-50">Export JSON</a>
                    <a href={`/api/pipeline-runs/${runId}/provenance?format=yaml`} target="_blank" className="border px-3 py-1.5 rounded text-sm hover:bg-gray-50">Export YAML</a>
                  </div>
                </div>
                {provenance ? (
                  <pre className="text-sm bg-gray-50 p-4 rounded overflow-auto max-h-96">
                    {JSON.stringify(
                      references.length > 0
                        ? {
                            ...provenance,
                            reference_datasets: references.map((ref) => ({
                              name: ref.name,
                              version: ref.version,
                              status: ref.status,
                              ...(ref.status === "deprecated"
                                ? {
                                    warning: "This reference dataset has been deprecated.",
                                    ...(ref.deprecation_note ? { deprecation_note: ref.deprecation_note } : {}),
                                    ...(ref.superseded_by_id ? { superseded_by_id: ref.superseded_by_id } : {}),
                                  }
                                : {}),
                            })),
                          }
                        : provenance,
                      null,
                      2,
                    )}
                  </pre>
                ) : <LoadingSpinner size="sm" />}
              </div>

              {/* Reference datasets in provenance view */}
              {references.length > 0 && (
                <div className="bg-white rounded-lg shadow p-6">
                  <h3 className="text-md font-semibold mb-3">Reference Datasets</h3>
                  <div className="space-y-2">
                    {references.map((ref) => (
                      <div key={ref.id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-md">
                        <div className="flex-1">
                          <span className="font-medium text-sm">{ref.name}</span>
                          <span className="text-gray-500 text-sm ml-2">v{ref.version}</span>
                        </div>
                        <ReferenceStatusBadge status={ref.status} />
                        {ref.status === "deprecated" && (
                          <span className="text-amber-600 text-xs">
                            Deprecated{ref.deprecation_note ? `: ${ref.deprecation_note}` : ""}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Report tab */}
          {activeTab === "report" && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Nextflow Report</h2>
              {report ? (
                <iframe srcDoc={report} className="w-full h-[600px] border rounded" title="Nextflow Report" />
              ) : isActive ? (
                <p className="text-gray-400">Reports are available after the pipeline run completes.</p>
              ) : reportLoading ? (
                <div className="flex items-center gap-2 text-gray-400"><LoadingSpinner size="sm" /><span>Loading report...</span></div>
              ) : (
                <p className="text-gray-400">No report available.</p>
              )}
            </div>
          )}

          {/* Logs tab */}
          {activeTab === "logs" && (
            <div className="bg-white rounded-lg shadow p-6">
              <div className="flex items-center gap-4 mb-4">
                <h2 className="text-lg font-semibold">Logs</h2>
                {run.processes.length > 0 && (
                  <select value={selectedProcess} onChange={(e) => setSelectedProcess(e.target.value)} className="border rounded px-3 py-1.5 text-sm">
                    <option value="">Select process...</option>
                    {run.processes.map((p) => <option key={p.id} value={p.process_name}>{p.process_name}</option>)}
                  </select>
                )}
              </div>
              {(selectedProcess || (run.k8s_job_name && !run.processes.length)) ? (
                logsLoading ? (
                  <div className="flex items-center gap-2 text-gray-400"><LoadingSpinner size="sm" /><span>Loading logs...</span></div>
                ) : (
                  <div className="space-y-4">
                    <div>
                      <h3 className="text-sm font-medium mb-1">stdout</h3>
                      <pre className="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96 whitespace-pre-wrap">{logs.stdout || "(empty)"}</pre>
                    </div>
                    {logs.stderr && (
                      <div>
                        <h3 className="text-sm font-medium mb-1">stderr</h3>
                        <pre className="text-xs bg-gray-900 text-red-400 p-4 rounded overflow-auto max-h-64 whitespace-pre-wrap">{logs.stderr || "(empty)"}</pre>
                      </div>
                    )}
                  </div>
                )
              ) : <p className="text-gray-400">Select a process to view logs</p>}
            </div>
          )}

          {/* Review tab */}
          {activeTab === "review" && (
            <ReviewPanel pipelineRunId={run.id} userRole={getUserRole()} onReviewSubmitted={loadRun} />
          )}

          {/* References Used section — shown below active tab content */}
          {references.length > 0 && (
            <div className="bg-white rounded-lg shadow mt-6">
              <div className="p-6 border-b">
                <h2 className="text-lg font-semibold">References Used</h2>
              </div>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Version</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Notes</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {references.map((ref) => (
                    <tr key={ref.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm font-medium">{ref.name}</td>
                      <td className="px-4 py-3 text-sm">{ref.version}</td>
                      <td className="px-4 py-3">
                        <ReferenceStatusBadge status={ref.status} />
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">
                        {ref.status === "deprecated" && (
                          <span className="text-amber-600">
                            This reference dataset has been deprecated.
                            {ref.deprecation_note ? ` ${ref.deprecation_note}` : ""}
                            {ref.superseded_by_id ? ` Superseded by reference #${ref.superseded_by_id}.` : ""}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}
