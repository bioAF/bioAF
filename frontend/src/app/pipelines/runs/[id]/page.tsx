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
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import type {
  CustomPipelineRunOverview,
  PipelineRunDetail,
  PipelineRunStatus,
  PipelineProcessStatus,
  ReferenceDataset,
} from "@/lib/types";

const STATUS_COLORS: Record<PipelineRunStatus | PipelineProcessStatus, string> = {
  pending: "bg-gray-100 text-gray-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-orange-100 text-orange-700",
  cached: "bg-purple-100 text-purple-700",
};

type Tab = "logs" | "report" | "parameters" | "provenance" | "review";

interface LogResponse {
  stdout: string;
  stderr: string;
  log_source?: "pod" | "custom_file";
  custom_log_pending?: boolean;
  pod_logs_available?: boolean;
  custom_log_missing?: boolean;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineFormat(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1 rounded">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" class="text-bioaf-600 hover:underline" target="_blank" rel="noreferrer">$1</a>',
    );
}

// Minimal markdown -> HTML renderer for custom pipeline `report.md` artifacts.
// Handles headings, paragraphs, fenced code blocks, unordered lists, links,
// inline code and bold/italic. Anything fancier should be a real renderer.
function renderMarkdown(md: string): string {
  const lines = md.split("\n");
  let html = "";
  let inCode = false;
  let codeBuf: string[] = [];
  let inList = false;
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      html += `<p class="my-2">${inlineFormat(escapeHtml(paragraph.join(" ")))}</p>\n`;
      paragraph = [];
    }
  };
  const flushList = () => {
    if (inList) {
      html += "</ul>\n";
      inList = false;
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html += `<pre class="bg-gray-50 border rounded p-3 text-xs font-mono overflow-x-auto my-2">${escapeHtml(codeBuf.join("\n"))}</pre>\n`;
        codeBuf = [];
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length;
      const sizes = ["text-2xl", "text-xl", "text-lg", "text-base", "text-sm", "text-xs"];
      html += `<h${level} class="font-semibold mt-4 mb-2 ${sizes[level - 1]}">${inlineFormat(escapeHtml(heading[2]))}</h${level}>\n`;
      continue;
    }

    const list = line.match(/^[-*]\s+(.+)$/);
    if (list) {
      flushParagraph();
      if (!inList) {
        html += '<ul class="list-disc pl-6 my-2">';
        inList = true;
      }
      html += `<li>${inlineFormat(escapeHtml(list[1]))}</li>`;
      continue;
    }

    if (line.trim() === "") {
      flushParagraph();
      flushList();
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  if (inCode && codeBuf.length > 0) {
    html += `<pre class="bg-gray-50 border rounded p-3 text-xs font-mono overflow-x-auto my-2">${escapeHtml(codeBuf.join("\n"))}</pre>\n`;
  }

  return html;
}

function getUserRole(): string {
  try {
    const token = getToken();
    if (!token) return "viewer";
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.role_name || "viewer";
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
  const [logs, setLogs] = useState<LogResponse>({ stdout: "", stderr: "" });
  const [logsLoading, setLogsLoading] = useState(false);
  const [selectedProcess, setSelectedProcess] = useState<string>("");
  const [provenance, setProvenance] = useState<Record<string, unknown> | null>(null);
  const [references, setReferences] = useState<ReferenceDataset[]>([]);
  const [customOverview, setCustomOverview] = useState<CustomPipelineRunOverview | null>(null);
  const [showSystemLogs, setShowSystemLogs] = useState(false);
  const [systemLogs, setSystemLogs] = useState<LogResponse | null>(null);
  const [systemLogsLoading, setSystemLogsLoading] = useState(false);

  const loadRun = useCallback(async () => {
    try {
      const data = await api.get<PipelineRunDetail>(`/api/pipeline-runs/${runId}`);
      setRun(data);
    } catch {} finally { setLoading(false); }
  }, [runId]);

  const isCustomRun = run?.custom_pipeline_version_id != null;

  // Load the custom pipeline overview once for custom runs.
  useEffect(() => {
    if (run?.custom_pipeline_version_id == null) {
      setCustomOverview(null);
      return;
    }
    void (async () => {
      try {
        const data = await api.get<CustomPipelineRunOverview>(
          `/api/v1/custom-pipelines/versions/${run.custom_pipeline_version_id}/overview`,
        );
        setCustomOverview(data);
      } catch {
        setCustomOverview(null);
      }
    })();
  }, [run?.custom_pipeline_version_id]);

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
      const data = await api.get<LogResponse>(url);
      setLogs(data);
    } catch {} finally { setLogsLoading(false); }
  }

  async function loadSystemLogs() {
    setSystemLogsLoading(true);
    try {
      const data = await api.get<LogResponse>(`/api/pipeline-runs/${runId}/logs?source=pod`);
      setSystemLogs(data);
    } catch {
      setSystemLogs(null);
    } finally {
      setSystemLogsLoading(false);
    }
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
    if (run?.k8s_job_name) {
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
  const customReportPath =
    isCustomRun
      ? typeof (run?.output_files as Record<string, unknown> | null)?.report_path === "string"
        ? ((run!.output_files as Record<string, unknown>).report_path as string)
        : null
      : null;
  const customReportFormat =
    isCustomRun
      ? typeof (run?.output_files as Record<string, unknown> | null)?.report_format === "string"
        ? ((run!.output_files as Record<string, unknown>).report_format as string)
        : null
      : null;
  const showReportTab = isCustomRun ? customReportPath != null : true;

  const tabs: { key: Tab; label: string }[] = [
    { key: "logs", label: "Logs" },
    ...(showReportTab ? [{ key: "report" as Tab, label: "Report" }] : []),
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
                {!isCustomRun && (
                  <span className="text-xs text-gray-500">
                    {run.progress.completed + run.progress.cached}/{run.progress.total_processes} processes
                  </span>
                )}
              </div>
              {run.failure_reason === "oom" && (
                <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg flex items-start gap-3">
                  <span className="text-amber-600 text-lg" title="Memory">&#x1F4BE;</span>
                  <div className="flex-1">
                    <p className="text-sm text-amber-800 font-medium">This pipeline ran out of memory.</p>
                    <p className="text-sm text-amber-700 mt-1">The current pipeline node size does not have enough RAM for this workload.</p>
                    <button
                      onClick={() => router.push("/infrastructure/components")}
                      className="mt-2 px-3 py-1 text-sm bg-amber-600 text-white rounded hover:bg-amber-700"
                    >
                      Update node size
                    </button>
                  </div>
                </div>
              )}
              {run.failure_reason === "preemption_exhausted" && (
                <div className="mt-3 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                  <p className="text-sm text-blue-800 font-medium">This pipeline was interrupted multiple times by Spot instance reclamation.</p>
                  <p className="text-sm text-blue-700 mt-1">This is unusual and typically resolves on its own.</p>
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={handleReproduce}
                      className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
                    >
                      Re-run pipeline
                    </button>
                    <button
                      onClick={() => router.push("/infrastructure/components")}
                      className="px-3 py-1 text-sm border border-blue-300 text-blue-700 rounded hover:bg-blue-50"
                    >
                      Disable Spot instances
                    </button>
                  </div>
                </div>
              )}
              {run.error_message && run.failure_reason !== "oom" && run.failure_reason !== "preemption_exhausted" && (
                <p className="text-sm text-red-600 mt-2">{run.error_message}</p>
              )}
            </div>
          )}

          {/* MINSEQE metadata (NF-Core only) */}
          {!isCustomRun && (run.reference_genome || run.alignment_algorithm) && (
            <div className="bg-white rounded-lg shadow p-4 mb-6 flex gap-6">
              {run.reference_genome && (
                <div><span className="text-xs text-gray-500">Reference Genome</span><p className="text-sm font-medium">{run.reference_genome}</p></div>
              )}
              {run.alignment_algorithm && (
                <div><span className="text-xs text-gray-500">Alignment Algorithm</span><p className="text-sm font-medium">{run.alignment_algorithm}</p></div>
              )}
            </div>
          )}

          {/* Custom pipeline overview */}
          {isCustomRun && customOverview && (
            <div className="bg-white rounded-lg shadow p-4 mb-6 space-y-3">
              <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                <div>
                  <span className="text-xs text-gray-500 block">Pipeline</span>
                  <a
                    href={`/pipelines/custom/${customOverview.pipeline_id}`}
                    className="text-bioaf-600 hover:underline font-medium"
                  >
                    {customOverview.pipeline_name}
                  </a>
                </div>
                <div>
                  <span className="text-xs text-gray-500 block">Version</span>
                  <span className="font-mono">v{customOverview.version_number}</span>
                </div>
                <div>
                  <span className="text-xs text-gray-500 block">Code source</span>
                  <span>
                    {customOverview.code_source_type === "github_repo"
                      ? "GitHub repo"
                      : customOverview.code_source_type === "code_blob"
                        ? "Code blob"
                        : "Inline command"}
                  </span>
                </div>
                <div>
                  <span className="text-xs text-gray-500 block">Entrypoint</span>
                  <code className="font-mono text-xs bg-gray-100 px-2 py-0.5 rounded">
                    {customOverview.entrypoint_command}
                  </code>
                </div>
                <div>
                  <span className="text-xs text-gray-500 block">Environment</span>
                  {customOverview.environment ? (
                    <span>
                      {customOverview.environment.environment_name} v
                      {customOverview.environment.version_number}.
                      {customOverview.environment.build_number}
                    </span>
                  ) : (
                    <span className="text-gray-500">—</span>
                  )}
                </div>
                <div>
                  <span className="text-xs text-gray-500 block">Resources</span>
                  <span className="font-mono">
                    CPU {customOverview.cpu_request} / Memory {customOverview.memory_request}
                  </span>
                </div>
              </div>
              {run.parameters && Object.keys(run.parameters).length > 0 && (
                <div>
                  <span className="text-xs text-gray-500 block mb-1">Variables used</span>
                  <table className="text-xs border w-full">
                    <thead className="bg-gray-50 text-gray-500 uppercase">
                      <tr>
                        <th className="px-2 py-1 text-left">Name</th>
                        <th className="px-2 py-1 text-left">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(run.parameters).map(([key, value]) => (
                        <tr key={key} className="border-t">
                          <td className="px-2 py-1 font-mono">{key}</td>
                          <td className="px-2 py-1 font-mono">
                            {typeof value === "string" ? value : JSON.stringify(value)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
                  <ProvenanceExportMenu entityType="pipeline-runs" entityId={Number(runId)} />
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
          {activeTab === "report" && showReportTab && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">
                {isCustomRun ? "Pipeline Report" : "Nextflow Report"}
              </h2>
              {report ? (
                isCustomRun && customReportFormat === "md" ? (
                  <div
                    className="prose prose-sm max-w-none"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(report) }}
                  />
                ) : (
                  <iframe
                    srcDoc={report}
                    className="w-full h-[600px] border rounded"
                    title={isCustomRun ? "Pipeline Report" : "Nextflow Report"}
                  />
                )
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
                {!run.k8s_job_name && run.processes.length > 0 && (
                  <select value={selectedProcess} onChange={(e) => setSelectedProcess(e.target.value)} className="border rounded px-3 py-1.5 text-sm">
                    <option value="">Select process...</option>
                    {run.processes.map((p) => <option key={p.id} value={p.process_name}>{p.process_name}</option>)}
                  </select>
                )}
                {isCustomRun && logs.pod_logs_available && (
                  <button
                    onClick={() => {
                      const next = !showSystemLogs;
                      setShowSystemLogs(next);
                      if (next && systemLogs == null) {
                        void loadSystemLogs();
                      }
                    }}
                    className={`ml-auto text-xs px-3 py-1 rounded border ${
                      showSystemLogs
                        ? "bg-bioaf-600 text-white border-bioaf-600"
                        : "border-gray-300 text-gray-700 hover:bg-gray-50"
                    }`}
                  >
                    {showSystemLogs ? "Hide System Logs" : "Show System Logs"}
                  </button>
                )}
              </div>

              {isCustomRun && logs.custom_log_pending && customOverview?.log_file_path && (
                <div className="mb-3 p-3 bg-blue-50 border border-blue-200 rounded text-sm text-blue-800">
                  Custom log file ({customOverview.log_file_path}) will be available after
                  completion. Showing terminal output.
                </div>
              )}
              {isCustomRun && logs.custom_log_missing && (
                <div className="mb-3 p-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
                  Custom log file not available. Showing terminal output.
                </div>
              )}

              {(run.k8s_job_name || selectedProcess) ? (
                logsLoading ? (
                  <div className="flex items-center gap-2 text-gray-400"><LoadingSpinner size="sm" /><span>Loading logs...</span></div>
                ) : (
                  <div className="space-y-4">
                    <div>
                      <pre className="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96 whitespace-pre-wrap">{logs.stdout || "(empty)"}</pre>
                    </div>
                    {logs.stderr && (
                      <div>
                        <h3 className="text-sm font-medium mb-1">stderr</h3>
                        <pre className="text-xs bg-gray-900 text-red-400 p-4 rounded overflow-auto max-h-64 whitespace-pre-wrap">{logs.stderr || "(empty)"}</pre>
                      </div>
                    )}

                    {isCustomRun && showSystemLogs && (
                      <div className="border-t pt-4">
                        <h3 className="text-sm font-medium mb-2">System Logs (pod stdout/stderr)</h3>
                        {systemLogsLoading ? (
                          <div className="flex items-center gap-2 text-gray-400">
                            <LoadingSpinner size="sm" /><span>Loading system logs...</span>
                          </div>
                        ) : (
                          <pre className="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96 whitespace-pre-wrap">
                            {systemLogs?.stdout || "(empty)"}
                          </pre>
                        )}
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
