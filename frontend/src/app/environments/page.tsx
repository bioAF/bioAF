"use client";

import { useEffect, useState } from "react";
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

type Tab = "versions" | "new-version";

export default function EnvironmentsPage() {
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
  const [createForm, setCreateForm] = useState({ name: "", description: "", visibility: "team" });
  const [creating, setCreating] = useState(false);

  // Version creation state
  const [newVersionFormat, setNewVersionFormat] = useState<"dockerfile" | "conda">("dockerfile");
  const [newVersionContent, setNewVersionContent] = useState("");
  const [creatingVersion, setCreatingVersion] = useState(false);

  // Version detail state
  const [selectedVersion, setSelectedVersion] = useState<EnvironmentVersionResponse | null>(null);
  const [buildLogs, setBuildLogs] = useState<BuildLogsResponse | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadEnvironments();
  }, [router]);

  async function loadEnvironments() {
    try {
      const data = await api.get<EnvironmentListResponse>("/api/v1/environments");
      setEnvironments(data.environments);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load environments");
    } finally { setLoading(false); }
  }

  async function selectEnvironment(id: number) {
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/v1/environments/${id}`);
      setSelectedEnv(detail);
      setActiveTab("versions");
      setSelectedVersion(null);
    } catch {}
  }

  async function handleCreate() {
    setCreating(true);
    try {
      await api.post("/api/v1/environments", {
        name: createForm.name,
        description: createForm.description || undefined,
        visibility: createForm.visibility,
      });
      setShowCreateModal(false);
      setCreateForm({ name: "", description: "", visibility: "team" });
      loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create environment");
    } finally { setCreating(false); }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this environment and all its versions?")) return;
    try {
      await api.delete(`/api/v1/environments/${id}`);
      setSelectedEnv(null);
      loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete");
    }
  }

  async function handleCreateVersion() {
    if (!selectedEnv) return;
    setCreatingVersion(true);
    try {
      const body: VersionCreateRequest = {
        definition_format: newVersionFormat,
        definition_content: newVersionContent,
      };
      await api.post(`/api/v1/environments/${selectedEnv.id}/versions`, body);
      setNewVersionContent("");
      setActiveTab("versions");
      selectEnvironment(selectedEnv.id);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create version");
    } finally { setCreatingVersion(false); }
  }

  async function handleBuild(envId: number, versionId: number) {
    if (!confirm("Start building this version? This submits a Cloud Build job.")) return;
    try {
      await api.post<EnvironmentVersionResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}/build`
      );
      selectEnvironment(envId);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Build failed to start");
    }
  }

  async function loadVersionDetail(envId: number, versionId: number) {
    try {
      const version = await api.get<EnvironmentVersionResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}`
      );
      setSelectedVersion(version);
      const logs = await api.get<BuildLogsResponse>(
        `/api/v1/environments/${envId}/versions/${versionId}/logs`
      );
      setBuildLogs(logs);
    } catch {}
  }

  const statusColor: Record<string, string> = {
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

  const visibilityBadge: Record<string, string> = {
    team: "bg-blue-100 text-blue-700",
    organization: "bg-purple-100 text-purple-700",
  };

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
              <button onClick={loadEnvironments} className="ml-2 underline">Retry</button>
            </div>
          )}
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Environments</h1>
            {canCreate && (
              <button
                onClick={() => setShowCreateModal(true)}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
              >
                New Environment
              </button>
            )}
          </div>

          {!selectedEnv ? (
            /* Environment Cards */
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {environments.map((env) => (
                <div
                  key={env.id}
                  onClick={() => selectEnvironment(env.id)}
                  className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow cursor-pointer"
                >
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="font-semibold text-lg">{env.name}</h3>
                    <span className={`px-2 py-0.5 text-xs rounded-full ${visibilityBadge[env.visibility] || "bg-gray-100"}`}>
                      {env.visibility}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mb-4 line-clamp-2">{env.description || "No description"}</p>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">
                      {env.version_count} version{env.version_count !== 1 ? "s" : ""}
                    </span>
                    {env.latest_version && (
                      <div className="flex items-center gap-1">
                        <span className={`w-2 h-2 rounded-full ${statusColor[env.latest_version.status] || "bg-gray-400"}`}></span>
                        <span className="text-xs text-gray-500">
                          v{env.latest_version.version_number} {statusLabel[env.latest_version.status] || env.latest_version.status}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {environments.length === 0 && (
                <div className="col-span-full text-center py-12 text-gray-400">
                  No environments yet. Create one to get started.
                </div>
              )}
            </div>
          ) : selectedVersion ? (
            /* Version Detail View */
            <div>
              <button
                onClick={() => setSelectedVersion(null)}
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
                        <span className={`w-2 h-2 rounded-full ${statusColor[selectedVersion.status]}`}></span>
                        <span className="text-sm text-gray-500">
                          {statusLabel[selectedVersion.status]} - {selectedVersion.definition_format}
                        </span>
                        {selectedVersion.image_uri && (
                          <span className="text-xs text-gray-400 font-mono ml-2">{selectedVersion.image_uri}</span>
                        )}
                      </div>
                    </div>
                    {canBuild && (selectedVersion.status === "draft" || selectedVersion.status === "failed") && (
                      <button
                        onClick={() => handleBuild(selectedEnv.id, selectedVersion.id)}
                        className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                      >
                        Build Image
                      </button>
                    )}
                  </div>
                </div>
                <div className="p-6">
                  <h3 className="font-medium mb-2">Definition</h3>
                  <pre className="bg-gray-50 border rounded p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap max-h-96">
                    {selectedVersion.definition_content}
                  </pre>
                  {buildLogs && buildLogs.build_id && (
                    <div className="mt-4">
                      <h3 className="font-medium mb-2">Build Info</h3>
                      <div className="text-sm text-gray-500 space-y-1">
                        <p>Build ID: <span className="font-mono">{buildLogs.build_id}</span></p>
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
            /* Environment Detail */
            <div>
              <button onClick={() => setSelectedEnv(null)} className="text-sm text-bioaf-600 mb-4 hover:underline">
                &larr; Back to environments
              </button>
              <div className="bg-white rounded-lg shadow">
                <div className="p-6 border-b">
                  <div className="flex items-center justify-between">
                    <div>
                      <h2 className="text-xl font-bold">{selectedEnv.name}</h2>
                      <p className="text-sm text-gray-500">{selectedEnv.description}</p>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className={`px-2 py-0.5 text-xs rounded-full ${visibilityBadge[selectedEnv.visibility]}`}>
                        {selectedEnv.visibility}
                      </span>
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
                </div>

                {/* Tabs */}
                <div className="border-b px-6 flex gap-4">
                  {(["versions", "new-version"] as Tab[]).map((tab) => (
                    <button
                      key={tab}
                      onClick={() => setActiveTab(tab)}
                      className={`py-3 text-sm ${activeTab === tab ? "border-b-2 border-bioaf-600 text-bioaf-600 font-medium" : "text-gray-500"}`}
                    >
                      {tab === "versions" ? "Versions" : "New Version"}
                    </button>
                  ))}
                </div>

                <div className="p-6">
                  {activeTab === "versions" && (
                    <div className="space-y-3">
                      {selectedEnv.versions.map((v: EnvironmentVersionSummary) => (
                        <div
                          key={v.id}
                          onClick={() => loadVersionDetail(selectedEnv.id, v.id)}
                          className="flex items-center justify-between p-4 border rounded-md hover:bg-gray-50 cursor-pointer"
                        >
                          <div className="flex items-center gap-3">
                            <span className="font-mono font-medium">v{v.version_number}</span>
                            <span className={`px-2 py-0.5 text-xs rounded-full ${
                              v.status === "ready" ? "bg-green-100 text-green-700" :
                              v.status === "building" ? "bg-yellow-100 text-yellow-700" :
                              v.status === "failed" ? "bg-red-100 text-red-700" :
                              "bg-gray-100 text-gray-700"
                            }`}>
                              {statusLabel[v.status] || v.status}
                            </span>
                            <span className="text-xs text-gray-400">{v.definition_format}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-xs text-gray-400">
                              {new Date(v.created_at).toLocaleDateString()}
                            </span>
                            {canBuild && (v.status === "draft" || v.status === "failed") && (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleBuild(selectedEnv.id, v.id); }}
                                className="text-xs text-bioaf-600 hover:underline"
                              >
                                Build
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
                        <label className="text-sm text-gray-500 block mb-1">Format</label>
                        <select
                          value={newVersionFormat}
                          onChange={(e) => setNewVersionFormat(e.target.value as "dockerfile" | "conda")}
                          className="border rounded px-3 py-2 text-sm"
                        >
                          <option value="dockerfile">Dockerfile</option>
                          <option value="conda">Conda (environment.yml)</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">
                          {newVersionFormat === "dockerfile" ? "Dockerfile" : "environment.yml"}
                        </label>
                        <textarea
                          value={newVersionContent}
                          onChange={(e) => setNewVersionContent(e.target.value)}
                          rows={16}
                          className="w-full border rounded px-3 py-2 text-sm font-mono"
                          placeholder={newVersionFormat === "dockerfile"
                            ? "FROM jupyter/scipy-notebook:latest\n\nUSER root\nRUN pip install scanpy anndata\n\nUSER ${NB_UID}"
                            : "name: my-env\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.11\n  - scanpy"
                          }
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

          {/* Create Environment Modal */}
          {showCreateModal && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 w-96">
                <h3 className="font-semibold text-lg mb-4">New Environment</h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Name</label>
                    <input
                      value={createForm.name}
                      onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                      placeholder="seurat-gpu"
                      className="w-full border rounded px-3 py-2 text-sm"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Description</label>
                    <input
                      value={createForm.description}
                      onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                      placeholder="Optional description"
                      className="w-full border rounded px-3 py-2 text-sm"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Visibility</label>
                    <select
                      value={createForm.visibility}
                      onChange={(e) => setCreateForm({ ...createForm, visibility: e.target.value })}
                      className="w-full border rounded px-3 py-2 text-sm"
                    >
                      <option value="team">Team (only your team)</option>
                      <option value="organization">Organization (everyone)</option>
                    </select>
                  </div>
                </div>
                <div className="flex gap-2 mt-6">
                  <button
                    onClick={handleCreate}
                    disabled={creating || !createForm.name}
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
          </>
          )}
        </main>
      </div>
    </div>
  );
}
