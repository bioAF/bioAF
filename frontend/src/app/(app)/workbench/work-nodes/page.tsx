"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useConfirm } from "@/hooks/useConfirm";
import { Modal } from "@/components/shared/Modal";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { api, ApiError } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { usePermissions } from "@/hooks/usePermissions";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";
import { resolveWorkNodeProfiles } from "@/lib/workNodeProfiles";
import { SessionBucketFilter, type SessionBucket } from "@/components/shared/SessionBucketFilter";
import { ErrorState } from "@/components/shared/ErrorState";
import { useToast } from "@/components/shared/Toast";
import { formatSessionStatusLabel, formatLinkedTo } from "@/lib/sessionStatus";
import { prefillFromWorkNode } from "@/lib/sessionRecreate";
import type {
  WorkNode,
  WorkNodeListResponse,
  WorkNodeLaunchRequest,
  MachineType,
  Project,
  ProjectListResponse,
  Experiment,
  ExperimentListResponse,
  EnvironmentResponse,
  EnvironmentListResponse,
  EnvironmentDetailResponse,
  GitHubRepo,
  GitHubRepoListResponse,
  FileResponse,
  FileListResponse,
} from "@/lib/types";
import { statusBadgeClass } from "@/lib/statusStyles";

import { clickableRow } from "@/lib/a11y";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

function statusLabel(node: WorkNode): string {
  if (node.status === "stopping") return "Syncing outputs...";
  // Prefer the backend-supplied failure taxonomy (failure_reason). Fall back
  // to the historical "no access_url means resource failure" heuristic for
  // sessions that predate the taxonomy migration.
  if (node.status === "failed" && !node.failure_reason && !node.access_url) {
    return "Resource Failure";
  }
  return formatSessionStatusLabel({ status: node.status, failure_reason: node.failure_reason });
}

const CATEGORY_LABELS: Record<string, string> = {
  standard: "Standard",
  "high-memory": "High Memory",
  gpu: "GPU",
};

