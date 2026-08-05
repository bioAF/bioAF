"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { DetailModal } from "@/components/shared/DetailModal";
import { api } from "@/lib/api";
import { useComponents } from "@/hooks/useComponents";
import type {
  NotebookSession,
  SessionListResponse,
  SessionLaunchRequest,
  SessionProvenance,
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
import { SessionBucketFilter, type SessionBucket } from "@/components/shared/SessionBucketFilter";
import { formatSessionStatusLabel, formatLinkedTo } from "@/lib/sessionStatus";
import { prefillFromNotebookSession } from "@/lib/sessionRecreate";
import { statusBadgeClass } from "@/lib/statusStyles";
import { useToast } from "@/components/shared/Toast";

import { clickableRow } from "@/lib/a11y";

const PROFILE_ORDER: ResourceProfile[] = ["small", "medium", "large", "xlarge", "2xlarge"];

const PROFILE_META: Record<ResourceProfile, { label: string; description: string }> = {
  small: { label: "Small", description: "Exploratory work and light data wrangling" },
  medium: { label: "Medium", description: "General-purpose analysis" },
  large: { label: "Large", description: "Larger datasets with several objects in memory" },
  xlarge: { label: "X Large", description: "Large single-cell datasets, Seurat/scanpy integration" },
  "2xlarge": { label: "XX Large", description: "Very large or multi-sample integration" },
};

export default function NotebooksPage() {
  const toast = useToast();
  const { components } = useComponents();
  const jupyterEnabled = components.some((c) => c.key === "jupyterhub" && c.enabled);
  const rstudioEnabled = components.some((c) => c.key === "rstudio" && c.enabled);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<NotebookSession[]>([]);
  const [bucket, setBucket] = useState<SessionBucket>("active");
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
  const [profileAvailability, setProfileAvailability] = useState<Record<string, boolean>>({});
  const [poolMachineType, setPoolMachineType] = useState<string>("");
  const [profileNotice, setProfileNotice] = useState<string>("");
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [scopeType, setScopeType] = useState<"experiment" | "project">("experiment");
  const [selectedExperiment, setSelectedExperiment] = useState<number | null>(null);
  const [selectedProject, setSelectedProject] = useState<number | null>(null);
  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [selectedEnvId, setSelectedEnvId] = useState<number | null>(null);
  const [selectedEnvDetail, setSelectedEnvDetail] = useState<EnvironmentDetailResponse | null>(null);
  const [selectedVersionImageUri, setSelectedVersionImageUri] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [stoppingSessions, setStoppingSessions] = useState<Set<number>>(new Set());
  const [provenance, setProvenance] = useState<SessionProvenance | null>(null);
  const [showFileSelector, setShowFileSelector] = useState(false);
  const [experimentFiles, setExperimentFiles] = useState<FileResponse[]>([]);
  const [sampleNames, setSampleNames] = useState<Record<number, string>>({});
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [activeBranchCount, setActiveBranchCount] = useState(0);
  const [showGuide, setShowGuide] = useState(false);
  const [pendingLaunch, setPendingLaunch] = useState<SessionType | null>(null);

  useEffect(() => {
    loadSessions(bucket);
    loadExperiments();
    loadProjects();
    loadBuildStatus();
    loadEnvironments();
    loadResourceProfiles();
  }, [bucket]);

  useEffect(() => {
    const hasStarting = sessions.some((s) => s.status === "starting");
    if (!hasStarting) return;
    const interval = setInterval(() => loadSessions(bucket), 10000);
    return () => clearInterval(interval);
  }, [sessions, bucket]);

  async function loadResourceProfiles() {
    try {
      const data = await api.get<{
        pool_machine_type: string;
        profiles: { name: ResourceProfile; available: boolean }[];
      }>("/api/v1/notebooks/resource-profiles");
      if (data?.profiles) {
        setPoolMachineType(data.pool_machine_type);
        setProfileAvailability(
          Object.fromEntries(data.profiles.map((p) => [p.name, p.available]))
        );
      }
    } catch {}
  }

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

  async function loadSessions(currentBucket: SessionBucket = bucket) {
    try {
      const data = await api.get<SessionListResponse>(
        `/api/v1/notebooks/sessions?bucket=${currentBucket}`,
      );
      setSessions(data.sessions);
      setLoadError(null);
    } catch (e) {
      // These sessions bill while they run, so "you have none" must never stand
      // in for "we could not ask".
      setLoadError(e instanceof Error ? e.message : "Could not load notebook sessions.");
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
      const data = await api.get<EnvironmentListResponse>("/api/v1/environments?type=notebook");
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
    setSelectedVersionId(null);
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${envId}`);
      setSelectedEnvDetail(detail);
      const readyVersion = detail.versions.find((v) => v.status === "ready" && v.image_uri);
      if (readyVersion) {
        setSelectedVersionImageUri(readyVersion.image_uri);
        setSelectedVersionId(readyVersion.id);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not change the environment.");
    }
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

  async function handleRecreateSession(source: NotebookSession) {
    const prefill = prefillFromNotebookSession(source);
    setLaunchError(null);
    setPendingLaunch(prefill.session_type);
    setSelectedProfile(prefill.resource_profile);
    setScopeType(prefill.scope_type);
    setSelectedExperiment(prefill.experiment_id);
    setSelectedProject(prefill.project_id);
    if (prefill.environment_version_id) {
      // Try to resolve the environment that owns this version so the
      // environment selector renders the right name.
      for (const env of environments) {
        if (env.latest_version?.id === prefill.environment_version_id) {
          setSelectedEnvId(env.id);
          break;
        }
      }
      setSelectedVersionId(prefill.environment_version_id);
    }
    setSelectedFileIds(prefill.input_file_ids);
    if (prefill.scope_type === "experiment" && prefill.experiment_id) {
      try {
        await loadFilesForExperiment(prefill.experiment_id);
        // loadFilesForExperiment clears selectedFileIds; restore the snapshot.
        setSelectedFileIds(prefill.input_file_ids);
      } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not recreate the session.");
    }
    }
    setShowLaunchModal(true);
    setViewingSession(null);
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
            names[s.id] = s.external_id || `Sample ${s.id}`;
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

  function handleLaunch(sessionType: SessionType) {
    if (selectedFileIds.length === 0) {
      setPendingLaunch(sessionType);
      return;
    }
    void performLaunch(sessionType);
  }

  async function performLaunch(sessionType: SessionType) {
    setPendingLaunch(null);
    setLaunching(true);
    setLaunchError(null);
    try {
      const req: SessionLaunchRequest = {
        session_type: sessionType,
        resource_profile: selectedProfile,
        experiment_id: scopeType === "experiment" ? selectedExperiment : undefined,
        image_uri: selectedVersionImageUri,
        input_file_ids: selectedFileIds.length > 0 ? selectedFileIds : undefined,
        environment_version_id: selectedVersionId,
      };
      await api.post("/api/v1/notebooks/sessions", req);
      setShowLaunchModal(false);
      loadSessions(bucket);
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : "Failed to launch session");
    } finally {
      setLaunching(false);
    }
  }

  async function handleStop(sessionId: number) {
    if (!confirm("Stop this notebook session? Files in /outputs/ will be synced to GCS before shutdown. This may take a few minutes for large files.")) return;
    setStoppingSessions((prev) => new Set(prev).add(sessionId));
    try {
      await api.post(`/api/v1/notebooks/sessions/${sessionId}/stop`);
      loadSessions(bucket);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not stop the session. It may still be running and billing.");
    } finally {
      setStoppingSessions((prev) => {
        const next = new Set(prev);
        next.delete(sessionId);
        return next;
      });
    }
  }

  return (
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

      {/* Quick Start Guide */}
      <div className="mb-6">
        <button
          onClick={() => setShowGuide(!showGuide)}
          className="inline-flex items-center gap-1.5 text-sm text-bioaf-600 hover:text-bioaf-700"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          How notebook sessions work
        </button>
        {showGuide && (
          <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-4">
            <div className="text-sm text-blue-800 space-y-2">
              <ul className="space-y-1.5 text-blue-700">
                <li><strong>Input files</strong> are mounted at <code className="bg-blue-100 px-1 rounded">/data/</code>, organized by project, experiment, sample, and pipeline. Select files when launching a session.</li>
                <li><strong>Output files</strong> should be saved to <code className="bg-blue-100 px-1 rounded">/outputs/</code>. Everything in this directory is automatically synced to GCS and registered when you stop the session.</li>
                <li><strong>Environments</strong> control the packages available in your session. Choose an environment and version when launching. Admins can create and build new environments from the <a href="/environments" className="underline font-medium">Environments</a> page.</li>
                <li><strong>Git integration</strong> can be configured per-session via SSH keys in your <a href="/profile" className="underline font-medium">Profile Settings</a>. Notebooks are auto-committed every 15 minutes when a git repo is configured.</li>
                <li><strong>Session credentials</strong> (username and password for RStudio) are set in your <a href="/profile" className="underline font-medium">Profile Settings</a>.</li>
              </ul>
            </div>
          </div>
        )}
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
        <div className="p-6 border-b flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold">Sessions</h2>
          <SessionBucketFilter value={bucket} onChange={setBucket} />
        </div>

        {loading ? (
          <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Linked to</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resources</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Start Time</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access URL</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {sessions.map((s) => (
                <tr key={s.id} className={`cursor-pointer ${s.status === "idle" ? "bg-yellow-50 hover:bg-yellow-100" : "hover:bg-gray-50"}`} {...clickableRow(() => setViewingSession(s))}>
                  <td className="px-4 py-3 text-sm capitalize font-medium">{s.session_type}</td>
                  <td className="px-4 py-3 text-sm">{s.user?.name || s.user?.email || "\u2014"}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatLinkedTo({ experiment: s.experiment, project: s.project }) ?? "\u2014"}</td>
                  <td className="px-4 py-3 text-sm">{s.cpu_cores} CPU / {s.memory_gb} GB</td>
                  <td className="px-4 py-3">
                    {stoppingSessions.has(s.id) ? (
                      <span className="flex items-center gap-1 text-xs text-orange-700">
                        <LoadingSpinner size="sm" />
                        Syncing outputs to GCS...
                      </span>
                    ) : s.status === "starting" ? (
                      <span className="flex items-center gap-1 text-xs text-blue-700">
                        <LoadingSpinner size="sm" />
                        Starting... this may take a few minutes
                      </span>
                    ) : (
                      <span className={`text-xs px-2 py-1 rounded ${statusBadgeClass("computeSession", s.status)}`}>
                        {formatSessionStatusLabel({ status: s.status, failure_reason: s.failure_reason })}
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
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-2">
                      {s.proxy_url && s.status === "running" && (
                        <a href={s.proxy_url} target="_blank" rel="noopener noreferrer" className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50">
                          Open
                        </a>
                      )}
                      {["pending", "starting", "running", "idle"].includes(s.status) && (
                        <button onClick={() => handleStop(s.id)} className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50">
                          Stop
                        </button>
                      )}
                      <button
                        onClick={() => handleRecreateSession(s)}
                        className="text-xs px-2 py-1 border border-green-600 text-green-700 rounded hover:bg-green-50"
                      >
                        Recreate
                      </button>
                      <button
                        onClick={() => setViewingSession(s)}
                        className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50"
                      >
                        Details
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {loadError ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center">
                    <p className="text-red-700 mb-3">Could not load notebook sessions. {loadError}</p>
                    <button
                      type="button"
                      onClick={() => loadSessions()}
                      className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
                    >
                      Retry
                    </button>
                  </td>
                </tr>
              ) : null}
              {sessions.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-400">No active sessions</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Session Detail Modal */}
      {viewingSession && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => { setViewingSession(null); setProvenance(null); }}>
          <div className="bg-white rounded-lg shadow-xl w-[600px] max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="p-6 border-b flex items-center justify-between">
              <h3 className="text-lg font-semibold">
                {viewingSession.session_type.charAt(0).toUpperCase() + viewingSession.session_type.slice(1)} Session
              </h3>
              <button onClick={() => { setViewingSession(null); setProvenance(null); }} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
            </div>
            <div className="p-6 space-y-3">
              {viewingSession.status === "failed" && viewingSession.failure_message && (
                <div className="bg-red-50 border border-red-200 rounded p-3 text-xs text-red-700 mb-2">
                  <div className="font-medium mb-1">
                    {formatSessionStatusLabel({ status: viewingSession.status, failure_reason: viewingSession.failure_reason })}
                  </div>
                  <div className="font-mono whitespace-pre-wrap break-words">{viewingSession.failure_message}</div>
                </div>
              )}
              {(() => {
                // Resolve environment name from loaded environments
                let envLabel: string | null = null;
                if (viewingSession.environment_version_id) {
                  for (const env of environments) {
                    const v = env.latest_version;
                    if (v && v.id === viewingSession.environment_version_id) {
                      envLabel = `${env.name} v${v.version_number}.${v.build_number}`;
                      break;
                    }
                  }
                  if (!envLabel) {
                    envLabel = `Version ID ${viewingSession.environment_version_id}`;
                  }
                }
                // Compute uptime
                let uptimeLabel: string | null = null;
                if (viewingSession.started_at && viewingSession.status === "running") {
                  const diff = Date.now() - new Date(viewingSession.started_at).getTime();
                  const hours = Math.floor(diff / 3600000);
                  const minutes = Math.floor((diff % 3600000) / 60000);
                  uptimeLabel = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
                }
                return [
                  { label: "Type", value: viewingSession.session_type },
                  { label: "Status", value: formatSessionStatusLabel({ status: viewingSession.status, failure_reason: viewingSession.failure_reason }) },
                  { label: "User", value: viewingSession.user?.name || viewingSession.user?.email },
                  { label: "Environment", value: envLabel },
                  { label: "Resources", value: `${viewingSession.cpu_cores} CPU / ${viewingSession.memory_gb} GB RAM` },
                  { label: "Disk Size", value: viewingSession.requested_disk_gb != null ? `${viewingSession.requested_disk_gb} GB` : null },
                  { label: "Linked to", value: formatLinkedTo({ experiment: viewingSession.experiment, project: viewingSession.project }) },
                  { label: "Started", value: viewingSession.started_at ? new Date(viewingSession.started_at).toLocaleString() : null },
                  { label: "Uptime", value: uptimeLabel },
                  { label: "Access URL", value: viewingSession.proxy_url || null },
                  { label: "Idle Since", value: viewingSession.idle_since ? new Date(viewingSession.idle_since).toLocaleString() : null },
                  { label: "Git Branch", value: viewingSession.git_branch_name || null },
                  { label: "Git Commit", value: viewingSession.git_commit_hash || null },
                ];
              })().filter((f) => f.value != null).map((f) => (
                <div key={f.label} className="flex justify-between text-sm">
                  <span className="text-gray-500">{f.label}</span>
                  <span className="font-medium">{String(f.value)}</span>
                </div>
              ))}

              {/* Provenance section for stopped sessions */}
              {viewingSession.status === "stopped" && (
                <div className="mt-4 pt-4 border-t">
                  {!provenance ? (
                    <button
                      onClick={async () => {
                        try {
                          const p = await api.get<SessionProvenance>(`/api/v1/notebooks/sessions/${viewingSession.id}/provenance`);
                          setProvenance(p);
                        } catch {}
                      }}
                      className="text-sm text-bioaf-600 hover:underline"
                    >
                      View provenance
                    </button>
                  ) : (
                    <div className="space-y-3">
                      <h4 className="text-sm font-semibold text-gray-700">Provenance</h4>
                      {provenance.environment && (
                        <div>
                          <p className="text-xs text-gray-500 mb-1">Environment</p>
                          <p className="text-sm">
                            {provenance.environment.environment_name} v{provenance.environment.version_number}.{provenance.environment.build_number}
                            <span className="text-gray-400 ml-1">({provenance.environment.definition_format})</span>
                          </p>
                        </div>
                      )}
                      {provenance.input_files.length > 0 && (
                        <div>
                          <p className="text-xs text-gray-500 mb-1">Input files ({provenance.input_files.length})</p>
                          <ul className="text-sm space-y-0.5 max-h-32 overflow-y-auto">
                            {provenance.input_files.map((f) => (
                              <li key={f.id} className="text-gray-700 truncate" title={f.storage_uri}>{f.filename}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {provenance.output_files.length > 0 && (
                        <div>
                          <p className="text-xs text-gray-500 mb-1">Output files ({provenance.output_files.length})</p>
                          <ul className="text-sm space-y-0.5 max-h-32 overflow-y-auto">
                            {provenance.output_files.map((f) => (
                              <li key={f.id} className="text-gray-700 truncate" title={f.storage_uri}>{f.filename}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {provenance.input_files.length === 0 && provenance.output_files.length === 0 && (
                        <p className="text-sm text-gray-400">No input or output files recorded</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="p-4 border-t bg-gray-50 flex gap-2 justify-end">
              {viewingSession.proxy_url && viewingSession.status === "running" && (
                <a href={viewingSession.proxy_url} target="_blank" rel="noopener noreferrer" className="px-3 py-1.5 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50">
                  Open
                </a>
              )}
              {["pending", "starting", "running", "idle"].includes(viewingSession.status) && (
                <button onClick={() => { handleStop(viewingSession.id); setViewingSession(null); setProvenance(null); }} className="px-3 py-1.5 border border-red-600 text-red-600 rounded text-sm hover:bg-red-50">
                  Stop
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Launch Modal */}
      {showLaunchModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-[800px] max-h-[85vh] flex flex-col">
            <div className="p-6 border-b flex items-center justify-between shrink-0">
              <h3 className="text-lg font-semibold">Launch Notebook Session</h3>
              <button onClick={() => setShowLaunchModal(false)} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
            </div>
            <div className="p-6 space-y-5 overflow-y-auto flex-1">
              {/* Resource Profile */}
              <div>
                <label className="text-sm text-gray-500 mb-2 block">Resource Profile</label>
                <div className="space-y-2">
                  {PROFILE_ORDER.map((profile) => {
                    const specs = RESOURCE_PROFILES[profile];
                    const meta = PROFILE_META[profile];
                    const selected = selectedProfile === profile;
                    // Unknown availability (e.g. before the fetch resolves) defaults to
                    // enabled so the picker never blocks the supported tiers.
                    const available = profileAvailability[profile] !== false;
                    const onProfileClick = () => {
                      if (available) {
                        setSelectedProfile(profile);
                        setProfileNotice("");
                      } else {
                        setProfileNotice(
                          `${meta.label} (${specs.cpu} CPU / ${specs.memory} GB) needs a larger interactive pool than the current ${poolMachineType || "pool"}. Ask your admin to increase the interactive pool machine type in Infrastructure > Components.`
                        );
                      }
                    };
                    return (
                      <button
                        key={profile}
                        type="button"
                        onClick={onProfileClick}
                        aria-pressed={selected}
                        aria-disabled={!available}
                        className={`w-full text-left p-3 border rounded-lg transition-colors ${
                          !available
                            ? "border-gray-200 bg-gray-50 opacity-60 cursor-not-allowed"
                            : selected
                            ? "border-bioaf-500 bg-bioaf-50"
                            : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          <span className="font-semibold flex items-center gap-2">
                            {meta.label}
                            {!available && (
                              <span className="text-[10px] font-medium uppercase tracking-wide text-gray-400 border border-gray-300 rounded px-1.5 py-0.5">
                                Admin upgrade required
                              </span>
                            )}
                          </span>
                          <span className="text-xs text-gray-500">{specs.cpu} CPU / {specs.memory} GB RAM</span>
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5">{meta.description}</div>
                      </button>
                    );
                  })}
                </div>
                {profileNotice && (
                  <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2 mt-2">
                    {profileNotice}
                  </p>
                )}
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
                      value={selectedVersionId || ""}
                      onChange={(e) => {
                        const vid = e.target.value ? Number(e.target.value) : null;
                        setSelectedVersionId(vid);
                        const v = selectedEnvDetail?.versions.find((ver) => ver.id === vid);
                        setSelectedVersionImageUri(v?.image_uri || null);
                      }}
                      className="border rounded px-3 py-2 text-sm flex-1"
                    >
                      {selectedEnvDetail.versions
                        .filter((v) => v.status === "ready" && v.image_uri)
                        .map((v) => (
                          <option key={v.id} value={v.id}>
                            v{v.version_number}.{v.build_number} ({v.definition_format})
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
                    aria-label="Experiment"
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
                    aria-label="Project"
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

              {scopeType === "experiment" && selectedExperiment && (
                <div>
                  <label className="text-sm text-gray-500 mb-2 block">Input Files</label>
                  {experimentFiles.length === 0 ? (
                    <p className="text-xs text-gray-400">
                      No files found for this experiment.
                    </p>
                  ) : (
                    <FileTreeSelector
                      files={experimentFiles}
                      sampleNames={sampleNames}
                      onSelectionChange={setSelectedFileIds}
                    />
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
                  {launchError.toLowerCase().includes("session credentials") && (
                    <p className="text-sm text-red-800 mt-1">
                      <Link href="/profile" className="underline font-medium">
                        Go to Profile Settings
                      </Link>{" "}
                      to configure your session credentials.
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* Launch buttons */}
            <div className="p-6 border-t bg-gray-50 flex gap-3 shrink-0">
              {rstudioEnabled && (
                <button
                  onClick={() => handleLaunch("rstudio")}
                  disabled={launching}
                  className="flex-1 bg-bioaf-600 text-white px-6 py-2.5 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
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

      {pendingLaunch && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Launch without inputs"
          className="fixed inset-0 bg-black/40 z-[60] flex items-center justify-center"
        >
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
            <h3 className="text-lg font-semibold mb-2">Launch without inputs?</h3>
            <p className="text-sm text-gray-600 mb-3">
              You haven&apos;t added the following to this session:
            </p>
            <ul className="text-sm text-gray-700 list-disc list-inside mb-4">
              <li>No input files attached to /data/</li>
            </ul>
            <p className="text-sm text-gray-600 mb-4">
              You can launch without them, or go back and add them now.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingLaunch(null)}
                className="px-4 py-2 border rounded-md text-sm text-gray-700 hover:bg-gray-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => performLaunch(pendingLaunch)}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700"
              >
                Launch anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
