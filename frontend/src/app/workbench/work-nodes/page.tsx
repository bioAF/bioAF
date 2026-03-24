"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import type {
  WorkNode,
  WorkNodeListResponse,
  WorkNodeLaunchRequest,
  MachineType,
  DataMount,
  Project,
  EnvironmentResponse,
  EnvironmentListResponse,
  EnvironmentDetailResponse,
} from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-800",
  starting: "bg-blue-100 text-blue-800",
  running: "bg-green-100 text-green-800",
  stopping: "bg-orange-100 text-orange-800",
  stopped: "bg-gray-100 text-gray-600",
  failed: "bg-red-100 text-red-800",
};

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

  // Launch form state
  const [launchStep, setLaunchStep] = useState(1);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [dataMounts, setDataMounts] = useState<DataMount[]>([]);
  const [selectedMounts, setSelectedMounts] = useState<string[]>([]);
  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [selectedEnvId, setSelectedEnvId] = useState<number | null>(null);
  const [envDetail, setEnvDetail] = useState<EnvironmentDetailResponse | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [machineTypes, setMachineTypes] = useState<MachineType[]>([]);
  const [selectedMachineType, setSelectedMachineType] = useState<string>("");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("work_nodes", "view")) { router.push("/dashboard"); return; }
    loadNodes();
  }, [router, permLoading, canAccess]);

  // Auto-refresh while starting
  useEffect(() => {
    const hasStarting = nodes.some((n) => n.status === "starting");
    if (!hasStarting) return;
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

  async function openLaunchDialog() {
    setShowLaunch(true);
    setLaunchStep(1);
    setSelectedProjectId(null);
    setSelectedMounts([]);
    setSelectedEnvId(null);
    setEnvDetail(null);
    setSelectedVersionId(null);
    setSelectedMachineType("");
    setLaunchError(null);

    try {
      const [projectData, mtData, envData] = await Promise.all([
        api.get<{ projects: Project[]; total: number }>("/api/projects?page_size=100"),
        api.get<MachineType[]>("/api/v1/work-nodes/machine-types"),
        api.get<EnvironmentListResponse>("/api/v1/environments"),
      ]);
      setProjects(projectData.projects);
      setMachineTypes(mtData);
      setEnvironments(envData.environments);
    } catch {}
  }

  async function handleProjectSelect(projectId: number) {
    setSelectedProjectId(projectId);
    try {
      const mounts = await api.get<DataMount[]>(`/api/v1/work-nodes/data-mounts/${projectId}`);
      setDataMounts(mounts);
    } catch {
      setDataMounts([]);
    }
    setLaunchStep(2);
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

  function toggleMount(path: string) {
    setSelectedMounts((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path]
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
        data_mount_paths: selectedMounts.length > 0 ? selectedMounts : undefined,
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
    if (!confirm("Stop this work node? Data in /scratch will be lost.")) return;
    try {
      await api.post(`/api/v1/work-nodes/sessions/${nodeId}/stop`);
      loadNodes();
      if (viewingNode?.id === nodeId) setViewingNode(null);
    } catch {}
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
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium"
              >
                Launch Work Node
              </button>
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
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Machine Type</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resources</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Uptime</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Last Heartbeat</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {nodes.map((node) => (
                    <tr key={node.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[node.status] || "bg-gray-100"}`}>
                          {node.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-900">{node.machine_type || "-"}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{node.cpu_cores} CPU / {node.memory_gb} GB</td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {node.status === "running" ? formatUptime(node.started_at) : "-"}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {node.heartbeat_at ? formatTimestamp(node.heartbeat_at) : "-"}
                      </td>
                      <td className="px-4 py-3 text-sm space-x-2">
                        <button
                          onClick={() => setViewingNode(node)}
                          className="text-indigo-600 hover:text-indigo-800"
                        >
                          Details
                        </button>
                        {canAccess("work_nodes", "stop") && node.status === "running" && (
                          <button
                            onClick={() => handleStop(node.id)}
                            className="text-red-600 hover:text-red-800"
                          >
                            Stop
                          </button>
                        )}
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
                      {viewingNode.status}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Machine Type</span>
                    <span>{viewingNode.machine_type || "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-500">Resources</span>
                    <span>{viewingNode.cpu_cores} CPU / {viewingNode.memory_gb} GB RAM</span>
                  </div>
                  {viewingNode.access_url && viewingNode.status === "running" && (
                    <div>
                      <span className="text-gray-500 block mb-1">SSH Command</span>
                      <div className="bg-gray-900 text-green-400 rounded p-3 font-mono text-xs flex items-center justify-between">
                        <code>ssh {viewingNode.access_url.replace("ssh://", "").replace(/:\d+/, "")}</code>
                        <button
                          onClick={() => navigator.clipboard.writeText(`ssh ${viewingNode.access_url?.replace("ssh://", "").replace(/:\d+/, "") ?? ""}`)}
                          className="ml-2 text-gray-400 hover:text-white text-xs"
                        >
                          Copy
                        </button>
                      </div>
                    </div>
                  )}
                  {viewingNode.data_mount_paths && viewingNode.data_mount_paths.length > 0 && (
                    <div>
                      <span className="text-gray-500 block mb-1">Data Mounts</span>
                      <ul className="text-xs text-gray-700 space-y-1">
                        {viewingNode.data_mount_paths.map((p) => (
                          <li key={p} className="font-mono bg-gray-50 px-2 py-1 rounded">/data/{p}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-gray-500">Started</span>
                    <span>{formatTimestamp(viewingNode.started_at)}</span>
                  </div>
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
                    <p className="text-xs text-gray-400 mt-1 text-center">Data in /scratch will be lost</p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Launch dialog */}
          {showLaunch && (
            <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setShowLaunch(false)}>
              <div className="bg-white rounded-lg shadow-xl max-w-xl w-full mx-4 p-6 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-between items-center mb-4">
                  <h2 className="text-lg font-semibold">Launch Work Node</h2>
                  <button onClick={() => setShowLaunch(false)} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
                </div>

                {/* Step indicators */}
                <div className="flex gap-2 mb-6">
                  {[1, 2, 3, 4, 5].map((s) => (
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

                {/* Step 2: Select data mounts */}
                {launchStep === 2 && (
                  <div>
                    <h3 className="font-medium mb-3">Select Data Directories</h3>
                    <p className="text-xs text-gray-500 mb-3">These will be mounted read-only at /data/</p>
                    <div className="space-y-2">
                      {dataMounts.map((mount) => (
                        <label key={mount.path} className="flex items-start gap-3 p-3 border rounded-lg hover:bg-gray-50 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={selectedMounts.includes(mount.path)}
                            onChange={() => toggleMount(mount.path)}
                            className="mt-0.5"
                          />
                          <div>
                            <div className="text-sm font-medium">{mount.label}</div>
                            <div className="text-xs text-gray-500">{mount.description}</div>
                            <div className="text-xs text-gray-400 font-mono mt-0.5">{mount.path}</div>
                          </div>
                        </label>
                      ))}
                    </div>
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
                              <option key={v.id} value={v.id}>v{v.version_number} (ready)</option>
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

                {/* Step 4: Select machine type */}
                {launchStep === 4 && (
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
                      <button onClick={() => setLaunchStep(3)} className="px-4 py-2 border rounded text-sm">Back</button>
                      <button
                        onClick={() => setLaunchStep(5)}
                        disabled={!selectedMachineType}
                        className="px-4 py-2 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
                      >
                        Review
                      </button>
                    </div>
                  </div>
                )}

                {/* Step 5: Review and launch */}
                {launchStep === 5 && (
                  <div>
                    <h3 className="font-medium mb-3">Review</h3>
                    <div className="space-y-2 text-sm bg-gray-50 rounded-lg p-4">
                      <div className="flex justify-between">
                        <span className="text-gray-500">Project</span>
                        <span>{projects.find((p) => p.id === selectedProjectId)?.name}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Data Mounts</span>
                        <span>{selectedMounts.length > 0 ? selectedMounts.length + " directories" : "None"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Environment</span>
                        <span>{environments.find((e) => e.id === selectedEnvId)?.name}</span>
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
                      <button onClick={() => setLaunchStep(4)} className="px-4 py-2 border rounded text-sm">Back</button>
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
