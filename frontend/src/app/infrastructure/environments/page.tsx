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
  EnvironmentChangeResponse,
  EnvironmentHistoryResponse,
} from "@/lib/types";

type Tab = "packages" | "history" | "compare";

export default function InfraEnvironmentsPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canMutate = user?.role === "admin" || user?.role === "comp_bio";

  const [environments, setEnvironments] = useState<EnvironmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedEnv, setSelectedEnv] = useState<EnvironmentDetailResponse | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("packages");
  const [history, setHistory] = useState<EnvironmentChangeResponse[]>([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState({ name: "", description: "", clone_from: "" });
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadEnvironments();
  }, [router]);

  async function loadEnvironments() {
    try {
      const data = await api.get<EnvironmentListResponse>("/api/environments");
      setEnvironments(data.environments);
    } catch {} finally { setLoading(false); }
  }

  async function selectEnvironment(name: string) {
    try {
      const detail = await api.get<EnvironmentDetailResponse>(`/api/environments/${name}`);
      setSelectedEnv(detail);
      setActiveTab("packages");
      loadHistory(name);
    } catch {}
  }

  async function loadHistory(name: string) {
    try {
      const data = await api.get<EnvironmentHistoryResponse>(`/api/environments/${name}/history`);
      setHistory(data.changes);
    } catch {}
  }

  async function handleCreate() {
    setCreating(true);
    try {
      await api.post("/api/environments", {
        name: createForm.name,
        description: createForm.description || null,
        clone_from: createForm.clone_from || null,
      });
      setShowCreateModal(false);
      setCreateForm({ name: "", description: "", clone_from: "" });
      loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create environment");
    } finally { setCreating(false); }
  }

  async function handleArchive(name: string) {
    if (!confirm(`Archive environment "${name}"? This will not delete it from GitOps.`)) return;
    try {
      await api.delete(`/api/environments/${name}`);
      setSelectedEnv(null);
      loadEnvironments();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to archive");
    }
  }

  async function handleRollback(changeId: number) {
    if (!selectedEnv) return;
    if (!confirm("This will revert the environment to this state. Running jobs will not be affected, but new jobs and kernel restarts will use the reverted environment.")) return;
    try {
      await api.post(`/api/environments/${selectedEnv.name}/rollback`, { target_change_id: changeId });
      loadHistory(selectedEnv.name);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Rollback failed");
    }
  }

  const typeBadge: Record<string, string> = {
    conda: "bg-green-100 text-green-700",
    r: "bg-blue-100 text-blue-700",
    custom_conda: "bg-purple-100 text-purple-700",
    custom_r: "bg-orange-100 text-orange-700",
  };

  const statusColor: Record<string, string> = {
    active: "bg-green-500",
    syncing: "bg-yellow-500",
    error: "bg-red-500",
    archived: "bg-gray-500",
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
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Environments</h1>
            {canMutate && (
              <button
                onClick={() => setShowCreateModal(true)}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
              >
                Create Environment
              </button>
            )}
          </div>

          {!selectedEnv ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {environments.map((env) => (
                <div
                  key={env.id}
                  onClick={() => selectEnvironment(env.name)}
                  className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow cursor-pointer"
                >
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="font-semibold text-lg">{env.name}</h3>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 text-xs rounded-full ${typeBadge[env.env_type] || "bg-gray-100"}`}>
                        {env.env_type}
                      </span>
                      {env.is_default && (
                        <span className="px-2 py-0.5 text-xs rounded-full bg-yellow-100 text-yellow-700">Default</span>
                      )}
                    </div>
                  </div>
                  <p className="text-sm text-gray-500 mb-4 line-clamp-2">{env.description || "No description"}</p>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">{env.package_count} packages</span>
                    <div className="flex items-center gap-1">
                      <span className={`w-2 h-2 rounded-full ${statusColor[env.status] || "bg-gray-400"}`}></span>
                      <span className="text-xs text-gray-500">{env.status}</span>
                    </div>
                  </div>
                </div>
              ))}
              {environments.length === 0 && (
                <div className="col-span-full text-center py-12 text-gray-400">No environments found</div>
              )}
            </div>
          ) : (
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
                      <span className={`px-2 py-0.5 text-xs rounded-full ${typeBadge[selectedEnv.env_type] || "bg-gray-100"}`}>
                        {selectedEnv.env_type}
                      </span>
                      {canMutate && !selectedEnv.is_default && (
                        <button
                          onClick={() => handleArchive(selectedEnv.name)}
                          className="text-red-500 text-sm hover:underline"
                        >
                          Archive
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                <div className="border-b px-6 flex gap-4">
                  {(["packages", "history", "compare"] as Tab[]).map((tab) => (
                    <button
                      key={tab}
                      onClick={() => setActiveTab(tab)}
                      className={`py-3 text-sm capitalize ${activeTab === tab ? "border-b-2 border-bioaf-600 text-bioaf-600 font-medium" : "text-gray-500"}`}
                    >
                      {tab}
                    </button>
                  ))}
                </div>

                <div className="p-6">
                  {activeTab === "packages" && (
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left text-gray-500">
                          <th className="py-2">Package</th>
                          <th className="py-2">Version</th>
                          <th className="py-2">Source</th>
                          <th className="py-2">Pinned</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedEnv.packages.map((pkg) => (
                          <tr key={`${pkg.name}-${pkg.source}`} className="border-b last:border-0">
                            <td className="py-2 font-medium">{pkg.name}</td>
                            <td className="py-2 text-gray-500">{pkg.version || "latest"}</td>
                            <td className="py-2">
                              <span className={`px-1.5 py-0.5 text-xs rounded ${typeBadge[pkg.source] || "bg-gray-100"}`}>
                                {pkg.source}
                              </span>
                            </td>
                            <td className="py-2">{pkg.pinned ? "Yes" : ""}</td>
                          </tr>
                        ))}
                        {selectedEnv.packages.length === 0 && (
                          <tr><td colSpan={4} className="text-center py-4 text-gray-400">No packages</td></tr>
                        )}
                      </tbody>
                    </table>
                  )}

                  {activeTab === "history" && (
                    <div className="space-y-3">
                      {history.map((change) => (
                        <div key={change.id} className="flex items-start justify-between p-3 border rounded-md">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium capitalize">{change.change_type}</span>
                              {change.package_name && (
                                <span className="text-sm text-gray-600">{change.package_name}</span>
                              )}
                              {change.new_version && (
                                <span className="text-xs text-gray-400">{change.new_version}</span>
                              )}
                            </div>
                            <div className="text-xs text-gray-400 mt-1">
                              {change.user?.email || "System"} &middot; {new Date(change.created_at).toLocaleString()}
                              {change.git_commit_sha && (
                                <span className="ml-2 font-mono">{change.git_commit_sha.slice(0, 8)}</span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-1">
                              {change.reconciled ? (
                                <span className="text-xs text-green-600">Reconciled</span>
                              ) : change.error_message ? (
                                <span className="text-xs text-red-600" title={change.error_message}>Error</span>
                              ) : (
                                <span className="text-xs text-yellow-600">Pending</span>
                              )}
                            </div>
                          </div>
                          {canMutate && change.git_commit_sha && (
                            <button
                              onClick={() => handleRollback(change.id)}
                              className="text-xs text-bioaf-600 hover:underline whitespace-nowrap"
                            >
                              Rollback to this
                            </button>
                          )}
                        </div>
                      ))}
                      {history.length === 0 && (
                        <p className="text-center text-gray-400 py-4">No changes recorded</p>
                      )}
                    </div>
                  )}

                  {activeTab === "compare" && (
                    <p className="text-center text-gray-400 py-8">
                      Select two commits from the History tab to compare environment versions.
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {showCreateModal && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 w-96">
                <h3 className="font-semibold text-lg mb-4">Create Environment</h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Name</label>
                    <input
                      value={createForm.name}
                      onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                      placeholder="my-custom-env"
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
                    <label className="text-sm text-gray-500 block mb-1">Clone From (optional)</label>
                    <select
                      value={createForm.clone_from}
                      onChange={(e) => setCreateForm({ ...createForm, clone_from: e.target.value })}
                      className="w-full border rounded px-3 py-2 text-sm"
                    >
                      <option value="">Start fresh</option>
                      {environments.map((env) => (
                        <option key={env.name} value={env.name}>{env.name}</option>
                      ))}
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
