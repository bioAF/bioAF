"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import type {
  CustomPipeline,
  CustomPipelineCreateRequest,
  PipelineCatalogListResponse,
  PipelineRun,
  PipelineRunListResponse,
  PipelineRunStatus,
} from "@/lib/types";

const STATUS_BADGE: Record<PipelineRunStatus, string> = {
  pending: "bg-gray-100 text-gray-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-orange-100 text-orange-700",
};

interface PipelineRow {
  id: number;
  name: string;
  description: string | null;
  pipeline_key: string;
  created_at: string;
  created_by_username: string | null;
  latest_version_number: number | null;
  last_run: PipelineRun | null;
}

export default function CustomPipelineListPage() {
  const router = useRouter();
  const { canAccess, loading: permsLoading } = usePermissions();

  const [rows, setRows] = useState<PipelineRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState<CustomPipelineCreateRequest>({
    name: "",
    description: "",
  });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadPipelines();
  }, [router]);

  async function loadPipelines() {
    setLoading(true);
    setError(null);
    try {
      const [pipelines, catalog] = await Promise.all([
        api.get<CustomPipeline[]>("/api/v1/custom-pipelines"),
        api.get<PipelineCatalogListResponse>("/api/pipelines"),
      ]);
      const catalogByCustomId = new Map<number, { created_by_username: string | null; latest_version_number: number | null; pipeline_key: string }>();
      for (const c of catalog.pipelines) {
        if (c.source_type === "custom" && c.custom_pipeline_id != null) {
          catalogByCustomId.set(c.custom_pipeline_id, {
            created_by_username: c.created_by_username ?? null,
            latest_version_number: c.latest_version_number ?? null,
            pipeline_key: c.pipeline_key,
          });
        }
      }
      const initialRows: PipelineRow[] = pipelines.map((p) => {
        const enriched = catalogByCustomId.get(p.id);
        return {
          id: p.id,
          name: p.name,
          description: p.description,
          pipeline_key: enriched?.pipeline_key ?? p.pipeline_key,
          created_at: p.created_at,
          created_by_username: enriched?.created_by_username ?? null,
          latest_version_number: enriched?.latest_version_number ?? null,
          last_run: null,
        };
      });
      setRows(initialRows);

      // Fetch latest run per pipeline in parallel; tolerate failures.
      const runResults = await Promise.all(
        initialRows.map(async (row) => {
          if (!row.pipeline_key) return null;
          try {
            const data = await api.get<PipelineRunListResponse>(
              `/api/pipeline-runs?pipeline_key=${encodeURIComponent(row.pipeline_key)}&page=1&page_size=1`,
            );
            return data.runs[0] ?? null;
          } catch {
            return null;
          }
        }),
      );
      setRows((prev) =>
        prev.map((row, idx) => ({ ...row, last_run: runResults[idx] })),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pipelines");
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    setCreating(true);
    setCreateError(null);
    try {
      const created = await api.post<CustomPipeline>("/api/v1/custom-pipelines", {
        name: createForm.name,
        description: createForm.description || undefined,
      });
      setShowCreateModal(false);
      setCreateForm({ name: "", description: "" });
      router.push(`/pipelines/custom/${created.id}?newVersion=1`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create pipeline");
    } finally {
      setCreating(false);
    }
  }

  const canCreate = !permsLoading && canAccess("custom_pipelines", "create");

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
              {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
                  {error}
                  <button onClick={loadPipelines} className="ml-2 underline">
                    Retry
                  </button>
                </div>
              )}

              <div className="flex items-center justify-between mb-6">
                <div>
                  <h1 className="text-2xl font-bold">Custom Pipelines</h1>
                  <p className="text-sm text-gray-500 mt-1">
                    User-defined pipeline wrappers, versioned with linked conda environments.
                  </p>
                </div>
                {canCreate && (
                  <button
                    onClick={() => setShowCreateModal(true)}
                    className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                  >
                    Create Pipeline
                  </button>
                )}
              </div>

              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-gray-500 uppercase text-xs tracking-wide">
                    <tr>
                      <th className="px-4 py-3 text-left font-medium">Name</th>
                      <th className="px-4 py-3 text-left font-medium">Creator</th>
                      <th className="px-4 py-3 text-left font-medium">Latest Version</th>
                      <th className="px-4 py-3 text-left font-medium">Last Run Status</th>
                      <th className="px-4 py-3 text-left font-medium">Created At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={row.id}
                        onClick={() => router.push(`/pipelines/custom/${row.id}`)}
                        className="border-t hover:bg-gray-50 cursor-pointer"
                      >
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900">{row.name}</div>
                          {row.description && (
                            <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">
                              {row.description}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gray-700">
                          {row.created_by_username || "--"}
                        </td>
                        <td className="px-4 py-3 font-mono text-gray-700">
                          {row.latest_version_number != null
                            ? `v${row.latest_version_number}`
                            : "--"}
                        </td>
                        <td className="px-4 py-3">
                          {row.last_run ? (
                            <span
                              className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full ${
                                STATUS_BADGE[row.last_run.status] ||
                                "bg-gray-100 text-gray-700"
                              }`}
                            >
                              {row.last_run.status}
                            </span>
                          ) : (
                            <span className="text-xs text-gray-400">Never run</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gray-500">
                          {new Date(row.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-4 py-12 text-center text-gray-400">
                          No custom pipelines yet.
                          {canCreate && " Click \"Create Pipeline\" to get started."}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {showCreateModal && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                  <div className="bg-white rounded-lg shadow-xl p-6 w-[480px]">
                    <h3 className="font-semibold text-lg mb-1">Create Custom Pipeline</h3>
                    <p className="text-xs text-gray-500 mb-4">
                      Step 1 of 2: name your pipeline. Next, you&apos;ll define a version
                      with the code, entrypoint command, environment, and variables.
                    </p>
                    <div className="space-y-3">
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">Name</label>
                        <input
                          value={createForm.name}
                          onChange={(e) =>
                            setCreateForm({ ...createForm, name: e.target.value })
                          }
                          placeholder="my-pipeline"
                          className="w-full border rounded px-3 py-2 text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-sm text-gray-500 block mb-1">
                          Description
                        </label>
                        <input
                          value={createForm.description ?? ""}
                          onChange={(e) =>
                            setCreateForm({ ...createForm, description: e.target.value })
                          }
                          placeholder="Optional description"
                          className="w-full border rounded px-3 py-2 text-sm"
                        />
                      </div>
                      {createError && (
                        <p className="text-sm text-red-600">{createError}</p>
                      )}
                      <p className="text-xs text-gray-400">
                        After clicking Create, you&apos;ll be taken to the version form to
                        provide the code source, entrypoint command (e.g.{" "}
                        <code>bash run.sh</code>), pipeline environment, and any variables.
                      </p>
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
                        onClick={() => {
                          setShowCreateModal(false);
                          setCreateError(null);
                        }}
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
