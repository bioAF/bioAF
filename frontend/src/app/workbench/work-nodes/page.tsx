"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";
import type {
  WorkNode,
  WorkNodeListResponse,
  WorkNodeLaunchRequest,
  MachineType,
  Project,
  EnvironmentResponse,
  EnvironmentListResponse,
  EnvironmentDetailResponse,
  GitHubRepo,
  GitHubRepoListResponse,
  FileResponse,
  FileListResponse,
} from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-800",
  starting: "bg-blue-100 text-blue-800",
  running: "bg-green-100 text-green-800",
  stopping: "bg-orange-100 text-orange-800",
  stopped: "bg-gray-100 text-gray-600",
  failed: "bg-red-100 text-red-800",
};

function statusLabel(node: WorkNode): string {
  if (node.status === "failed" && !node.access_url) return "Resource Failure";
  if (node.status === "stopping") return "Syncing outputs...";
  return node.status;
}

const CATEGORY_LABELS: Record<string, string> = {
  standard: "Standard",
  "high-memory": "High Memory",
  gpu: "GPU",
};

export default function WorkNodesPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();

  const [nodes, setNodes] = useState<WorkNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [showLaunch, setShowLaunch] = useState(false);
  const [viewingNode, setViewingNode] = useState<WorkNode | null>(null);

  // GitHub repos state
  const [repos, setRepos] = useState<GitHubRepo[]>([]);
  const [showRepos, setShowRepos] = useState(false);
  const [newRepoUrl, setNewRepoUrl] = useState("");
  const [newRepoName, setNewRepoName] = useState("");
  const [repoError, setRepoError] = useState<string | null>(null);
  const [addingRepo, setAddingRepo] = useState(false);

  // Launch form state
  const [launchStep, setLaunchStep] = useState(1);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [projectExperiments, setProjectExperiments] = useState<{ id: number; name: string; code: string | null }[]>([]);
  const [selectedExperimentId, setSelectedExperimentId] = useState<number | null>(null);
  const [experimentFiles, setExperimentFiles] = useState<FileResponse[]>([]);
  const [sampleNames, setSampleNames] = useState<Record<number, string>>({});
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [showFileSelector, setShowFileSelector] = useState(false);
  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [selectedEnvId, setSelectedEnvId] = useState<number | null>(null);
  const [envDetail, setEnvDetail] = useState<EnvironmentDetailResponse | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [selectedRepoIds, setSelectedRepoIds] = useState<number[]>([]);
  const [machineTypes, setMachineTypes] = useState<MachineType[]>([]);
  const [selectedMachineType, setSelectedMachineType] = useState<string>("");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [stoppingNodes, setStoppingNodes] = useState<Set<number>>(new Set());
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("work_nodes", "view")) { router.push("/dashboard"); return; }
    loadNodes();
    loadRepos();
  }, [router, permLoading, canAccess]);

  // Auto-refresh while starting
  useEffect(() => {
    const hasInProgress = nodes.some((n) => n.status === "starting" || n.status === "stopping");
    if (!hasInProgress) return;
    const interval = setInterval(() => loadNodes(), 10000);
    return () => clearInterval(interval);
  }, [nodes]);

  const loadNodes = useCallback(async () => {
    try {
      const data = await api.get<WorkNodeListResponse>("/api/v1/work-nodes/sessions");
      setNodes(data.sessions);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  async function loadRepos() {
    try {
      const data = await api.get<GitHubRepoListResponse>("/api/v1/github-repos");
      setRepos(data.repos);
    } catch {}
  }

  async function handleAddRepo() {
    if (!newRepoUrl.trim()) return;
    setAddingRepo(true);
    setRepoError(null);
    try {
      await api.post("/api/v1/github-repos", {
        git_ssh_url: newRepoUrl.trim(),
        display_name: newRepoName.trim() || null,
      });
      setNewRepoUrl("");
      setNewRepoName("");
      loadRepos();
    } catch (err) {
      setRepoError(err instanceof ApiError ? err.message : "Failed to add repo");
    } finally {
      setAddingRepo(false);
    }
  }

  async function handleDeleteRepo(repoId: number) {
    try {
      await api.delete(`/api/v1/github-repos/${repoId}`);
      loadRepos();
    } catch {}
  }

  async function openLaunchDialog() {
    setShowLaunch(true);
    setLaunchStep(1);
    setSelectedProjectId(null);
    setProjectExperiments([]);
    setSelectedExperimentId(null);
    setExperimentFiles([]);
    setSampleNames({});
    setSelectedFileIds([]);
    setShowFileSelector(false);
    setSelectedEnvId(null);
    setEnvDetail(null);
    setSelectedVersionId(null);
    setSelectedRepoIds([]);
    setSelectedMachineType("");
    setLaunchError(null);

    try {
      const [projectData, mtData, envData] = await Promise.all([
        api.get<{ projects: Project[]; total: number }>("/api/projects?page_size=100"),
        api.get<MachineType[]>("/api/v1/work-nodes/machine-types"),
        api.get<EnvironmentListResponse>("/api/v1/environments?type=work_node"),
      ]);
      setProjects(projectData.projects);
      setMachineTypes(mtData);
      setEnvironments(envData.environments);
    } catch {}
  }

  async function handleProjectSelect(projectId: number) {
    setSelectedProjectId(projectId);
    setSelectedExperimentId(null);
    setExperimentFiles([]);
    setSelectedFileIds([]);
    setShowFileSelector(false);
    try {
      const data = await api.get<{ experiments: { id: number; name: string; code: string | null }[]; total: number }>(
        `/api/experiments?project_id=${projectId}&page_size=100`
      );
      setProjectExperiments(data.experiments);
    } catch {
      setProjectExperiments([]);
    }
    setLaunchStep(2);
  }

  async function handleExperimentSelect(experimentId: number) {
    setSelectedExperimentId(experimentId);
    setSelectedFileIds([]);
    setShowFileSelector(false);
    try {
      const data = await api.get<FileListResponse>(
        `/api/experiments/${experimentId}/files?page_size=500`
      );
      setExperimentFiles(data.files);

      // Resolve sample names
      const sampleIds = new Set<number>();
      for (const file of data.files) {
        for (const sid of file.sample_ids || []) {
          sampleIds.add(sid);
        }
      }
      if (sampleIds.size > 0) {
        try {
          const samplesData = await api.get<{ samples: { id: number; sample_id_unique: string }[] }>(
            `/api/experiments/${experimentId}/samples?page_size=500`
          );
          const names: Record<number, string> = {};
          for (const s of samplesData.samples) {
            names[s.id] = s.sample_id_unique || `Sample ${s.id}`;
          }
          setSampleNames(names);
        } catch {
          setSampleNames({});
        }
      } else {
        setSampleNames({});
      }
    } catch {
      setExperimentFiles([]);
    }
  }

  async function handleEnvSelect(envId: number) {
    setSelectedEnvId(envId);
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${envId}`);
      setEnvDetail(detail);
      const readyVersion = detail.versions.find((v) => v.status === "ready" && v.image_uri);
      if (readyVersion) setSelectedVersionId(readyVersion.id);
    } catch {}
  }

  function toggleRepo(repoId: number) {
    setSelectedRepoIds((prev) =>
      prev.includes(repoId) ? prev.filter((id) => id !== repoId) : [...prev, repoId]
    );
  }

  async function handleLaunch() {
    if (!selectedProjectId || !selectedVersionId || !selectedMachineType) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const req: WorkNodeLaunchRequest = {
        project_id: selectedProjectId,
        environment_version_id: selectedVersionId,
        machine_type: selectedMachineType,
        input_file_ids: selectedFileIds.length > 0 ? selectedFileIds : undefined,
        github_repo_ids: selectedRepoIds.length > 0 ? selectedRepoIds : undefined,
      };
      await api.post("/api/v1/work-nodes/sessions", req);
      setShowLaunch(false);
      loadNodes();
    } catch (err) {
      setLaunchError(err instanceof ApiError ? err.message : "Launch failed");
    } finally {
      setLaunching(false);
    }
  }

  async function handleStop(nodeId: number) {
    if (!confirm("Stop this work node? Files in /outputs/ will be synced to GCS. Data in /scratch will be lost.")) return;
    setStoppingNodes((prev) => new Set(prev).add(nodeId));
    try {
      await api.post(`/api/v1/work-nodes/sessions/${nodeId}/stop`);
      loadNodes();
      if (viewingNode?.id === nodeId) setViewingNode(null);
    } catch {
    } finally {
      setStoppingNodes((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  }

  function formatUptime(startedAt: string | null): string {
    if (!startedAt) return "-";
    const diff = Date.now() - new Date(startedAt).getTime();
    const hours = Math.floor(diff / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function formatTimestamp(ts: string | null): string {
    if (!ts) return "-";
    return new Date(ts).toLocaleString();
  }

  function extractSshCommand(accessUrl: string | null): string {
    if (!accessUrl) return "";
    // access_url is like ssh://1.2.3.4:22
    const ip = accessUrl.replace("ssh://", "").replace(/:\d+$/, "");
    return `ssh ${ip}`;
  }

  if (permLoading || loading) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center">
            <LoadingSpinner />
          </main>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold text-gray-900">Work Nodes</h1>
            {canAccess("work_nodes", "launch") && (
              <button
                onClick={openLaunchDialog}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
              >
                Launch Work Node
              </button>
            )}
          </div>

          {/* Quick Start Guide */}
          <div className="mb-6">
            <button
              onClick={() => setShowGuide(!showGuide)}
              className="inline-flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              How work nodes work
            </button>
            {showGuide && (
              <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-4">
                <div className="text-sm text-blue-800 space-y-2">
                  <ul className="space-y-1.5 text-blue-700">
                    <li><strong>Work nodes</strong> are full Linux VMs with SSH access. They run conda environments you configure on the <a href="/environments" className="underline font-medium">Environments</a> page.</li>
                    <li><strong>Input files</strong> are mounted at <code className="bg-blue-100 px-1 rounded">/data/</code>. Select data mounts during launch to access pipeline outputs, uploads, and shared results.</li>
                    <li><strong>GitHub repos</strong> are cloned at boot into <code className="bg-blue-100 px-1 rounded">~/repos/</code>. Add repos in the section below, then select them when launching.</li>
                    <li><strong>Output files</strong> should be saved to <code className="bg-blue-100 px-1 rounded">/outputs/</code>. Everything here is automatically synced to GCS when you stop the node.</li>
                    <li><strong>Scratch space</strong> at <code className="bg-blue-100 px-1 rounded">/scratch/</code> is for temporary computation. This data is lost when the node stops.</li>
                    <li><strong>SSH access</strong> uses the credentials from your <a href="/profile" className="underline font-medium">Profile Settings</a>. The SSH command appears after launch.</li>
                  </ul>
                </div>
              </div>
            )}
          </div>

          {/* GitHub Repos section */}
          <div className="mb-6">
            <button
              onClick={() => setShowRepos(!showRepos)}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-gray-900"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className={`h-4 w-4 transition-transform ${showRepos ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
              GitHub Repos ({repos.length})
            </button>
            {showRepos && (
              <div className="mt-2 bg-white rounded-lg border border-gray-200 p-4">
                <p className="text-xs text-gray-500 mb-3">Add GitHub repos to clone into your work nodes at boot. Provide the git SSH URL from GitHub.</p>

                {repoError && (
                  <div className="bg-red-50 border border-red-200 rounded p-2 mb-3 text-xs text-red-700">{repoError}</div>
                )}

                {/* Add repo form */}
                {canAccess("work_nodes", "launch") && (
                  <div className="flex gap-2 mb-3">
                    <input
                      type="text"
                      value={newRepoUrl}
                      onChange={(e) => setNewRepoUrl(e.target.value)}
                      placeholder="git@github.com:owner/repo.git"
                      className="flex-1 border rounded px-3 py-1.5 text-sm font-mono"
                    />
                    <input
                      type="text"
                      value={newRepoName}
                      onChange={(e) => setNewRepoName(e.target.value)}
                      placeholder="Display name (optional)"
                      className="w-48 border rounded px-3 py-1.5 text-sm"
                    />
                    <button
                      onClick={handleAddRepo}
                      disabled={addingRepo || !newRepoUrl.trim()}
                      className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {addingRepo ? "Adding..." : "Add"}
                    </button>
                  </div>
                )}

                {/* Repo list */}
                {repos.length === 0 ? (
                  <p className="text-xs text-gray-400">No repos configured yet.</p>
                ) : (
                  <div className="space-y-1">
                    {repos.map((repo) => (
                      <div key={repo.id} className="flex items-center justify-between py-1.5 px-2 bg-gray-50 rounded text-sm">
                        <div>
                          <span className="font-medium">{repo.display_name}</span>
                          <span className="text-gray-400 font-mono text-xs ml-2">{repo.git_ssh_url}</span>
                        </div>
                        {canAccess("work_nodes", "launch") && (
                          <button
                            onClick={() => handleDeleteRepo(repo.id)}
                            className="text-red-500 hover:text-red-700 text-xs"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Node list */}
          {nodes.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
              <p className="text-gray-500">
                No work nodes. Launch one to get an SSH-accessible compute environment.
              </p>
            </div>
          ) : (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Machine Type</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resources</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Start Time</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {nodes.map((node) => (
                    <tr key={node.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => setViewingNode(node)}>
                      <td className="px-4 py-3 text-sm">{node.user?.name || node.user?.email || "\u2014"}</td>
                      <td className="px-4 py-3 text-sm text-gray-900">{node.machine_type || "\u2014"}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{node.cpu_cores} CPU / {node.memory_gb} GB</td>
                      <td className="px-4 py-3">
                        {stoppingNodes.has(node.id) ? (
                          <span className="flex items-center gap-1 text-xs text-orange-700">
                            <LoadingSpinner size="sm" />
                            Syncing outputs...
                          </span>
                        ) : (
                          <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[node.status] || "bg-gray-100"}`}>
                            {statusLabel(node)}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {node.started_at ? new Date(node.started_at).toLocaleString() : "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-sm font-mono" onClick={(e) => e.stopPropagation()}>
                        {node.access_url && node.status === "running" ? (
                          <button
                            onClick={() => navigator.clipboard.writeText(extractSshCommand(node.access_url))}
                            className="text-indigo-600 hover:underline"
                            title={extractSshCommand(node.access_url)}
                          >
                            {extractSshCommand(node.access_url)}
                          </button>
                        ) : "\u2014"}
                      </td>
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-2">
                          {canAccess("work_nodes", "stop") && node.status === "running" && (
                            <button
                              onClick={() => handleStop(node.id)}
                              className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50"
                            >
                              Stop
                            </button>
                          )}
                          <button
                            onClick={() => setViewingNode(node)}
                            className="text-xs px-2 py-1 border border-indigo-600 text-indigo-600 rounded hover:bg-indigo-50"
                          >
                            Details
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Detail panel */}
          {viewingNode && (
            <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setViewingNode(null)}>
              <div className="bg-white rounded-lg shadow-xl max-w-lg w-full mx-4 p-6" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-between items-center mb-4">
                  <h2 className="text-lg font-semibold">Work Node Details</h2>
                  <button onClick={() => setViewingNode(null)} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
                </div>
                <div className="space-y-3 text-sm">
                  <div className="flex justify-between">
                    <span className="text-gray-500">Status</span>
                    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[viewingNode.status] || "bg-gray-100"}`}>
                      {statusLabel(viewingNode)}
                    </span>
                  </div>
                  {viewingNode.status === "failed" && !viewingNode.access_url && (
                    <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
                      GCP Resources Unavailable -- the VM could not be created. Try again later or choose a different machine type.
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">User</span>
                    <span>{viewingNode.user?.name || viewingNode.user?.email || "\u2014"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Machine Type</span>
                    <span>{viewingNode.machine_type || "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Resources</span>
                    <span>{viewingNode.cpu_cores} CPU / {viewingNode.memory_gb} GB RAM</span>
                  </div>
                  {viewingNode.gce_instance_name && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">VM Instance</span>
                      <span className="font-mono text-xs">{viewingNode.gce_instance_name}</span>
                    </div>
                  )}
                  {viewingNode.gce_zone && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Zone</span>
                      <span>{viewingNode.gce_zone}</span>
                    </div>
                  )}
                  {viewingNode.access_url && viewingNode.status === "running" && (
                    <div>
                      <span className="text-gray-500 block mb-1">SSH Command</span>
                      <div className="bg-gray-900 text-green-400 rounded p-3 font-mono text-xs flex items-center justify-between">
                        <code>{extractSshCommand(viewingNode.access_url)}</code>
                        <button
                          onClick={() => navigator.clipboard.writeText(extractSshCommand(viewingNode.access_url))}
                          className="ml-2 text-gray-400 hover:text-white text-xs"
                        >
                          Copy
                        </button>
                      </div>
                    </div>
                  )}
                  {viewingNode.github_repo_ids && viewingNode.github_repo_ids.length > 0 && (
                    <div>
                      <span className="text-gray-500 block mb-1">Cloned Repos</span>
                      <ul className="text-xs text-gray-700 space-y-1">
                        {viewingNode.github_repo_ids.map((repoId) => {
                          const repo = repos.find((r) => r.id === repoId);
                          return (
                            <li key={repoId} className="font-mono bg-gray-50 px-2 py-1 rounded">
                              ~/repos/{repo?.display_name || `repo-${repoId}`}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                  {viewingNode.input_file_ids && viewingNode.input_file_ids.length > 0 && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Input Files</span>
                      <span>{viewingNode.input_file_ids.length} file(s) in /data/</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">Started</span>
                    <span>{formatTimestamp(viewingNode.started_at)}</span>
                  </div>
                  {viewingNode.status === "running" && viewingNode.started_at && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Uptime</span>
                      <span>{formatUptime(viewingNode.started_at)}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">Last Heartbeat</span>
                    <span>{viewingNode.heartbeat_at ? formatTimestamp(viewingNode.heartbeat_at) : "-"}</span>
                  </div>
                  {viewingNode.stopped_at && (
                    <div className="flex justify-between">
                      <span className="text-gray-500">Stopped</span>
                      <span>{formatTimestamp(viewingNode.stopped_at)}</span>
                    </div>
                  )}
                </div>
                {canAccess("work_nodes", "stop") && viewingNode.status === "running" && (
                  <div className="mt-4 pt-4 border-t">
                    <button
                      onClick={() => handleStop(viewingNode.id)}
                      className="w-full px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 text-sm"
                    >
                      Stop Work Node
                    </button>
                    <p className="text-xs text-gray-400 mt-1 text-center">Files in /outputs/ will be synced. Data in /scratch will be lost.</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Launch dialog -- 6 steps */}
          {showLaunch && (
            <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setShowLaunch(false)}>
              <div className="bg-white rounded-lg shadow-xl max-w-xl w-full mx-4 p-6 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-between items-center mb-4">
                  <h2 className="text-lg font-semibold">Launch Work Node</h2>
                  <button onClick={() => setShowLaunch(false)} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
                </div>

                {/* Step indicators */}
                <div className="flex gap-2 mb-6">
                  {[1, 2, 3, 4, 5, 6].map((s) => (
                    <div key={s} className={`h-1 flex-1 rounded ${s <= launchStep ? "bg-indigo-600" : "bg-gray-200"}`} />
                  ))}
                </div>

                {launchError && (
                  <div className="bg-red-50 border border-red-200 rounded p-3 mb-4 text-sm text-red-700">{launchError}</div>
                )}

                {/* Step 1: Select project */}
                {launchStep === 1 && (
                  <div>
                    <h3 className="font-medium mb-3">Select Project</h3>
                    <div className="space-y-2 max-h-60 overflow-y-auto">
                      {projects.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => handleProjectSelect(p.id)}
                          className="w-full text-left p-3 border rounded-lg hover:border-indigo-400 hover:bg-indigo-50 transition-colors"
                        >
                          <div className="font-medium text-sm">{p.name}</div>
                          {p.description && <div className="text-xs text-gray-500 mt-0.5">{p.description}</div>}
                        </button>
                      ))}
                      {projects.length === 0 && <p className="text-sm text-gray-500">No projects found</p>}
                    </div>
                  </div>
                )}

                {/* Step 2: Select input files */}
                {launchStep === 2 && (
                  <div>
                    <h3 className="font-medium mb-3">Select Input Files</h3>
                    <p className="text-xs text-gray-500 mb-3">Choose an experiment, then select files to copy to /data/ at boot (optional)</p>

                    {/* Experiment selector */}
                    {projectExperiments.length === 0 ? (
                      <p className="text-sm text-gray-400">No experiments in this project.</p>
                    ) : (
                      <div className="mb-3">
                        <label className="text-xs font-medium text-gray-600 block mb-1">Experiment</label>
                        <select
                          className="w-full border rounded px-3 py-2 text-sm"
                          value={selectedExperimentId || ""}
                          onChange={(e) => {
                            const val = e.target.value ? Number(e.target.value) : null;
                            if (val) handleExperimentSelect(val);
                          }}
                        >
                          <option value="">Select an experiment...</option>
                          {projectExperiments.map((exp) => (
                            <option key={exp.id} value={exp.id}>
                              {exp.name}{exp.code ? ` (${exp.code})` : ""}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}

                    {/* File picker (shown after experiment is selected) */}
                    {selectedExperimentId && experimentFiles.length === 0 && (
                      <p className="text-sm text-gray-400">No files found for this experiment.</p>
                    )}
                    {selectedExperimentId && experimentFiles.length > 0 && (
                      <>
                        <button
                          onClick={() => setShowFileSelector(!showFileSelector)}
                          className="text-xs text-indigo-600 hover:underline mb-2"
                        >
                          {showFileSelector ? "Hide file picker" : `Select files (${experimentFiles.length} available)`}
                        </button>
                        {showFileSelector && (
                          <FileTreeSelector
                            files={experimentFiles}
                            sampleNames={sampleNames}
                            onSelectionChange={setSelectedFileIds}
                          />
                        )}
                        {!showFileSelector && selectedFileIds.length > 0 && (
                          <p className="text-xs text-gray-500">{selectedFileIds.length} file(s) selected</p>
                        )}
                      </>
                    )}

                    <div className="flex gap-2 mt-4">
                      <button onClick={() => setLaunchStep(1)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button onClick={() => setLaunchStep(3)} className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">
                        Next
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 3: Select environment */}
                {launchStep === 3 && (
                  <div>
                    <h3 className="font-medium mb-3">Select Environment</h3>
                    {environments.length === 0 ? (
                      <div className="bg-yellow-50 border border-yellow-200 rounded p-3 text-sm text-yellow-700">
                        No work node environments found. Create one from the <a href="/environments" className="underline font-medium">Environments</a> page with type &quot;Work Node&quot;.
                      </div>
                    ) : (
                      <div className="space-y-2 max-h-60 overflow-y-auto">
                        {environments.map((env) => (
                          <button
                            key={env.id}
                            onClick={() => { handleEnvSelect(env.id); }}
                            className={`w-full text-left p-3 border rounded-lg hover:border-indigo-400 transition-colors ${selectedEnvId === env.id ? "border-indigo-500 bg-indigo-50" : ""}`}
                          >
                            <div className="font-medium text-sm">{env.name}</div>
                            {env.description && <div className="text-xs text-gray-500 mt-0.5">{env.description}</div>}
                            {env.latest_version && (
                              <div className="text-xs text-gray-400 mt-1">
                                v{env.latest_version.version_number} - {env.latest_version.status}
                              </div>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                    {envDetail && (
                      <div className="mt-3">
                        <label className="text-xs font-medium text-gray-600">Version</label>
                        <select
                          className="w-full mt-1 border rounded px-3 py-2 text-sm"
                          value={selectedVersionId || ""}
                          onChange={(e) => setSelectedVersionId(Number(e.target.value))}
                        >
                          <option value="">Select version</option>
                          {envDetail.versions
                            .filter((v) => v.status === "ready" && v.image_uri)
                            .map((v) => (
                              <option key={v.id} value={v.id}>v{v.version_number}.{v.build_number} (ready)</option>
                            ))}
                        </select>
                      </div>
                    )}
                    <div className="flex gap-2 mt-4">
                      <button onClick={() => setLaunchStep(2)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button
                        onClick={() => setLaunchStep(4)}
                        disabled={!selectedVersionId}
                        className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 4: Select GitHub repos */}
                {launchStep === 4 && (
                  <div>
                    <h3 className="font-medium mb-3">Select GitHub Repos</h3>
                    <p className="text-xs text-gray-500 mb-3">These repos will be cloned into ~/repos/ when the node boots.</p>
                    {repos.length === 0 ? (
                      <p className="text-sm text-gray-400">No repos configured. You can add them from the GitHub Repos section on this page.</p>
                    ) : (
                      <div className="space-y-2">
                        {repos.map((repo) => (
                          <label key={repo.id} className="flex items-start gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={selectedRepoIds.includes(repo.id)}
                              onChange={() => toggleRepo(repo.id)}
                              className="mt-0.5"
                            />
                            <div>
                              <div className="text-sm font-medium">{repo.display_name}</div>
                              <div className="text-xs text-gray-400 font-mono mt-0.5">{repo.git_ssh_url}</div>
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                    <div className="flex gap-2 mt-4">
                      <button onClick={() => setLaunchStep(3)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button onClick={() => setLaunchStep(5)} className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">
                        Next
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 5: Select machine type */}
                {launchStep === 5 && (
                  <div>
                    <h3 className="font-medium mb-3">Select Machine Type</h3>
                    {Object.entries(
                      machineTypes.reduce<Record<string, MachineType[]>>((groups, mt) => {
                        (groups[mt.category] = groups[mt.category] || []).push(mt);
                        return groups;
                      }, {})
                    ).map(([category, types]) => (
                      <div key={category} className="mb-4">
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
                          {CATEGORY_LABELS[category] || category}
                        </h4>
                        <div className="space-y-2">
                          {types.map((mt) => (
                            <button
                              key={mt.name}
                              onClick={() => setSelectedMachineType(mt.name)}
                              className={`w-full text-left p-3 border rounded-lg hover:border-indigo-400 transition-colors ${selectedMachineType === mt.name ? "border-indigo-500 bg-indigo-50" : ""}`}
                            >
                              <div className="flex justify-between items-center">
                                <span className="font-mono text-sm">{mt.name}</span>
                                <span className="text-xs text-gray-500">
                                  {mt.cpu} CPU / {mt.memory_gb} GB{mt.gpu ? ` / ${mt.gpu}` : ""}
                                </span>
                              </div>
                              <div className="text-xs text-gray-500 mt-0.5">{mt.description}</div>
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                    <div className="flex gap-2 mt-4">
                      <button onClick={() => setLaunchStep(4)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button
                        onClick={() => setLaunchStep(6)}
                        disabled={!selectedMachineType}
                        className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                      >
                        Review
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 6: Review and launch */}
                {launchStep === 6 && (
                  <div>
                    <h3 className="font-medium mb-3">Review</h3>
                    <div className="space-y-2 text-sm bg-gray-50 rounded-lg p-4">
                      <div className="flex justify-between">
                        <span className="text-gray-500">Project</span>
                        <span>{projects.find((p) => p.id === selectedProjectId)?.name}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Input Files</span>
                        <span>{selectedFileIds.length > 0 ? selectedFileIds.length + " files" : "None"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Environment</span>
                        <span>{environments.find((e) => e.id === selectedEnvId)?.name}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Repos</span>
                        <span>{selectedRepoIds.length > 0 ? selectedRepoIds.length + " repos" : "None"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Machine Type</span>
                        <span className="font-mono">{selectedMachineType}</span>
                      </div>
                      {(() => {
                        const mt = machineTypes.find((m) => m.name === selectedMachineType);
                        return mt ? (
                          <div className="flex justify-between">
                            <span className="text-gray-500">Resources</span>
                            <span>{mt.cpu} CPU / {mt.memory_gb} GB{mt.gpu ? ` / ${mt.gpu}` : ""}</span>
                          </div>
                        ) : null;
                      })()}
                    </div>
                    <div className="flex gap-2 mt-4">
                      <button onClick={() => setLaunchStep(5)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button
                        onClick={handleLaunch}
                        disabled={launching}
                        className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {launching ? "Launching..." : "Launch Work Node"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