export default function WorkNodesPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();

  const [nodes, setNodes] = useState<WorkNode[]>([]);
  const [bucket, setBucket] = useState<SessionBucket>("active");
  const [loading, setLoading] = useState(true);
  const [showLaunch, setShowLaunch] = useState(false);
  useDismissOnEscape(showLaunch, () => { setShowLaunch(false); });
  const [viewingNode, setViewingNode] = useState<WorkNode | null>(null);
  useDismissOnEscape(viewingNode !== null, () => { setViewingNode(null); });

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
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [scopeType, setScopeType] = useState<"experiment" | "project">("experiment");
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedExperimentId, setSelectedExperimentId] = useState<number | null>(null);
  const [experimentFiles, setExperimentFiles] = useState<FileResponse[]>([]);
  const [sampleNames, setSampleNames] = useState<Record<number, string>>({});
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);
  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [selectedEnvId, setSelectedEnvId] = useState<number | null>(null);
  const [envDetail, setEnvDetail] = useState<EnvironmentDetailResponse | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [selectedRepoIds, setSelectedRepoIds] = useState<number[]>([]);
  const [machineTypes, setMachineTypes] = useState<MachineType[]>([]);
  const [selectedMachineType, setSelectedMachineType] = useState<string>("");
  const [showAdvancedMachines, setShowAdvancedMachines] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [showConfirmLaunch, setShowConfirmLaunch] = useState(false);
  const [provisioningNotice, setProvisioningNotice] = useState<
    null | { kind: "info" | "error"; message: string }
  >(null);
  const [stoppingNodes, setStoppingNodes] = useState<Set<number>>(new Set());
  const [showGuide, setShowGuide] = useState(false);

  const workNodeProfiles = resolveWorkNodeProfiles(machineTypes);

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("work_nodes", "view")) { router.push("/dashboard"); return; }
    loadNodes();
    loadRepos();
  }, [router, permLoading, canAccess, bucket]);

  // Auto-refresh while starting
  useEffect(() => {
    const hasInProgress = nodes.some((n) => n.status === "starting" || n.status === "stopping");
    if (!hasInProgress) return;
    const interval = setInterval(() => loadNodes(), 10000);
    return () => clearInterval(interval);
  }, [nodes]);

  const [loadError, setLoadError] = useState<string | null>(null);

  const loadNodes = useCallback(async () => {
    try {
      const data = await api.get<WorkNodeListResponse>(`/api/v1/work-nodes/sessions?bucket=${bucket}`);
      setNodes(data.sessions);
      setLoadError(null);
    } catch (e) {
      // Never fall through to the empty state here: these nodes bill by the hour,
      // and "you have none" is a very different claim from "we could not ask".
      logError("loading work nodes", e);
      setLoadError(loadFailureMessage("Work nodes"));
    } finally {
      setLoading(false);
    }
  }, [bucket]);

  async function loadRepos() {
    try {
      const data = await api.get<GitHubRepoListResponse>("/api/v1/github-repos");
      setRepos(data.repos);
    } catch (e) {
      logError("loading GitHub repositories", e);
      toast.error(loadFailureMessage("Repositories"));
    }
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
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete the repository.");
    }
  }

  async function openLaunchDialog() {
    setShowLaunch(true);
    setLaunchStep(1);
    setScopeType("experiment");
    setSelectedProjectId(null);
    setSelectedExperimentId(null);
    setExperimentFiles([]);
    setSampleNames({});
    setSelectedFileIds([]);
    setSelectedEnvId(null);
    setEnvDetail(null);
    setSelectedVersionId(null);
    setSelectedRepoIds([]);
    setSelectedMachineType("");
    setShowAdvancedMachines(false);
    setLaunchError(null);

    try {
      const [projectData, expData, mtData, envData] = await Promise.all([
        api.get<ProjectListResponse>("/api/projects?page_size=100"),
        api.get<ExperimentListResponse>("/api/experiments?page_size=100"),
        api.get<MachineType[]>("/api/v1/work-nodes/machine-types"),
        api.get<EnvironmentListResponse>("/api/v1/environments?type=work_node"),
      ]);
      setProjects(projectData.projects);
      setExperiments(expData.experiments);
      setMachineTypes(mtData);
      setEnvironments(envData.environments);
    } catch (e) {
      logError("loading the work node form options", e);
      toast.error(loadFailureMessage("Form options"));
    }
  }

  async function handleRecreateWorkNode(source: WorkNode) {
    const prefill = prefillFromWorkNode(source);
    setLaunchError(null);
    setShowLaunch(true);
    setLaunchStep(1);
    setScopeType("project");
    setSelectedExperimentId(null);
    setSelectedProjectId(prefill.project_id);
    setSelectedMachineType(prefill.machine_type ?? "");
    setSelectedFileIds(prefill.input_file_ids);
    setSelectedRepoIds(prefill.github_repo_ids);
    if (prefill.environment_version_id) {
      // Try to resolve the environment that owns this version so the env
      // selector renders the right card.
      for (const env of environments) {
        if (env.latest_version?.id === prefill.environment_version_id) {
          setSelectedEnvId(env.id);
          break;
        }
      }
      setSelectedVersionId(prefill.environment_version_id);
    }
    setViewingNode(null);
    // Ensure the reference data is loaded even if the user hasn't opened the
    // launch dialog yet this session.
    if (projects.length === 0 || machineTypes.length === 0 || environments.length === 0) {
      try {
        const [projectData, expData, mtData, envData] = await Promise.all([
          api.get<ProjectListResponse>("/api/projects?page_size=100"),
          api.get<ExperimentListResponse>("/api/experiments?page_size=100"),
          api.get<MachineType[]>("/api/v1/work-nodes/machine-types"),
          api.get<EnvironmentListResponse>("/api/v1/environments?type=work_node"),
        ]);
        setProjects(projectData.projects);
        setExperiments(expData.experiments);
        setMachineTypes(mtData);
        setEnvironments(envData.environments);
      } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not recreate the work node.");
    }
    }
  }

  function handleScopeChange(scope: "experiment" | "project") {
    setScopeType(scope);
    setSelectedProjectId(null);
    setSelectedExperimentId(null);
    setExperimentFiles([]);
    setSampleNames({});
    setSelectedFileIds([]);
  }

  function handleProjectSelect(projectId: number) {
    setSelectedProjectId(projectId);
  }

  async function handleExperimentSelect(experimentId: number) {
    const exp = experiments.find((e) => e.id === experimentId);
    setSelectedExperimentId(experimentId);
    setSelectedProjectId(exp?.project?.id ?? null);
    setSelectedFileIds([]);
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
          const samplesData = await api.get<{ samples: { id: number; external_id: string }[] }>(
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
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not select that template.");
    }
  }

  function toggleRepo(repoId: number) {
    setSelectedRepoIds((prev) =>
      prev.includes(repoId) ? prev.filter((id) => id !== repoId) : [...prev, repoId]
    );
  }

  // A work node needs an image + size and a scope selection appropriate to the
  // chosen scope: an experiment (which may have no project) or a project. Matches
  // notebooks, which don't require a project.
  const scopeSelected = scopeType === "experiment" ? !!selectedExperimentId : !!selectedProjectId;

  function handleLaunch() {
    if (!scopeSelected || !selectedVersionId || !selectedMachineType) return;
    const missingFiles = selectedFileIds.length === 0;
    const missingRepos = repos.length > 0 && selectedRepoIds.length === 0;
    if (missingFiles || missingRepos) {
      setShowConfirmLaunch(true);
      return;
    }
    void performLaunch();
  }

  async function performLaunch() {
    if (!scopeSelected || !selectedVersionId || !selectedMachineType) return;
    setShowConfirmLaunch(false);
    setLaunching(true);
    setLaunchError(null);
    const req: WorkNodeLaunchRequest = {
      // project_id may be null for a standalone experiment; experiment_id ties
      // the node (and its outputs) to the experiment, like notebook sessions.
      project_id: selectedProjectId ?? undefined,
      experiment_id: scopeType === "experiment" ? (selectedExperimentId ?? undefined) : undefined,
      environment_version_id: selectedVersionId,
      machine_type: selectedMachineType,
      input_file_ids: selectedFileIds.length > 0 ? selectedFileIds : undefined,
      github_repo_ids: selectedRepoIds.length > 0 ? selectedRepoIds : undefined,
    };
    setShowLaunch(false);
    setProvisioningNotice({
      kind: "info",
      message: "Provisioning work node... this can take 1-3 minutes.",
    });
    try {
      await api.post("/api/v1/work-nodes/sessions", req);
      setProvisioningNotice(null);
      loadNodes();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Launch failed";
      setProvisioningNotice({ kind: "error", message });
    } finally {
      setLaunching(false);
    }
  }

  async function handleStop(nodeId: number) {
    const ok = await confirm({
      title: "Stop this work node?",
      message: "Files in /outputs/ will be synced to GCS. Data in /scratch will be lost.",
      confirmLabel: "Stop",
      variant: "danger",
    });
    if (!ok) return;
    setStoppingNodes((prev) => new Set(prev).add(nodeId));
    try {
      await api.post(`/api/v1/work-nodes/sessions/${nodeId}/stop`);
      loadNodes();
      if (viewingNode?.id === nodeId) setViewingNode(null);
    } catch (e) {
      // A silent failure here leaves a node running and billing while the user
      // believes they stopped it.
      toast.error(e instanceof Error ? e.message : "Could not stop the work node.");
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
      <main className="flex-1 flex items-center justify-center">
        <LoadingSpinner />
      </main>
    );
  }

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Work Nodes</h1>
          <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
            Full Linux VMs with SSH access, for work that does not fit a notebook or a pipeline.
          </p>
        </div>
        {canAccess("work_nodes", "launch") && (
          <button
            onClick={openLaunchDialog}
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
          >
            New Work Node
          </button>
        )}
      </div>

      {provisioningNotice && (
        <div
          role="status"
          className={`mb-4 rounded-lg p-3 flex items-center justify-between gap-3 ${
            provisioningNotice.kind === "error"
              ? "bg-red-50 border border-red-200 text-red-800"
              : "bg-blue-50 border border-blue-200 text-blue-800"
          }`}
        >
          <div className="flex items-center gap-2 text-sm">
            {provisioningNotice.kind === "info" && <LoadingSpinner size="sm" />}
            <span>{provisioningNotice.message}</span>
          </div>
          <button
            type="button"
            onClick={() => setProvisioningNotice(null)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Quick Start Guide */}
      <div className="mb-6">
        <button
          onClick={() => setShowGuide(!showGuide)}
          aria-expanded={showGuide}
          className="inline-flex items-center gap-1.5 text-sm text-bioaf-600 hover:text-bioaf-700"
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
                <li><strong>Work nodes</strong> are full Linux VMs with SSH access. They run the conda software stack you configure on the <a href="/environments" className="underline font-medium">Workbench Templates</a> page.</li>
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
                <input aria-label="Git SSH URL"
                  type="text"
                  value={newRepoUrl}
                  onChange={(e) => setNewRepoUrl(e.target.value)}
                  placeholder="git@github.com:owner/repo.git"
                  className="flex-1 border rounded px-3 py-1.5 text-sm font-mono"
                />
                <input aria-label="Display name (optional)"
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
              <p className="text-xs text-gray-500">No repos configured yet.</p>
            ) : (
              <div className="space-y-1">
                {repos.map((repo) => (
                  <div key={repo.id} className="flex items-center justify-between py-1.5 px-2 bg-gray-50 rounded text-sm">
                    <div>
                      <span className="font-medium">{repo.display_name}</span>
                      <span className="text-gray-500 font-mono text-xs ml-2">{repo.git_ssh_url}</span>
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
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-gray-900">Work Nodes</h2>
        <SessionBucketFilter value={bucket} onChange={setBucket} />
      </div>
      {loadError ? (
        <ErrorState
          message={loadError}
          onRetry={() => {
            setLoading(true);
            loadNodes();
          }}
        />
      ) : nodes.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
          <p className="text-gray-500">
            No work nodes in this view.
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Linked to</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Machine Type</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resources</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Start Time</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-gray-50 cursor-pointer" {...clickableRow(() => setViewingNode(node))}>
                  <td className="px-4 py-3 text-sm">{node.user?.name || node.user?.email || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatLinkedTo({ project: node.project }) ?? NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{node.machine_type || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{node.cpu_cores} CPU / {node.memory_gb} GB</td>
                  <td className="px-4 py-3">
                    {stoppingNodes.has(node.id) ? (
                      <span className="flex items-center gap-1 text-xs text-orange-700">
                        <LoadingSpinner size="sm" />
                        Syncing outputs...
                      </span>
                    ) : (
                      <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClass("computeSession", node.status)}`}>
                        {statusLabel(node)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {node.started_at ? new Date(node.started_at).toLocaleString() : NOT_SET}
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
                    ) : NOT_SET}
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
                      {canAccess("work_nodes", "launch") && (
                        <button
                          onClick={() => handleRecreateWorkNode(node)}
                          className="text-xs px-2 py-1 border border-green-600 text-green-700 rounded hover:bg-green-50"
                        >
                          Recreate
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
        <Modal
          open
          title="Work Node Details"
          onClose={() => setViewingNode(null)}
          size="md"
        >
          <div className="flex justify-between items-center mb-4">
            <button onClick={() => setViewingNode(null)} className="text-gray-500 hover:text-gray-600 text-xl">&times;</button>
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">Status</span>
              <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClass("computeSession", viewingNode.status)}`}>
                {statusLabel(viewingNode)}
              </span>
            </div>
            {viewingNode.status === "failed" && viewingNode.failure_message && (
              <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
                <div className="font-medium mb-1">
                  {formatSessionStatusLabel({ status: viewingNode.status, failure_reason: viewingNode.failure_reason })}
                </div>
                <div className="font-mono whitespace-pre-wrap break-words">{viewingNode.failure_message}</div>
              </div>
            )}
            {viewingNode.status === "failed" && !viewingNode.failure_message && !viewingNode.access_url && (
              <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
                GCP Resources Unavailable -- the VM could not be created. Try again later or choose a different machine type.
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-gray-500">User</span>
              <span>{viewingNode.user?.name || viewingNode.user?.email || NOT_SET}</span>
            </div>
            {formatLinkedTo({ project: viewingNode.project }) && (
              <div className="flex justify-between">
                <span className="text-gray-500">Linked to</span>
                <span>{formatLinkedTo({ project: viewingNode.project })}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-gray-500">Machine Type</span>
              <span>{viewingNode.machine_type || "-"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Resources</span>
              <span>{viewingNode.cpu_cores} CPU / {viewingNode.memory_gb} GB RAM</span>
            </div>
            {viewingNode.requested_disk_gb !== null && viewingNode.requested_disk_gb !== undefined && (
              <div className="flex justify-between">
                <span className="text-gray-500">Disk Size</span>
                <span>{viewingNode.requested_disk_gb} GB</span>
              </div>
            )}
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
                    className="ml-2 text-gray-500 hover:text-white text-xs"
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
              <p className="text-xs text-gray-500 mt-1 text-center">Files in /outputs/ will be synced. Data in /scratch will be lost.</p>
            </div>
          )}
        </Modal>
      )}

      {/* Launch dialog -- 2 steps */}
      {showLaunch && (
        <Modal
          open
          title="Launch Work Node"
          onClose={() => setShowLaunch(false)}
          size="xl"
          footer={
            <>
          {launchStep === 1 ? (
            <button
              type="button"
              onClick={() => setLaunchStep(2)}
              disabled={!scopeSelected || !selectedVersionId || !selectedMachineType}
              className="flex-1 bg-indigo-600 text-white px-6 py-2.5 rounded-md text-sm hover:bg-indigo-700 disabled:opacity-50"
            >
              Next: Review
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setLaunchStep(1)}
                className="px-4 py-2.5 border rounded-md text-sm text-gray-600 hover:bg-gray-100"
              >
                Back
              </button>
              <button
                type="button"
                onClick={handleLaunch}
                disabled={launching}
                className="flex-1 bg-indigo-600 text-white px-6 py-2.5 rounded-md text-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                Launch Work Node
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setShowLaunch(false)}
            className="px-4 py-2.5 border rounded-md text-sm text-gray-600 hover:bg-gray-100"
          >
            Cancel
          </button>
            </>
          }
        >
          {/* Step indicator: 2 steps */}
          <div className="mb-4 flex gap-2">
            {[1, 2].map((s) => (
              <div
                key={s}
                className={`h-1 flex-1 rounded ${s <= launchStep ? "bg-indigo-600" : "bg-gray-200"}`}
              />
            ))}
          </div>
          {launchError && (
            <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
              {launchError}
            </div>
          )}

          {launchStep === 1 && (
            <>
              <section aria-labelledby="wn-machine-profile-heading">
                <h3 id="wn-machine-profile-heading" className="text-sm font-medium text-gray-700 mb-2">
                  Machine Profile
                </h3>
                <div role="group" aria-label="Machine Profile" className="space-y-2">
                  {workNodeProfiles.map((profile) => {
                    const available = profile.machineType !== null;
                    const selected =
                      available && selectedMachineType === profile.machineType?.name;
                    return (
                      <button
                        key={profile.id}
                        type="button"
                        disabled={!available}
                        aria-pressed={selected}
                        onClick={() => {
                          if (profile.machineType) {
                            setSelectedMachineType(profile.machineType.name);
                          }
                        }}
                        className={`w-full text-left p-3 border rounded-lg transition-colors ${
                          !available
                            ? "border-gray-200 bg-gray-50 opacity-60 cursor-not-allowed"
                            : selected
                            ? "border-indigo-500 bg-indigo-50"
                            : "border-gray-200 hover:border-gray-300"
                        }`}
                      >
                        <div className="flex justify-between items-center">
                          <span className="font-semibold text-sm flex items-center gap-2">
                            {profile.label}
                            {!available && (
                              <span className="text-[10px] font-medium uppercase tracking-wide text-gray-500 border border-gray-300 rounded px-1.5 py-0.5">
                                Unavailable
                              </span>
                            )}
                          </span>
                          {profile.machineType && (
                            <span className="text-xs text-gray-500">
                              {profile.machineType.cpu} CPU /{" "}
                              {profile.machineType.memory_gb} GB
                              {profile.machineType.gpu
                                ? ` / ${profile.machineType.gpu}`
                                : ""}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5">
                          {profile.description}
                        </div>
                      </button>
                    );
                  })}
                </div>

                <div className="mt-3">
                  <button
                    type="button"
                    onClick={() => setShowAdvancedMachines(!showAdvancedMachines)}
                    aria-expanded={showAdvancedMachines}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    Advanced: choose a specific machine type{" "}
                    {showAdvancedMachines ? "▼" : "▶"}
                  </button>
                  {showAdvancedMachines && (
                    <div className="mt-2 space-y-3">
                      {Object.entries(
                        machineTypes.reduce<Record<string, MachineType[]>>(
                          (groups, mt) => {
                            (groups[mt.category] = groups[mt.category] || []).push(mt);
                            return groups;
                          },
                          {}
                        )
                      ).map(([category, types]) => (
                        <div key={category}>
                          <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">
                            {CATEGORY_LABELS[category] || category}
                          </h4>
                          <div className="space-y-1">
                            {types.map((mt) => (
                              <button
                                key={mt.name}
                                type="button"
                                onClick={() => setSelectedMachineType(mt.name)}
                                className={`w-full text-left p-2 border rounded-lg text-xs transition-colors ${
                                  selectedMachineType === mt.name
                                    ? "border-indigo-500 bg-indigo-50"
                                    : "border-gray-200 hover:border-gray-300"
                                }`}
                              >
                                <div className="flex justify-between items-center">
                                  <span className="font-mono">{mt.name}</span>
                                  <span className="text-gray-500">
                                    {mt.cpu} CPU / {mt.memory_gb} GB
                                    {mt.gpu ? ` / ${mt.gpu}` : ""}
                                  </span>
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </section>

              <section>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Select Workbench Template</h3>
                {environments.length === 0 ? (
                  <div className="bg-yellow-50 border border-yellow-200 rounded p-3 text-sm text-yellow-700">
                    No work node templates found. Create one from the{" "}
                    <a href="/environments" className="underline font-medium">
                      Workbench Templates
                    </a>{" "}
                    page with type &quot;Work Node&quot;.
                  </div>
                ) : (
                  <div className="flex gap-3">
                    <select
                      aria-label="Workbench template"
                      value={selectedEnvId || ""}
                      onChange={(e) =>
                        e.target.value ? handleEnvSelect(Number(e.target.value)) : null
                      }
                      className="border rounded px-3 py-2 text-sm flex-1"
                    >
                      <option value="">Select a template</option>
                      {environments.map((env) => (
                        <option key={env.id} value={env.id}>
                          {env.name}
                          {env.latest_version
                            ? ` (v${env.latest_version.version_number} - ${env.latest_version.status})`
                            : " (no versions)"}
                        </option>
                      ))}
                    </select>
                    {envDetail &&
                      envDetail.versions.filter(
                        (v) => v.status === "ready" && v.image_uri
                      ).length > 0 && (
                        <select
                          aria-label="Version"
                          value={selectedVersionId || ""}
                          onChange={(e) =>
                            setSelectedVersionId(
                              e.target.value ? Number(e.target.value) : null
                            )
                          }
                          className="border rounded px-3 py-2 text-sm flex-1"
                        >
                          {envDetail.versions
                            .filter((v) => v.status === "ready" && v.image_uri)
                            .map((v) => (
                              <option key={v.id} value={v.id}>
                                v{v.version_number}.{v.build_number} (ready)
                              </option>
                            ))}
                        </select>
                      )}
                  </div>
                )}
              </section>

              <section role="group" aria-label="Link to">
                <h3 className="text-sm font-medium text-gray-700 mb-2">Link to</h3>
                <div className="flex gap-2 mb-2">
                  <button
                    type="button"
                    onClick={() => handleScopeChange("experiment")}
                    aria-pressed={scopeType === "experiment"}
                    className={`px-3 py-1.5 text-sm rounded ${
                      scopeType === "experiment"
                        ? "bg-bioaf-100 text-bioaf-700 font-medium"
                        : "text-gray-600 hover:bg-gray-100"
                    }`}
                  >
                    Experiment
                  </button>
                  <button
                    type="button"
                    onClick={() => handleScopeChange("project")}
                    aria-pressed={scopeType === "project"}
                    className={`px-3 py-1.5 text-sm rounded ${
                      scopeType === "project"
                        ? "bg-bioaf-100 text-bioaf-700 font-medium"
                        : "text-gray-600 hover:bg-gray-100"
                    }`}
                  >
                    Project
                  </button>
                </div>
                {scopeType === "experiment" ? (
                  <select
                    aria-label="Select experiment"
                    value={selectedExperimentId || ""}
                    onChange={(e) =>
                      e.target.value ? handleExperimentSelect(Number(e.target.value)) : null
                    }
                    className="border rounded px-3 py-2 text-sm w-full"
                  >
                    <option value="">No experiment</option>
                    {experiments.map((exp) => (
                      <option key={exp.id} value={exp.id}>
                        {exp.name}
                        {exp.code ? ` (${exp.code})` : ""}
                      </option>
                    ))}
                  </select>
                ) : (
                  <select
                    aria-label="Select project"
                    value={selectedProjectId || ""}
                    onChange={(e) =>
                      e.target.value ? handleProjectSelect(Number(e.target.value)) : null
                    }
                    className="border rounded px-3 py-2 text-sm w-full"
                  >
                    <option value="">No project</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.code ? ` (${p.code})` : ""}
                      </option>
                    ))}
                  </select>
                )}
              </section>

              {scopeType === "experiment" && selectedExperimentId && (
                <section>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Input Files</h3>
                  {experimentFiles.length === 0 ? (
                    <p className="text-xs text-gray-500">
                      No files found for this experiment.
                    </p>
                  ) : (
                    <FileTreeSelector
                      files={experimentFiles}
                      sampleNames={sampleNames}
                      onSelectionChange={setSelectedFileIds}
                    />
                  )}
                </section>
              )}

              <section>
                <h3 className="text-sm font-medium text-gray-700 mb-2">
                  GitHub Repos (optional)
                </h3>
                <p className="text-xs text-gray-500 mb-2">
                  Cloned into ~/repos/ when the node boots.
                </p>
                {repos.length === 0 ? (
                  <p className="text-xs text-gray-500">
                    No repos configured. Add one from the GitHub Repos section below.
                  </p>
                ) : (
                  <div className="space-y-1">
                    {repos.map((repo) => (
                      <label
                        key={repo.id}
                        className="flex items-start gap-3 p-2 border rounded-lg hover:bg-gray-50 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={selectedRepoIds.includes(repo.id)}
                          onChange={() => toggleRepo(repo.id)}
                          className="mt-0.5"
                        />
                        <div>
                          <div className="text-sm font-medium">{repo.display_name}</div>
                          <div className="text-xs text-gray-500 font-mono mt-0.5">
                            {repo.git_ssh_url}
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>
                )}
              </section>
            </>
          )}

          {launchStep === 2 && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-3">Review</h3>
              <div className="space-y-2 text-sm bg-gray-50 rounded-lg p-4">
                <div className="flex justify-between">
                  <span className="text-gray-500">{scopeType === "experiment" ? "Experiment" : "Project"}</span>
                  <span>
                    {scopeType === "experiment"
                      ? experiments.find((e) => e.id === selectedExperimentId)?.name
                      : projects.find((p) => p.id === selectedProjectId)?.name}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Input Files</span>
                  <span>
                    {selectedFileIds.length > 0 ? selectedFileIds.length + " files" : "None"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Template</span>
                  <span>{environments.find((e) => e.id === selectedEnvId)?.name}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Repos</span>
                  <span>
                    {selectedRepoIds.length > 0 ? selectedRepoIds.length + " repos" : "None"}
                  </span>
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
                      <span>
                        {mt.cpu} CPU / {mt.memory_gb} GB
                        {mt.gpu ? ` / ${mt.gpu}` : ""}
                      </span>
                    </div>
                  ) : null;
                })()}
              </div>
            </div>
          )}
        </Modal>
      )}

      {showConfirmLaunch && (
        <Modal
          open
          title="Launch without inputs?"
          onClose={() => setShowConfirmLaunch(false)}
          size="sm"
          footer={
            <>
              <button
                type="button"
                onClick={() => setShowConfirmLaunch(false)}
                className="px-4 py-2 border rounded-md text-sm text-gray-700 hover:bg-gray-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => performLaunch()}
                className="px-4 py-2 bg-indigo-600 text-white rounded-md text-sm hover:bg-indigo-700"
              >
                Launch anyway
              </button>
            </>
          }
        >
          <p className="text-sm text-gray-600 mb-3">
            You haven&apos;t added the following to this work node:
          </p>
          <ul className="text-sm text-gray-700 list-disc list-inside mb-4 space-y-1">
            {selectedFileIds.length === 0 && (
              <li>No input files attached to /data/</li>
            )}
            {repos.length > 0 && selectedRepoIds.length === 0 && (
              <li>No GitHub repos cloned into ~/repos/</li>
            )}
          </ul>
          <p className="text-sm text-gray-600">
            You can launch without them, or go back and add them now.
          </p>
        </Modal>
      )}
    </main>
  );
}
