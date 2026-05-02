"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type {
  EnvironmentResponse,
  EnvironmentListResponse,
  EnvironmentDetailResponse,
  EnvironmentVersionSummary,
  EnvironmentVersionResponse,
  VersionCreateRequest,
  BuildLogsResponse,
} from "@/lib/types";

const DEFAULT_PIPELINE_CONDA_YML = `name: bioaf-pipeline
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - numpy
  - pandas
  - scipy
  - matplotlib
  - scikit-learn
  - pip
`;

const POLL_INTERVAL_MS = 5000;

type Tab = "versions" | "new-version";

const statusBadgeClass: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  building: "bg-yellow-100 text-yellow-700",
  ready: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const statusDotClass: Record<string, string> = {
  draft: "bg-gray-400",
  building: "bg-yellow-500",
  ready: "bg-green-500",
  failed: "bg-red-500",
};

const statusLabel: Record<string, string> = {
  draft: "Draft",
  building: "Building",
  ready: "Ready",
  failed: "Failed",
};

export default function PipelineEnvironmentsPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canCreate = user?.role_name === "admin" || user?.role_name === "comp_bio";
  const canBuild = user?.role_name === "admin" || user?.role_name === "comp_bio";
  const canDelete = user?.role_name === "admin" || user?.role_name === "comp_bio";

  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedEnv, setSelectedEnv] = useState<EnvironmentDetailResponse | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("versions");

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: "",
    description: "",
    definition_content: DEFAULT_PIPELINE_CONDA_YML,
  });
  const [creating, setCreating] = useState(false);

  const [newVersionContent, setNewVersionContent] = useState(DEFAULT_PIPELINE_CONDA_YML);
  const [creatingVersion, setCreatingVersion] = useState(false);

  const [selectedVersion, setSelectedVersion] = useState<EnvironmentVersionResponse | null>(null);
  const [buildLogs, setBuildLogs] = useState<BuildLogsResponse | null>(null);

  const [showDeleteVersionModal, setShowDeleteVersionModal] =
    useState<EnvironmentVersionSummary | null>(null);
  const [deletingVersion, setDeletingVersion] = useState(false);

  const selectedEnvIdRef = useRef<number | null>(null);
  const selectedVersionIdRef = useRef<number | null>(null);

  useEffect(() => {
    selectedEnvIdRef.current = selectedEnv?.id ?? null;
  }, [selectedEnv]);

  useEffect(() => {
    selectedVersionIdRef.current = selectedVersion?.id ?? null;
  }, [selectedVersion]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadEnvironments();
  }, [router]);

  // Status polling: while any environment (or the open detail) has a build in
  // progress, refresh in the background so the UI reflects the new status.
  useEffect(() => {
    const listHasBuilding = environments.some((e) => e.latest_version?.status === "building");
    const detailHasBuilding =
      selectedEnv?.versions.some((v) => v.status === "building") ?? false;
    const versionIsBuilding = selectedVersion?.status === "building";

    if (!listHasBuilding && !detailHasBuilding && !versionIsBuilding) return;

    const interval = setInterval(() => {
      void loadEnvironments(true);
      const envId = selectedEnvIdRef.current;
      if (envId !== null) {
        void refreshSelectedEnv(envId);
      }
      const versionId = selectedVersionIdRef.current;
      if (envId !== null && versionId !== null) {
        void refreshSelectedVersion(envId, versionId);
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [environments, selectedEnv, selectedVersion]);

  async function loadEnvironments(silent = false) {
    try {
      const data = await api.get<EnvironmentListResponse>(
        "/api/v1/environments?type=pipeline",
      );
      setEnvironments(data.environments);
      setLoadError(null);
    } catch (err) {
      if (!silent) {
        setLoadError(err instanceof Error ? err.message : "Failed to load environments");
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }

  async function refreshSelectedEnv(id: number) {
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${id}`);
      setSelectedEnv((prev) => (prev && prev.id === id ? detail : prev));
    } catch {}
  }

  async function refreshSelectedVersion(envId: number, versionId: number) {
    try {
      const version = await api.get<EnvironmentVersionResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}`,
      );
      setSelectedVersion((prev) => (prev && prev.id === versionId ? version : prev));
    } catch {}
  }

  async function selectEnvironment(id: number) {
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${id}`);
      setSelectedEnv(detail);
      setActiveTab("versions");
      setSelectedVersion(null);
      setBuildLogs(null);
      setNewVersionContent(DEFAULT_PIPELINE_CONDA_YML);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to load environment");
    }
  }

  async function handleCreate() {
    setCreating(true);
    try {
      const env = await api.post<EnvironmentResponse>("/api/v1/environments", {
        name: createForm.name,
        description: createForm.description || undefined,
        environment_type: "pipeline",
      });
      // Seed an initial version with the conda YAML the user provided.
      if (createForm.definition_content.trim()) {
        const versionBody: VersionCreateRequest = {
          definition_format: "conda",
          definition_content: createForm.definition_content,
        };
        await api.post(`/api/v1/environments/${env.id}/versions`, versionBody);
      }
      setShowCreateModal(false);
      setCreateForm({
        name: "",
        description: "",
        definition_content: DEFAULT_PIPELINE_CONDA_YML,
      });
      await loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create environment");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this environment and all its versions?")) return;
    try {
      await api.delete(`/api/v1/environments/${id}`);
      setSelectedEnv(null);
      await loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete");
    }
  }

  async function handleCreateVersion() {
    if (!selectedEnv) return;
    setCreatingVersion(true);
    try {
      const body: VersionCreateRequest = {
        definition_format: "conda",
        definition_content: newVersionContent,
      };
      await api.post(`/api/v1/environments/${selectedEnv.id}/versions`, body);
      setNewVersionContent(DEFAULT_PIPELINE_CONDA_YML);
      setActiveTab("versions");
      await selectEnvironment(selectedEnv.id);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create version");
    } finally {
      setCreatingVersion(false);
    }
  }

  async function handleBuild(envId: number, versionId: number) {
    if (!confirm("Start building this version? This submits a Cloud Build job.")) return;
    try {
      await api.post(`/api/v1/environments/${envId}/versions/${versionId}/build`);
      await selectEnvironment(envId);
      await loadEnvironments(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Build failed to start");
    }
  }

  async function handleRebuild(envId: number, versionId: number) {
    if (!confirm("Rebuild this version? A new build will be created with the same definition.")) return;
    try {
      await api.post(`/api/v1/environments/${envId}/versions/${versionId}/rebuild`);
      await selectEnvironment(envId);
      await loadEnvironments(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Rebuild failed to start");
    }
  }

  async function loadVersionDetail(envId: number, versionId: number) {
    try {
      const version = await api.get<EnvironmentVersionResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}`,
      );
      setSelectedVersion(version);
      const logs = await api.get<BuildLogsResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}/logs`,
      );
      setBuildLogs(logs);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to load version detail");
    }
  }

  async function handleDeleteVersion(envId: number, version: EnvironmentVersionSummary) {
    setDeletingVersion(true);
    try {
      await api.delete(`/api/v1/environments/${envId}/versions/${version.id}`);
      setShowDeleteVersionModal(null);
      await selectEnvironment(envId);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete version");
    } finally {
      setDeletingVersion(false);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : (
            <>
              {loadError && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
                  {loadError}
                  <button onClick={() => loadEnvironments()} className="ml-2 underline">
                    Retry
                  </button>
                </div>
              )}

              <div className="flex items-center justify-between mb-6">
                <div>
                  <h1 className="text-2xl font-bold">Pipeline Environments</h1>
                  <p className="text-sm text-gray-500 mt-1">
                    Conda environments used by custom pipeline wrappers.
                  </p>
                </div>
                {!selectedEnv && canCreate && (
                  <button
                    onClick={() => setShowCreateModal(true)}
                    className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                  >
                    New Pipeline Environment
                  </button>
                )}
              </div>

              {!selectedEnv ? (
                <div className="bg-white rounded-lg shadow overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-gray-500 uppercase text-xs tracking-wide">
                      <tr>
                        <th className="px-4 py-3 text-left font-medium">Name</th>
                        <th className="px-4 py-3 text-left font-medium">Latest Version</th>
                        <th className="px-4 py-3 text-left font-medium">Status</th>
                        <th className="px-4 py-3 text-left font-medium">Created By</th>
                        <th className="px-4 py-3 text-left font-medium">Updated At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {environments.map((env) => (
                        <tr
                          key={env.id}
                          onClick={() => selectEnvironment(env.id)}
                          className="border-t hover:bg-gray-50 cursor-pointer"
                        >
                          <td className="px-4 py-3">
                            <div className="font-medium text-gray-900">{env.name}</div>
                            {env.description && (
                              <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">
                                {env.description}
                              </div>
                            )}
                          </td>
                          <td className="px-4 py-3 font-mono text-gray-700">
                            {env.latest_version
                              ? `v${env.latest_version.version_number}`
                              : "--"}
                          </td>
                          <td className="px-4 py-3">
                            {env.latest_version ? (
                              <span
                                className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs rounded-full ${
                                  statusBadgeClass[env.latest_version.status] ||
                                  "bg-gray-100 text-gray-700"
                                }`}
                              >
                                <span
                                  className={`w-1.5 h-1.5 rounded-full ${
                                    statusDotClass[env.latest_version.status] || "bg-gray-400"
                                  }`}
                                />
                                {statusLabel[env.latest_version.status] || env.latest_version.status}
                              </span>
                            ) : (
                              <span className="text-xs text-gray-400">No versions</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-gray-700">
                            {env.created_by?.name || env.created_by?.email || "--"}
                          </td>
                          <td className="px-4 py-3 text-gray-500">
                            {new Date(env.updated_at).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                      {environments.length === 0 && (
                        <tr>
                          <td colSpan={5} className="px-4 py-12 text-center text-gray-400">
                            No pipeline environments yet. Create one to get started.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ) : selectedVersion ? (
                <div>
                  <button
                    onClick={() => {
                      setSelectedVersion(null);
                      setBuildLogs(null);
                    }}
                    className="text-sm text-bioaf-600 mb-4 hover:underline"
                  >
                    &larr; Back to {selectedEnv.name}
                  </button>
                  <div className="bg-white rounded-lg shadow">
                    <div className="p-6 border-b">
                      <div className="flex items-center justify-between">
                        <div>
                          <h2 className="text-xl font-bold">
                            {selectedEnv.name} v{selectedVersion.version_number}
                          </h2>
                          <div className="flex items-center gap-2 mt-1">
                            <span
                              className={`w-2 h-2 rounded-full ${
                                statusDotClass[selectedVersion.status] || "bg-gray-400"
                              }`}
                            />
                            <span className="text-sm text-gray-500">
                              {statusLabel[selectedVersion.status] || selectedVersion.status} -{" "}
                              {selectedVersion.definition_format}
                            </span>
                            {selectedVersion.image_uri && (
                              <span className="text-xs text-gray-400 font-mono ml-2">
                                {selectedVersion.image_uri}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {canBuild &&
                            (selectedVersion.status === "draft" ||
                              selectedVersion.status === "failed") && (
                              <button
                                onClick={() => handleBuild(selectedEnv.id, selectedVersion.id)}
                                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                              >
                                Build Image
                              </button>
                            )}
                          {canBuild && selectedVersion.status === "ready" && (
                            <button
                              onClick={() => handleRebuild(selectedEnv.id, selectedVersion.id)}
                              className="border border-bioaf-600 text-bioaf-600 px-4 py-2 rounded-md text-sm hover:bg-bioaf-50"
                            >
                              Rebuild
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="p-6">
                      <h3 className="font-medium mb-2">environment.yml</h3>
                      <pre className="bg-gray-50 border rounded p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap max-h-96">
                        {selectedVersion.definition_content}
                      </pre>
                      {buildLogs && buildLogs.build_id && (
                        <div className="mt-4">
                          <h3 className="font-medium mb-2">Build Info</h3>
                          <div className="text-sm text-gray-500 space-y-1">
                            <p>
                              Build ID: <span className="font-mono">{buildLogs.build_id}</span>
                            </p>
                            {buildLogs.logs_url && (
                              <p>
                                <a
                                  href={buildLogs.logs_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-bioaf-600 hover:underline"
                                >
                                  View build logs in Cloud Console
                                </a>
                              </p>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div>
                  <button
                    onClick={() => setSelectedEnv(null)}
                    className="text-sm text-bioaf-600 mb-4 hover:underline"
                  >
                    &larr; Back to pipeline environments
                  </button>
                  <div className="bg-white rounded-lg shadow">
                    <div className="p-6 border-b">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <h2 className="text-xl font-bold">{selectedEnv.name}</h2>
                            <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700">
                              Pipeline
                            </span>
                          </div>
                          {selectedEnv.description && (
                            <p className="text-sm text-gray-500 mt-1">{selectedEnv.description}</p>
                          )}
                        </div>
                        {canDelete && (
                          <button
                            onClick={() => handleDelete(selectedEnv.id)}
                            className="text-red-500 text-sm hover:underline"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="border-b px-6 flex gap-4">
                      {(["versions", "new-version"] as Tab[]).map((tab) => (
                        <button
                          key={tab}
                          onClick={() => setActiveTab(tab)}
                          className={`py-3 text-sm ${
                            activeTab === tab
                              ? "border-b-2 border-bioaf-600 text-bioaf-600 font-medium"
                              : "text-gray-500"
                          }`}
                        >
                          {tab === "versions" ? "Versions" : "New Version"}
                        </button>
                      ))}
                    </div>

                    <div className="p-6">
                      {activeTab === "versions" && (
                        <div className="space-y-3">
                          {selectedEnv.versions.map((v) => (
                            <div
                              key={v.id}
                              onClick={() => loadVersionDetail(selectedEnv.id, v.id)}
                              className="flex items-center justify-between p-4 border rounded-md hover:bg-gray-50 cursor-pointer"
                            >
                              <div className="flex items-center gap-3">
                                <span className="font-mono font-medium">
                                  v{v.version_number}
                                </span>
                                <span className="text-xs text-gray-400">
                                  build #{v.build_number}
                                </span>
                                <span
                                  className={`px-2 py-0.5 text-xs rounded-full ${
                                    statusBadgeClass[v.status] || "bg-gray-100 text-gray-700"
                                  }`}
                                >
                                  {statusLabel[v.status] || v.status}
                                </span>
                              </div>
                              <div className="flex items-center gap-3">
                                <span className="text-xs text-gray-400">
                                  {new Date(v.created_at).toLocaleDateString()}
                                </span>
                                {canBuild && (v.status === "draft" || v.status === "failed") && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleBuild(selectedEnv.id, v.id);
                                    }}
                                    className="text-xs text-bioaf-600 hover:underline"
                                  >
                                    Build
                                  </button>
                                )}
                                {canBuild && v.status === "ready" && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleRebuild(selectedEnv.id, v.id);
                                    }}
                                    className="text-xs text-bioaf-600 hover:underline"
                                  >
                                    Rebuild
                                  </button>
                                )}
                                {canDelete && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setShowDeleteVersionModal(v);
                                    }}
                                    className="text-xs text-red-500 hover:underline"
                                  >
                                    Delete
                                  </button>
                                )}
                              </div>
                            </div>
                          ))}
                          {selectedEnv.versions.length === 0 && (
                            <p className="text-center text-gray-400 py-8">
                              No versions yet. Create one in the &quot;New Version&quot; tab.
                            </p>
                          )}
                        </div>
                      )}

                      {activeTab === "new-version" && canCreate && (
                        <div className="space-y-4">
                          <div>
                            <label className="text-sm text-gray-500 block mb-1">
                              environment.yml
                            </label>
                            <textarea
                              value={newVersionContent}
                              onChange={(e) => setNewVersionContent(e.target.value)}
                              rows={18}
                              className="w-full border rounded px-3 py-2 text-sm font-mono"
                            />
                          </div>
                          <button
                            onClick={handleCreateVersion}
                            disabled={creatingVersion || !newVersionContent.trim()}
                            className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
                          >
                            {creatingVersion ? "Creating..." : "Create Version"}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {showCreateModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                  <div className="bg-white rounded-lg shadow-xl p-6 w-[640px] max-h-[90vh] overflow-y-auto">
                    <h3 className="font-semibold text-lg mb-4">New Pipeline Environment</h3>
                    <div className="space-y-3">
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">Name</label>
                        <input
                          value={createForm.name}
                          onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                          placeholder="rnaseq-pipeline"
                          className="w-full border rounded px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">Description</label>
                        <input
                          value={createForm.description}
                          onChange={(e) =>
                            setCreateForm({ ...createForm, description: e.target.value })
                          }
                          placeholder="Optional description"
                          className="w-full border rounded px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">
                          environment.yml
                        </label>
                        <textarea
                          value={createForm.definition_content}
                          onChange={(e) =>
                            setCreateForm({
                              ...createForm,
                              definition_content: e.target.value,
                            })
                          }
                          rows={14}
                          className="w-full border rounded px-3 py-2 text-sm font-mono"
                        />
                        <p className="text-xs text-gray-400 mt-1">
                          The first version is created automatically. You can build it from the
                          versions list.
                        </p>
                      </div>
                    </div>
                    <div className="flex gap-2 mt-6">
                      <button
                        onClick={handleCreate}
                        disabled={
                          creating || !createForm.name || !createForm.definition_content.trim()
                        }
                        className="flex-1 bg-bioaf-600 text-white py-2 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                      >
                        {creating ? "Creating..." : "Create"}
                      </button>
                      <button
                        onClick={() => setShowCreateModal(false)}
                        className="flex-1 border py-2 rounded text-sm"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {showDeleteVersionModal && selectedEnv && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                  <div className="bg-white rounded-lg shadow-xl p-6 w-96">
                    <h3 className="font-semibold text-lg mb-4">Delete Version</h3>
                    <div className="bg-red-50 border border-red-200 rounded p-3 mb-4">
                      <p className="text-sm text-red-800">
                        This will permanently delete{" "}
                        <strong>v{showDeleteVersionModal.version_number}</strong> and its
                        container image. Pipelines pinned to this version will fail to launch.
                      </p>
                    </div>
                    <p className="text-sm text-gray-600 mb-4">This action cannot be undone.</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleDeleteVersion(selectedEnv.id, showDeleteVersionModal)}
                        disabled={deletingVersion}
                        className="flex-1 bg-red-600 text-white py-2 rounded text-sm hover:bg-red-700 disabled:opacity-50"
                      >
                        {deletingVersion ? "Deleting..." : "Delete Version"}
                      </button>
                      <button
                        onClick={() => setShowDeleteVersionModal(null)}
                        className="flex-1 border py-2 rounded text-sm"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
