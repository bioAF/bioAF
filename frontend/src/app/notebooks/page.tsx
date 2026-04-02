"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { useComponents } from "@/hooks/useComponents";
import type {
  NotebookSession,
  SessionListResponse,
  SessionLaunchRequest,
  ResourceProfile,
  SessionType,
  Experiment,
  ExperimentListResponse,
  EnvironmentResponse,
  EnvironmentListResponse,
  EnvironmentDetailResponse,
  FileResponse,
  FileListResponse,
  Sample,
  Project,
  ProjectListResponse,
} from "@/lib/types";
import { RESOURCE_PROFILES } from "@/lib/types";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";

const SESSION_STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-800",
  starting: "bg-blue-100 text-blue-800",
  running: "bg-green-100 text-green-800",
  idle: "bg-yellow-100 text-yellow-800",
  stopping: "bg-orange-100 text-orange-800",
  stopped: "bg-gray-100 text-gray-600",
  failed: "bg-red-100 text-red-800",
};

export default function NotebooksPage() {
  const router = useRouter();
  const { components } = useComponents();
  const jupyterEnabled = components.some((c) => c.key === "jupyterhub" && c.enabled);
  const rstudioEnabled = components.some((c) => c.key === "rstudio" && c.enabled);
  const [sessions, setSessions] = useState<NotebookSession[]>([]);
  const [viewingSession, setViewingSession] = useState<NotebookSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [imageBuildStatus, setImageBuildStatus] = useState<{
    build_id: string | null;
    build_status: string | null;
    image_uri: string | null;
  } | null>(null);

  // Launch modal state
  const [showLaunchModal, setShowLaunchModal] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<ResourceProfile>("small");
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [scopeType, setScopeType] = useState<"experiment" | "project">("experiment");
  const [selectedExperiment, setSelectedExperiment] = useState<number | null>(null);
  const [selectedProject, setSelectedProject] = useState<number | null>(null);
  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [selectedEnvId, setSelectedEnvId] = useState<number | null>(null);
  const [selectedEnvDetail, setSelectedEnvDetail] = useState<EnvironmentDetailResponse | null>(null);
  const [selectedVersionImageUri, setSelectedVersionImageUri] = useState<string | null>(null);
  const [showFileSelector, setShowFileSelector] = useState(false);
  const [experimentFiles, setExperimentFiles] = useState<FileResponse[]>([]);
  const [sampleNames, setSampleNames] = useState<Record<number, string>>({});
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [activeBranchCount, setActiveBranchCount] = useState(0);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadSessions();
    loadExperiments();
    loadProjects();
    loadBuildStatus();
    loadEnvironments();
  }, [router]);

  useEffect(() => {
    const hasStarting = sessions.some((s) => s.status === "starting");
    if (!hasStarting) return;
    const interval = setInterval(() => loadSessions(), 10000);
    return () => clearInterval(interval);
  }, [sessions]);

  async function loadBuildStatus() {
    try {
      const status = await api.get<{
        build_id: string | null;
        build_status: string | null;
        image_uri: string | null;
      }>("/api/v1/infrastructure/notebook-image/build-status");
      setImageBuildStatus(status);
      if (status.build_status && ["WORKING", "QUEUED"].includes(status.build_status)) {
        setTimeout(loadBuildStatus, 15000);
      }
    } catch {}
  }

  async function loadSessions() {
    try {
      const data = await api.get<SessionListResponse>("/api/v1/notebooks/sessions");
      setSessions(data.sessions);
    } catch {
    } finally {
      setLoading(false);
    }
  }

  async function loadExperiments() {
    try {
      const data = await api.get<ExperimentListResponse>("/api/experiments?page_size=100");
      setExperiments(data.experiments);
    } catch {}
  }

  async function loadProjects() {
    try {
      const data = await api.get<ProjectListResponse>("/api/projects?page_size=100");
      setProjects(data.projects);
    } catch {}
  }

  async function loadEnvironments() {
    try {
      const data = await api.get<EnvironmentListResponse>("/api/v1/environments");
      setEnvironments(data.environments);
      const withReady = data.environments.find(
        (e) => e.latest_version?.status === "ready" && e.latest_version?.image_uri
      );
      if (withReady && withReady.latest_version) {
        setSelectedEnvId(withReady.id);
        setSelectedVersionImageUri(withReady.latest_version.image_uri);
      }
    } catch {}
  }

  async function handleEnvChange(envId: number) {
    setSelectedEnvId(envId);
    setSelectedVersionImageUri(null);
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${envId}`);
      setSelectedEnvDetail(detail);
      const readyVersion = detail.versions.find((v) => v.status === "ready" && v.image_uri);
      if (readyVersion) {
        setSelectedVersionImageUri(readyVersion.image_uri);
      }
    } catch {}
  }

  function openLaunchModal() {
    setShowLaunchModal(true);
    setLaunchError(null);
    setSelectedFileIds([]);
    setExperimentFiles([]);
    setSampleNames({});
    setShowFileSelector(false);
    setActiveBranchCount(0);
  }

  async function loadFilesForExperiment(experimentId: number) {
    try {
      const data = await api.get<FileListResponse>(
        `/api/experiments/${experimentId}/files?page_size=500`
      );
      setExperimentFiles(data.files);
      setSelectedFileIds([]);

      const sampleIds = new Set<number>();
      for (const file of data.files) {
        for (const sid of file.sample_ids || []) {
          sampleIds.add(sid);
        }
      }
      if (sampleIds.size > 0) {
        try {
          const samplesData = await api.get<{ samples: Sample[] }>(
            `/api/experiments/${experimentId}/samples?page_size=500`
          );
          const names: Record<number, string> = {};
          for (const s of samplesData.samples) {
            names[s.id] = s.sample_id_external || `Sample ${s.id}`;
          }
          setSampleNames(names);
        } catch {
          setSampleNames({});
        }
      } else {
        setSampleNames({});
      }

      // Check active branches
      const active = sessions.filter(
        (s) =>
          s.experiment?.id === experimentId &&
          ["running", "starting", "idle"].includes(s.status) &&
          s.git_branch_name
      );
      setActiveBranchCount(active.length);
    } catch {
      setExperimentFiles([]);
    }
  }

  function handleExperimentChange(expId: number | null) {
    setSelectedExperiment(expId);
    setSelectedFileIds([]);
    setExperimentFiles([]);
    setShowFileSelector(false);
    setActiveBranchCount(0);
    if (expId) {
      loadFilesForExperiment(expId);
    }
  }

  async function handleLaunch(sessionType: SessionType) {
    setLaunching(true);
    setLaunchError(null);
    try {
      const req: SessionLaunchRequest = {
        session_type: sessionType,
        resource_profile: selectedProfile,
        experiment_id: scopeType === "experiment" ? selectedExperiment : undefined,
        image_uri: selectedVersionImageUri,
        input_file_ids: selectedFileIds.length > 0 ? selectedFileIds : undefined,
      };
      await api.post("/api/v1/notebooks/sessions", req);
      setShowLaunchModal(false);
      loadSessions();
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : "Failed to launch session");
    } finally {
      setLaunching(false);
    }
  }

  async function handleStop(sessionId: number) {
    if (!confirm("Stop this notebook session? Unsaved work may be lost.")) return;
    try {
      await api.post(`/api/v1/notebooks/sessions/${sessionId}/stop`);
      loadSessions();
    } catch {}
  }

  async function handleSync(sessionId: number) {
    try {
      await api.post(`/api/v1/notebooks/sessions/${sessionId}/sync`);
      alert("Sync triggered successfully");
    } catch {}
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Notebook Sessions</h1>
            <button
              onClick={openLaunchModal}
              className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
            >
              Launch Session
            </button>
          </div>

          {/* Build Status Banner */}
          {imageBuildStatus?.build_status && ["WORKING", "QUEUED"].includes(imageBuildStatus.build_status) && (
            <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 mb-6">
              <div className="flex items-center gap-3">
                <div className="animate-spin h-5 w-5 border-2 border-amber-500 border-t-transparent rounded-full" />
                <div>
                  <p className="text-sm font-medium text-amber-800">
                    Notebook image is building
                  </p>
                  <p className="text-xs text-amber-600 mt-0.5">
                    Status: {imageBuildStatus.build_status}
                    {imageBuildStatus.build_id && (
                      <span className="ml-1 text-amber-400">
                        (build {imageBuildStatus.build_id.slice(0, 8)})
                      </span>
                    )}
                    {" -- "}this one-time setup can take up to an hour. Sessions launched now may fail until it completes.
                  </p>
                </div>
              </div>
            </div>
          )}

          {imageBuildStatus?.build_status === "FAILURE" && (
            <div className="bg-red-50 border border-red-300 rounded-lg p-4 mb-6">
              <p className="text-sm font-medium text-red-800">
                Notebook image build failed
              </p>
              <p className="text-xs text-red-600 mt-0.5">
                The last image build did not succeed. Re-enable the component in Infrastructure &gt; Components to retry.
              </p>
            </div>
          )}

          {/* Active Sessions */}
          <div className="bg-white rounded-lg shadow">
            <div className="p-6 border-b">
              <h2 className="text-lg font-semibold">Active Sessions</h2>
            </div>

            {loading ? (
              <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
            ) : (
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Profile</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Start Time</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access URL</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Experiment</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {sessions.map((s) => (
                    <tr key={s.id} className={`cursor-pointer ${s.status === "idle" ? "bg-yellow-50 hover:bg-yellow-100" : "hover:bg-gray-50"}`} onClick={() => setViewingSession(s)}>
                      <td className="px-4 py-3 text-sm capitalize font-medium">{s.session_type}</td>
                      <td className="px-4 py-3 text-sm">{s.user?.name || s.user?.email || "\u2014"}</td>
                      <td className="px-4 py-3 text-sm capitalize">
                        {s.resource_profile} ({s.cpu_cores} CPU, {s.memory_gb}GB)
                      </td>
                      <td className="px-4 py-3">
                        {s.status === "starting" ? (
                          <span className="flex items-center gap-1 text-xs text-blue-700">
                            <LoadingSpinner size="sm" />
                            Starting... this may take a few minutes
                          </span>
                        ) : (
                          <span className={`text-xs px-2 py-1 rounded ${SESSION_STATUS_COLORS[s.status] || "bg-gray-100"}`}>
                            {s.status}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        {s.started_at ? new Date(s.started_at).toLocaleString() : "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-sm font-mono" onClick={(e) => e.stopPropagation()}>
                        {s.proxy_url && s.status === "running" ? (
                          <a href={s.proxy_url} target="_blank" rel="noopener noreferrer" className="text-bioaf-600 hover:underline">
                            {s.proxy_url.replace("http://", "")}
                          </a>
                        ) : "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-sm" onClick={(e) => e.stopPropagation()}>
                        {s.experiment ? (
                          <Link href={`/experiments/${s.experiment.id}`} className="text-bioaf-600 hover:underline">
                            {s.experiment.name}
                          </Link>
                        ) : "\u2014"}
                      </td>
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <div className="flex gap-2">
                          {s.proxy_url && s.status === "running" && (
                            <a href={s.proxy_url} target="_blank" rel="noopener noreferrer" className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50">
                              Open
                            </a>
                          )}
                          {s.status === "running" && (
                            <button onClick={() => handleSync(s.id)} className="text-xs px-2 py-1 border border-green-600 text-green-600 rounded hover:bg-green-50">
                              Sync
                            </button>
                          )}
                          {["pending", "starting", "running", "idle"].includes(s.status) && (
                            <button onClick={() => handleStop(s.id)} className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50">
                              Stop
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {sessions.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">No active sessions</td></tr>
                  )}
                </tbody>
              </table>
            )}
          </div>

          {/* Session Detail Modal */}
          {viewingSession && (
            <DetailModal
              title={`${viewingSession.session_type.charAt(0).toUpperCase() + viewingSession.session_type.slice(1)} Session`}
              onClose={() => setViewingSession(null)}
              fields={[
                { label: "Type", value: viewingSession.session_type },
                { label: "Status", value: viewingSession.status },
                { label: "User", value: viewingSession.user?.name || viewingSession.user?.email },
                { label: "Resource Profile", value: viewingSession.resource_profile },
                { label: "CPU Cores", value: viewingSession.cpu_cores },
                { label: "Memory (GB)", value: viewingSession.memory_gb },
                { label: "Experiment", value: viewingSession.experiment?.name },
                { label: "Started", value: viewingSession.started_at ? new Date(viewingSession.started_at).toLocaleString() : null },
                { label: "Access URL", value: viewingSession.proxy_url || null },
                { label: "Idle Since", value: viewingSession.idle_since ? new Date(viewingSession.idle_since).toLocaleString() : null },
                { label: "Git Branch", value: viewingSession.git_branch_name || null },
                { label: "Git Commit", value: viewingSession.git_commit_hash || null },
              ]}
              actions={
                <>
                  {viewingSession.proxy_url && viewingSession.status === "running" && (
                    <a href={viewingSession.proxy_url} target="_blank" rel="noopener noreferrer" className="px-3 py-1.5 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50">
                      Open
                    </a>
                  )}
                  {viewingSession.status === "running" && (
                    <button onClick={() => { handleSync(viewingSession.id); }} className="px-3 py-1.5 border border-green-600 text-green-600 rounded text-sm hover:bg-green-50">
                      Sync
                    </button>
                  )}
                  {["pending", "starting", "running", "idle"].includes(viewingSession.status) && (
                    <button onClick={() => { handleStop(viewingSession.id); setViewingSession(null); }} className="px-3 py-1.5 border border-red-600 text-red-600 rounded text-sm hover:bg-red-50">
                      Stop
                    </button>
                  )}
                </>
              }
            />
          )}

          {/* Launch Modal */}
          {showLaunchModal && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl w-[600px] max-h-[85vh] overflow-y-auto">
                <div className="p-6 border-b flex items-center justify-between">
                  <h3 className="text-lg font-semibold">Launch Notebook Session</h3>
                  <button onClick={() => setShowLaunchModal(false)} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
                </div>
                <div className="p-6 space-y-5">
                  {/* Resource Profile */}
                  <div>
                    <label className="text-sm text-gray-500 mb-2 block">Resource Profile</label>
                    <div className="flex gap-3">
                      {(["small", "medium", "large"] as ResourceProfile[]).map((profile) => {
                        const specs = RESOURCE_PROFILES[profile];
                        return (
                          <button
                            key={profile}
                            onClick={() => setSelectedProfile(profile)}
                            className={`border rounded-lg px-4 py-3 text-sm flex-1 ${
                              selectedProfile === profile
                                ? "border-bioaf-500 bg-bioaf-50 text-bioaf-700"
                                : "border-gray-200 hover:border-gray-300"
                            }`}
                          >
                            <div className="font-semibold capitalize">{profile}</div>
                            <div className="text-xs text-gray-500">{specs.cpu} CPU, {specs.memory}GB RAM</div>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Environment */}
                  <div>
                    <label className="text-sm text-gray-500 mb-2 block">Environment</label>
                    <div className="flex gap-3">
                      <select
                        value={selectedEnvId || ""}
                        onChange={(e) => e.target.value ? handleEnvChange(Number(e.target.value)) : null}
                        className="border rounded px-3 py-2 text-sm flex-1"
                      >
                        <option value="">Select environment</option>
                        {environments.map((env) => (
                          <option key={env.id} value={env.id}>
                            {env.name}
                            {env.latest_version ? ` (v${env.latest_version.version_number} - ${env.latest_version.status})` : " (no versions)"}
                          </option>
                        ))}
                      </select>
                      {selectedEnvDetail && selectedEnvDetail.versions.filter((v) => v.status === "ready").length > 0 && (
                        <select
                          value={selectedVersionImageUri || ""}
                          onChange={(e) => setSelectedVersionImageUri(e.target.value || null)}
                          className="border rounded px-3 py-2 text-sm flex-1"
                        >
                          {selectedEnvDetail.versions
                            .filter((v) => v.status === "ready" && v.image_uri)
                            .map((v) => (
                              <option key={v.id} value={v.image_uri || ""}>
                                v{v.version_number} ({v.definition_format})
                              </option>
                            ))}
                        </select>
                      )}
                    </div>
                  </div>

                  {/* Scope: Experiment or Project */}
                  <div>
                    <label className="text-sm text-gray-500 mb-2 block">Link to (optional)</label>
                    <div className="flex gap-2 mb-2">
                      <button
                        onClick={() => { setScopeType("experiment"); setSelectedProject(null); }}
                        className={`px-3 py-1.5 text-sm rounded ${scopeType === "experiment" ? "bg-bioaf-100 text-bioaf-700 font-medium" : "text-gray-500 hover:bg-gray-100"}`}
                      >
                        Experiment
                      </button>
                      <button
                        onClick={() => { setScopeType("project"); setSelectedExperiment(null); setExperimentFiles([]); setSelectedFileIds([]); }}
                        className={`px-3 py-1.5 text-sm rounded ${scopeType === "project" ? "bg-bioaf-100 text-bioaf-700 font-medium" : "text-gray-500 hover:bg-gray-100"}`}
                      >
                        Project
                      </button>
                    </div>
                    {scopeType === "experiment" ? (
                      <select
                        value={selectedExperiment || ""}
                        onChange={(e) => handleExperimentChange(e.target.value ? Number(e.target.value) : null)}
                        className="border rounded px-3 py-2 text-sm w-full"
                      >
                        <option value="">No experiment</option>
                        {experiments.map((exp) => (
                          <option key={exp.id} value={exp.id}>{exp.name}{exp.code ? ` (${exp.code})` : ""}</option>
                        ))}
                      </select>
                    ) : (
                      <select
                        value={selectedProject || ""}
                        onChange={(e) => setSelectedProject(e.target.value ? Number(e.target.value) : null)}
                        className="border rounded px-3 py-2 text-sm w-full"
                      >
                        <option value="">No project</option>
                        {projects.map((p) => (
                          <option key={p.id} value={p.id}>{p.name}{p.code ? ` (${p.code})` : ""}</option>
                        ))}
                      </select>
                    )}
                  </div>

                  {/* Input Files (only for experiment scope with files available) */}
                  {scopeType === "experiment" && selectedExperiment && experimentFiles.length > 0 && (
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <label className="text-sm text-gray-500">Input Files (optional)</label>
                        <button
                          onClick={() => setShowFileSelector(!showFileSelector)}
                          className="text-xs text-bioaf-600 hover:underline"
                        >
                          {showFileSelector ? "Hide" : `Select files (${experimentFiles.length} available)`}
                        </button>
                      </div>
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
                    </div>
                  )}

                  {/* Branch conflict warning */}
                  {activeBranchCount > 0 && (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                      <p className="text-sm text-amber-800">
                        There {activeBranchCount === 1 ? "is" : "are"} {activeBranchCount} active notebook{" "}
                        {activeBranchCount === 1 ? "branch" : "branches"} for this experiment.
                        You may need to merge changes on GitHub after your session.
                      </p>
                    </div>
                  )}

                  {/* Error */}
                  {launchError && (
                    <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                      <p className="text-sm text-red-800">{launchError}</p>
                    </div>
                  )}
                </div>

                {/* Launch buttons */}
                <div className="p-6 border-t bg-gray-50 flex gap-3">
                  {rstudioEnabled && (
                    <button
                      onClick={() => handleLaunch("rstudio")}
                      disabled={launching}
                      className="flex-1 bg-blue-600 text-white px-6 py-2.5 rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
                    >
                      {launching ? "Launching..." : "Launch RStudio"}
                    </button>
                  )}
                  {jupyterEnabled && (
                    <button
                      onClick={() => handleLaunch("jupyter")}
                      disabled={launching}
                      className="flex-1 bg-bioaf-600 text-white px-6 py-2.5 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
                    >
                      {launching ? "Launching..." : "Launch Jupyter"}
                    </button>
                  )}
                  <button
                    onClick={() => setShowLaunchModal(false)}
                    className="px-4 py-2.5 border rounded-md text-sm text-gray-600 hover:bg-gray-100"
                  >
                    Cancel
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
