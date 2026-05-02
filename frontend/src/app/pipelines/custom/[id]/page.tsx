"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { CustomPipelineLaunchDialog } from "@/components/pipelines/CustomPipelineLaunchDialog";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import type {
  CustomPipelineCodeSource,
  CustomPipelineDetail,
  CustomPipelineVariableDefinition,
  CustomPipelineVersion,
  CustomPipelineVersionCreateRequest,
  EnvironmentDetailResponse,
  EnvironmentListResponse,
  GitHubRepo,
  GitHubRepoListResponse,
} from "@/lib/types";

interface EnvVersionOption {
  env_id: number;
  env_name: string;
  version_id: number;
  version_number: number;
  status: string;
}

interface VariableDraft extends CustomPipelineVariableDefinition {
  _key: string;
}

const DEFAULT_CPU = "2";
const DEFAULT_MEMORY = "8Gi";

function emptyVariableDraft(): VariableDraft {
  return {
    _key: Math.random().toString(36).slice(2),
    variable_name: "",
    default_value: "",
    variable_type: "string",
    is_required: false,
  };
}

function changeLabel(
  current: CustomPipelineVersion,
  previous: CustomPipelineVersion | null,
): { label: string; tone: "blue" | "amber" | "purple" | "gray" } {
  if (previous == null) return { label: "Initial version", tone: "gray" };
  if (current.version_trigger === "environment_cascade") {
    return { label: "Image change", tone: "amber" };
  }
  if (current.environment_version_id !== previous.environment_version_id) {
    return { label: "Config + image change", tone: "purple" };
  }
  return { label: "Config change", tone: "blue" };
}

const TONE_CLASSES: Record<"blue" | "amber" | "purple" | "gray", string> = {
  blue: "bg-blue-100 text-blue-700",
  amber: "bg-amber-100 text-amber-700",
  purple: "bg-purple-100 text-purple-700",
  gray: "bg-gray-100 text-gray-700",
};

export default function CustomPipelineDetailPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const pipelineId = Number(params.id);
  const autoLaunch = searchParams.get("launch") === "1";
  const autoNewVersion = searchParams.get("newVersion") === "1";
  const { canAccess, loading: permsLoading } = usePermissions();

  const [pipeline, setPipeline] = useState<CustomPipelineDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [envOptions, setEnvOptions] = useState<EnvVersionOption[]>([]);
  const [repos, setRepos] = useState<GitHubRepo[]>([]);

  const [editingMeta, setEditingMeta] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [savingMeta, setSavingMeta] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(null);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [showLaunchDialog, setShowLaunchDialog] = useState(false);

  const [expandedVersionIds, setExpandedVersionIds] = useState<Set<number>>(new Set());

  const [showNewVersionForm, setShowNewVersionForm] = useState(false);
  const [versionForm, setVersionForm] = useState<{
    code_source_type: CustomPipelineCodeSource;
    github_repo_id: number | null;
    code_content: string;
    entrypoint_command: string;
    environment_version_id: number | null;
    cpu_request: string;
    memory_request: string;
    log_file_path: string;
    variables: VariableDraft[];
  }>({
    code_source_type: "code_blob",
    github_repo_id: null,
    code_content: "",
    entrypoint_command: "",
    environment_version_id: null,
    cpu_request: DEFAULT_CPU,
    memory_request: DEFAULT_MEMORY,
    log_file_path: "",
    variables: [],
  });
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [versionError, setVersionError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    if (Number.isNaN(pipelineId)) return;
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, pipelineId]);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [detail, envList, repoList] = await Promise.all([
        api.get<CustomPipelineDetail>(`/api/v1/custom-pipelines/${pipelineId}`),
        api.get<EnvironmentListResponse>("/api/v1/environments?type=pipeline"),
        api.get<GitHubRepoListResponse>("/api/v1/github-repos").catch(() => ({
          repos: [] as GitHubRepo[],
          total: 0,
        })),
      ]);
      setPipeline(detail);
      setEditName(detail.name);
      setEditDescription(detail.description ?? "");
      setRepos(repoList.repos);

      // Load each pipeline environment's versions to get the full list of
      // ready versions for the dropdown.
      const envDetails = await Promise.all(
        envList.environments.map((env) =>
          api
            .get<EnvironmentDetailResponse>(`/api/v1/environments/${env.id}`)
            .catch(() => null),
        ),
      );
      const options: EnvVersionOption[] = [];
      for (const env of envDetails) {
        if (!env) continue;
        for (const v of env.versions) {
          options.push({
            env_id: env.id,
            env_name: env.name,
            version_id: v.id,
            version_number: v.version_number,
            status: v.status,
          });
        }
      }
      setEnvOptions(options);
      seedVersionFormFromLatest(detail, options);

      if (autoLaunch && detail.versions.some((v) => v.status === "active")) {
        setShowLaunchDialog(true);
      }
      if (autoNewVersion) {
        setShowNewVersionForm(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pipeline");
    } finally {
      setLoading(false);
    }
  }

  function seedVersionFormFromLatest(
    detail: CustomPipelineDetail,
    options: EnvVersionOption[],
  ) {
    const latest = detail.versions[0];
    if (!latest) {
      setVersionForm((prev) => ({
        ...prev,
        environment_version_id: options.find((o) => o.status === "ready")?.version_id ?? null,
      }));
      return;
    }
    setVersionForm({
      code_source_type: latest.code_source_type,
      github_repo_id: latest.github_repo_id,
      code_content: latest.code_content ?? "",
      entrypoint_command: latest.entrypoint_command,
      environment_version_id: latest.environment_version_id,
      cpu_request: latest.cpu_request || DEFAULT_CPU,
      memory_request: latest.memory_request || DEFAULT_MEMORY,
      log_file_path: latest.log_file_path ?? "",
      variables: latest.variables.map((v) => ({
        _key: `${v.id}`,
        variable_name: v.variable_name,
        default_value: v.default_value,
        variable_type: v.variable_type,
        is_required: v.is_required,
      })),
    });
  }

  const envOptionsById = useMemo(() => {
    const map = new Map<number, EnvVersionOption>();
    for (const o of envOptions) map.set(o.version_id, o);
    return map;
  }, [envOptions]);

  const readyEnvOptions = useMemo(
    () => envOptions.filter((o) => o.status === "ready"),
    [envOptions],
  );

  const repoById = useMemo(() => {
    const map = new Map<number, GitHubRepo>();
    for (const r of repos) map.set(r.id, r);
    return map;
  }, [repos]);

  const canEdit = !permsLoading && canAccess("custom_pipelines", "edit");
  const canDelete = !permsLoading && canAccess("custom_pipelines", "delete");
  const canLaunch = !permsLoading && canAccess("custom_pipelines", "launch");

  function toggleVersion(versionId: number) {
    setExpandedVersionIds((prev) => {
      const next = new Set(prev);
      if (next.has(versionId)) next.delete(versionId);
      else next.add(versionId);
      return next;
    });
  }

  async function handleSaveMeta() {
    if (!pipeline) return;
    setSavingMeta(true);
    setMetaError(null);
    try {
      await api.put(`/api/v1/custom-pipelines/${pipeline.id}`, {
        name: editName,
        description: editDescription || null,
      });
      await loadAll();
      setEditingMeta(false);
    } catch (err) {
      setMetaError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSavingMeta(false);
    }
  }

  async function handleDelete() {
    if (!pipeline) return;
    setDeleting(true);
    try {
      await api.delete(`/api/v1/custom-pipelines/${pipeline.id}`);
      router.push("/pipelines/custom");
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to delete");
      setDeleting(false);
    }
  }

  async function handleDeprecate(versionId: number) {
    if (!pipeline) return;
    if (!confirm("Deprecate this version? It will no longer be launchable.")) return;
    try {
      await api.post(
        `/api/v1/custom-pipelines/${pipeline.id}/versions/${versionId}/deprecate`,
        {},
      );
      await loadAll();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to deprecate");
    }
  }

  function addVariable() {
    setVersionForm((prev) => ({
      ...prev,
      variables: [...prev.variables, emptyVariableDraft()],
    }));
  }

  function updateVariable(idx: number, patch: Partial<VariableDraft>) {
    setVersionForm((prev) => ({
      ...prev,
      variables: prev.variables.map((v, i) => (i === idx ? { ...v, ...patch } : v)),
    }));
  }

  function removeVariable(idx: number) {
    setVersionForm((prev) => ({
      ...prev,
      variables: prev.variables.filter((_, i) => i !== idx),
    }));
  }

  async function handleCreateVersion() {
    if (!pipeline) return;
    setVersionError(null);

    if (!versionForm.entrypoint_command.trim()) {
      setVersionError("Entrypoint command is required.");
      return;
    }
    if (versionForm.environment_version_id == null) {
      setVersionError("Select an environment version.");
      return;
    }
    if (versionForm.code_source_type === "github_repo" && !versionForm.github_repo_id) {
      setVersionError("Select a GitHub repo.");
      return;
    }
    if (
      (versionForm.code_source_type === "code_blob" ||
        versionForm.code_source_type === "inline") &&
      !versionForm.code_content.trim()
    ) {
      setVersionError("Provide code content for the chosen source type.");
      return;
    }
    for (const v of versionForm.variables) {
      if (!v.variable_name.trim()) {
        setVersionError("All variables need a name.");
        return;
      }
    }

    const body: CustomPipelineVersionCreateRequest = {
      code_source_type: versionForm.code_source_type,
      code_content:
        versionForm.code_source_type === "github_repo"
          ? null
          : versionForm.code_content,
      github_repo_id:
        versionForm.code_source_type === "github_repo"
          ? versionForm.github_repo_id
          : null,
      entrypoint_command: versionForm.entrypoint_command,
      environment_version_id: versionForm.environment_version_id,
      cpu_request: versionForm.cpu_request || DEFAULT_CPU,
      memory_request: versionForm.memory_request || DEFAULT_MEMORY,
      log_file_path: versionForm.log_file_path || null,
      variables: versionForm.variables.map((v) => ({
        variable_name: v.variable_name,
        default_value: v.default_value || null,
        variable_type: v.variable_type,
        is_required: v.is_required,
      })),
    };

    setCreatingVersion(true);
    try {
      await api.post(`/api/v1/custom-pipelines/${pipeline.id}/versions`, body);
      setShowNewVersionForm(false);
      await loadAll();
    } catch (err) {
      setVersionError(err instanceof Error ? err.message : "Failed to create version");
    } finally {
      setCreatingVersion(false);
    }
  }

  function handleLaunch() {
    if (!pipeline) return;
    setShowLaunchDialog(true);
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : error || !pipeline ? (
            <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
              {error || "Pipeline not found"}
              <button
                onClick={() => router.push("/pipelines/custom")}
                className="ml-2 underline"
              >
                Back to list
              </button>
            </div>
          ) : (
            <>
              <button
                onClick={() => router.push("/pipelines/custom")}
                className="text-sm text-bioaf-600 mb-4 hover:underline"
              >
                &larr; Back to custom pipelines
              </button>

              <div className="bg-white rounded-lg shadow mb-6">
                <div className="p-6 flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    {editingMeta ? (
                      <div className="space-y-3">
                        <input
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          className="w-full text-xl font-bold border rounded px-3 py-1.5"
                        />
                        <textarea
                          value={editDescription}
                          onChange={(e) => setEditDescription(e.target.value)}
                          rows={2}
                          placeholder="Description"
                          className="w-full border rounded px-3 py-2 text-sm"
                        />
                        {metaError && <p className="text-sm text-red-600">{metaError}</p>}
                        <div className="flex gap-2">
                          <button
                            onClick={handleSaveMeta}
                            disabled={savingMeta || !editName.trim()}
                            className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                          >
                            {savingMeta ? "Saving..." : "Save"}
                          </button>
                          <button
                            onClick={() => {
                              setEditingMeta(false);
                              setEditName(pipeline.name);
                              setEditDescription(pipeline.description ?? "");
                              setMetaError(null);
                            }}
                            className="border px-4 py-1.5 rounded text-sm"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <h1 className="text-2xl font-bold truncate">{pipeline.name}</h1>
                        {pipeline.description && (
                          <p className="text-sm text-gray-500 mt-1">{pipeline.description}</p>
                        )}
                        <p className="text-xs text-gray-400 mt-2 font-mono">
                          {pipeline.pipeline_key}
                        </p>
                      </>
                    )}
                  </div>
                  {!editingMeta && (
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {canLaunch && (
                        <button
                          onClick={handleLaunch}
                          className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
                        >
                          Launch
                        </button>
                      )}
                      {canEdit && (
                        <button
                          onClick={() => setEditingMeta(true)}
                          className="border px-4 py-2 rounded-md text-sm"
                        >
                          Edit
                        </button>
                      )}
                      {canDelete && (
                        <button
                          onClick={() => setShowDeleteConfirm(true)}
                          className="text-red-500 text-sm px-2 py-2 hover:underline"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="bg-white rounded-lg shadow">
                <div className="px-6 py-4 border-b flex items-center justify-between">
                  <h2 className="font-semibold text-lg">Versions</h2>
                  {canEdit && (
                    <button
                      onClick={() => {
                        setShowNewVersionForm((s) => !s);
                        if (!showNewVersionForm) {
                          seedVersionFormFromLatest(pipeline, envOptions);
                        }
                      }}
                      className="bg-bioaf-600 text-white px-3 py-1.5 rounded text-sm hover:bg-bioaf-700"
                    >
                      {showNewVersionForm ? "Cancel" : "New Version"}
                    </button>
                  )}
                </div>

                {showNewVersionForm && canEdit && (
                  <div className="p-6 border-b bg-gray-50">
                    <h3 className="font-semibold mb-4">New Version</h3>
                    <NewVersionForm
                      versionForm={versionForm}
                      setVersionForm={setVersionForm}
                      readyEnvOptions={readyEnvOptions}
                      repos={repos}
                      addVariable={addVariable}
                      updateVariable={updateVariable}
                      removeVariable={removeVariable}
                    />
                    {versionError && (
                      <p className="text-sm text-red-600 mt-3">{versionError}</p>
                    )}
                    <div className="mt-4 flex gap-2">
                      <button
                        onClick={handleCreateVersion}
                        disabled={creatingVersion}
                        className="bg-bioaf-600 text-white px-4 py-2 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                      >
                        {creatingVersion ? "Creating..." : "Create Version"}
                      </button>
                      <button
                        onClick={() => {
                          setShowNewVersionForm(false);
                          setVersionError(null);
                        }}
                        className="border px-4 py-2 rounded text-sm"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                <div className="divide-y">
                  {pipeline.versions.map((version, idx) => {
                    const previous = pipeline.versions[idx + 1] ?? null;
                    const change = changeLabel(version, previous);
                    const expanded = expandedVersionIds.has(version.id);
                    const env = envOptionsById.get(version.environment_version_id);
                    const repo =
                      version.github_repo_id != null
                        ? repoById.get(version.github_repo_id)
                        : null;
                    return (
                      <div key={version.id} className="p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-3 flex-wrap">
                            <span className="font-mono font-semibold">
                              v{version.version_number}
                            </span>
                            <span
                              className={`px-2 py-0.5 text-xs rounded-full ${TONE_CLASSES[change.tone]}`}
                            >
                              {change.label}
                            </span>
                            <span
                              className={`px-2 py-0.5 text-xs rounded-full ${
                                version.status === "active"
                                  ? "bg-green-100 text-green-700"
                                  : "bg-gray-200 text-gray-600"
                              }`}
                            >
                              {version.status}
                            </span>
                            <span className="text-xs text-gray-400">
                              {new Date(version.created_at).toLocaleString()}
                            </span>
                          </div>
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => toggleVersion(version.id)}
                              className="text-sm text-bioaf-600 hover:underline"
                            >
                              {expanded ? "Hide Details" : "Show Details"}
                            </button>
                            {canEdit && version.status === "active" && (
                              <button
                                onClick={() => handleDeprecate(version.id)}
                                className="text-sm text-gray-500 hover:text-red-600 hover:underline"
                              >
                                Deprecate
                              </button>
                            )}
                          </div>
                        </div>

                        {expanded && (
                          <div className="mt-4 pl-2 border-l-2 border-gray-200 space-y-3 text-sm">
                            <DetailRow label="Code source">
                              {version.code_source_type === "github_repo" ? (
                                repo ? (
                                  <span className="font-mono text-gray-700">
                                    {repo.display_name} ({repo.git_ssh_url})
                                  </span>
                                ) : (
                                  <span className="font-mono text-gray-500">
                                    GitHub repo #{version.github_repo_id}
                                  </span>
                                )
                              ) : version.code_source_type === "code_blob" ? (
                                <span>Code blob</span>
                              ) : (
                                <span>Inline command</span>
                              )}
                            </DetailRow>
                            <DetailRow label="Entrypoint">
                              <code className="font-mono bg-gray-100 px-2 py-0.5 rounded">
                                {version.entrypoint_command}
                              </code>
                            </DetailRow>
                            {(version.code_source_type === "code_blob" ||
                              version.code_source_type === "inline") &&
                              version.code_content && (
                                <div>
                                  <div className="text-xs text-gray-500 mb-1">Code content</div>
                                  <pre className="bg-gray-50 border rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-72">
                                    {version.code_content}
                                  </pre>
                                </div>
                              )}
                            <DetailRow label="Environment">
                              {env ? (
                                <span>
                                  {env.env_name} v{env.version_number}{" "}
                                  <span className="text-xs text-gray-400">
                                    ({env.status})
                                  </span>
                                </span>
                              ) : (
                                <span className="text-gray-500">
                                  Environment version #{version.environment_version_id}
                                </span>
                              )}
                            </DetailRow>
                            <DetailRow label="Resources">
                              <span className="font-mono">
                                CPU {version.cpu_request} / Memory {version.memory_request}
                              </span>
                            </DetailRow>
                            <DetailRow label="Log file">
                              {version.log_file_path ? (
                                <code className="font-mono">{version.log_file_path}</code>
                              ) : (
                                <span className="text-gray-500">Default (terminal output)</span>
                              )}
                            </DetailRow>
                            <div>
                              <div className="text-xs text-gray-500 mb-1">Variables</div>
                              {version.variables.length === 0 ? (
                                <p className="text-gray-500 text-xs">No variables.</p>
                              ) : (
                                <table className="w-full text-xs border">
                                  <thead className="bg-gray-100 text-gray-500 uppercase">
                                    <tr>
                                      <th className="px-2 py-1 text-left">Name</th>
                                      <th className="px-2 py-1 text-left">Type</th>
                                      <th className="px-2 py-1 text-left">Default</th>
                                      <th className="px-2 py-1 text-left">Required</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {version.variables.map((v) => (
                                      <tr key={v.id} className="border-t">
                                        <td className="px-2 py-1 font-mono">
                                          {v.variable_name}
                                        </td>
                                        <td className="px-2 py-1">{v.variable_type}</td>
                                        <td className="px-2 py-1 font-mono">
                                          {v.default_value ?? ""}
                                        </td>
                                        <td className="px-2 py-1">
                                          {v.is_required ? "Yes" : "No"}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {pipeline.versions.length === 0 && (
                    <div className="p-12 text-center text-gray-500 text-sm">
                      <p className="font-medium text-gray-700">No versions yet.</p>
                      {canEdit && (
                        <p className="mt-1 text-gray-500">
                          A version holds the code, entrypoint command, environment, and
                          variables. Click <span className="font-semibold">New Version</span>{" "}
                          to define the first one and make this pipeline launchable.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {showLaunchDialog && (
                <CustomPipelineLaunchDialog
                  pipeline={pipeline}
                  envOptionsById={envOptionsById}
                  repoById={repoById}
                  onClose={() => setShowLaunchDialog(false)}
                  onLaunched={(runId) => {
                    setShowLaunchDialog(false);
                    router.push(`/pipelines/runs/${runId}`);
                  }}
                />
              )}

              {showDeleteConfirm && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                  <div className="bg-white rounded-lg shadow-xl p-6 w-96">
                    <h3 className="font-semibold text-lg mb-4">Delete Pipeline</h3>
                    <p className="text-sm text-gray-600 mb-4">
                      This will soft-delete <strong>{pipeline.name}</strong>. Existing pipeline
                      runs are preserved.
                    </p>
                    <div className="flex gap-2">
                      <button
                        onClick={handleDelete}
                        disabled={deleting}
                        className="flex-1 bg-red-600 text-white py-2 rounded text-sm hover:bg-red-700 disabled:opacity-50"
                      >
                        {deleting ? "Deleting..." : "Delete"}
                      </button>
                      <button
                        onClick={() => setShowDeleteConfirm(false)}
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

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 items-start">
      <div className="text-xs text-gray-500 uppercase tracking-wide pt-0.5">{label}</div>
      <div className="text-sm text-gray-700">{children}</div>
    </div>
  );
}

function NewVersionForm({
  versionForm,
  setVersionForm,
  readyEnvOptions,
  repos,
  addVariable,
  updateVariable,
  removeVariable,
}: {
  versionForm: {
    code_source_type: CustomPipelineCodeSource;
    github_repo_id: number | null;
    code_content: string;
    entrypoint_command: string;
    environment_version_id: number | null;
    cpu_request: string;
    memory_request: string;
    log_file_path: string;
    variables: VariableDraft[];
  };
  setVersionForm: React.Dispatch<
    React.SetStateAction<{
      code_source_type: CustomPipelineCodeSource;
      github_repo_id: number | null;
      code_content: string;
      entrypoint_command: string;
      environment_version_id: number | null;
      cpu_request: string;
      memory_request: string;
      log_file_path: string;
      variables: VariableDraft[];
    }>
  >;
  readyEnvOptions: EnvVersionOption[];
  repos: GitHubRepo[];
  addVariable: () => void;
  updateVariable: (idx: number, patch: Partial<VariableDraft>) => void;
  removeVariable: (idx: number) => void;
}) {
  return (
    <div className="space-y-4">
      <RuntimeContractPanel />
      <div>
        <label className="text-sm text-gray-500 block mb-1">Code source</label>
        <div className="flex gap-4 text-sm">
          {(["github_repo", "code_blob", "inline"] as CustomPipelineCodeSource[]).map(
            (src) => (
              <label key={src} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="code_source_type"
                  value={src}
                  checked={versionForm.code_source_type === src}
                  onChange={() =>
                    setVersionForm((prev) => ({ ...prev, code_source_type: src }))
                  }
                />
                {src === "github_repo"
                  ? "GitHub repo"
                  : src === "code_blob"
                    ? "Code blob"
                    : "Inline command"}
              </label>
            ),
          )}
        </div>
      </div>

      {versionForm.code_source_type === "github_repo" && (
        <div>
          <label className="text-sm text-gray-500 block mb-1">GitHub repo</label>
          <select
            value={versionForm.github_repo_id ?? ""}
            onChange={(e) =>
              setVersionForm((prev) => ({
                ...prev,
                github_repo_id: e.target.value ? Number(e.target.value) : null,
              }))
            }
            className="w-full border rounded px-3 py-2 text-sm bg-white"
          >
            <option value="">Select a repo...</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.display_name} ({r.git_ssh_url})
              </option>
            ))}
          </select>
          {repos.length === 0 && (
            <p className="text-xs text-gray-500 mt-1">
              No repos registered. Add one from Workbench &gt; Work Nodes.
            </p>
          )}
        </div>
      )}

      {versionForm.code_source_type === "code_blob" && (
        <div>
          <label className="text-sm text-gray-500 block mb-1">Code blob</label>
          <textarea
            value={versionForm.code_content}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, code_content: e.target.value }))
            }
            rows={10}
            className="w-full border rounded px-3 py-2 text-sm font-mono"
            placeholder={"#!/bin/bash\necho hello"}
          />
        </div>
      )}

      {versionForm.code_source_type === "inline" && (
        <div>
          <label className="text-sm text-gray-500 block mb-1">Inline command</label>
          <input
            value={versionForm.code_content}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, code_content: e.target.value }))
            }
            placeholder="echo hello"
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
      )}

      <div>
        <label className="text-sm text-gray-500 block mb-1">Entrypoint command</label>
        <input
          value={versionForm.entrypoint_command}
          onChange={(e) =>
            setVersionForm((prev) => ({ ...prev, entrypoint_command: e.target.value }))
          }
          placeholder="bash run.sh"
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      </div>

      <div>
        <label className="text-sm text-gray-500 block mb-1">Environment</label>
        <select
          value={versionForm.environment_version_id ?? ""}
          onChange={(e) =>
            setVersionForm((prev) => ({
              ...prev,
              environment_version_id: e.target.value ? Number(e.target.value) : null,
            }))
          }
          className="w-full border rounded px-3 py-2 text-sm bg-white"
        >
          <option value="">Select an environment version...</option>
          {readyEnvOptions.map((opt) => (
            <option key={opt.version_id} value={opt.version_id}>
              {opt.env_name} v{opt.version_number}
            </option>
          ))}
        </select>
        {readyEnvOptions.length === 0 && (
          <p className="text-xs text-gray-500 mt-1">
            No ready pipeline environment versions. Build one in Pipelines &gt; Environments.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-sm text-gray-500 block mb-1">CPU</label>
          <input
            value={versionForm.cpu_request}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, cpu_request: e.target.value }))
            }
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
        <div>
          <label className="text-sm text-gray-500 block mb-1">Memory</label>
          <input
            value={versionForm.memory_request}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, memory_request: e.target.value }))
            }
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
      </div>

      <div>
        <label className="text-sm text-gray-500 block mb-1">Log file path (optional)</label>
        <input
          value={versionForm.log_file_path}
          onChange={(e) =>
            setVersionForm((prev) => ({ ...prev, log_file_path: e.target.value }))
          }
          placeholder="/outputs/my-script.log"
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      </div>

      <div>
        <label className="text-sm text-gray-500 block mb-2">Variables</label>
        <div className="space-y-2">
          {versionForm.variables.map((v, idx) => (
            <div
              key={v._key}
              className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,1.2fr)_auto_auto] gap-2 items-center"
            >
              <input
                value={v.variable_name}
                onChange={(e) => updateVariable(idx, { variable_name: e.target.value })}
                placeholder="Variable name"
                className="border rounded px-2 py-1.5 text-sm"
              />
              <select
                value={v.variable_type}
                onChange={(e) =>
                  updateVariable(idx, {
                    variable_type: e.target.value as VariableDraft["variable_type"],
                  })
                }
                className="border rounded px-2 py-1.5 text-sm bg-white"
              >
                <option value="string">string</option>
                <option value="number">number</option>
                <option value="boolean">boolean</option>
              </select>
              <input
                value={v.default_value ?? ""}
                onChange={(e) => updateVariable(idx, { default_value: e.target.value })}
                placeholder="Default value"
                className="border rounded px-2 py-1.5 text-sm"
              />
              <label className="flex items-center gap-1 text-xs text-gray-600 px-1">
                <input
                  type="checkbox"
                  checked={v.is_required}
                  onChange={(e) => updateVariable(idx, { is_required: e.target.checked })}
                  className="rounded border-gray-300"
                />
                Required
              </label>
              <button
                type="button"
                onClick={() => removeVariable(idx)}
                className="text-red-400 hover:text-red-600 text-sm px-1"
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addVariable}
            className="text-sm text-bioaf-600 hover:underline"
          >
            + Add Variable
          </button>
        </div>
      </div>
    </div>
  );
}

function RuntimeContractPanel() {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-blue-200 bg-blue-50 rounded">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-blue-900"
      >
        <span>Runtime contract — what your code can expect</span>
        <span className="text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 text-xs text-blue-900 space-y-2">
          <div>
            <span className="font-semibold">Inputs:</span> selected files are staged
            under <code className="bg-white/60 px-1 rounded">/data/</code> using paths
            like <code className="bg-white/60 px-1 rounded">/data/&lt;project&gt;/&lt;experiment&gt;/&lt;sample&gt;/&lt;filename&gt;</code>.
            Full mapping (file ids, project, experiment, sample) is in{" "}
            <code className="bg-white/60 px-1 rounded">/data/manifest.json</code>.
          </div>
          <div>
            <span className="font-semibold">Variables:</span> launch-time values are
            written to <code className="bg-white/60 px-1 rounded">/data/params.json</code>{" "}
            and exposed as environment variables{" "}
            <code className="bg-white/60 px-1 rounded">PARAM_&lt;NAME&gt;</code> on the
            container.
          </div>
          <div>
            <span className="font-semibold">Code source:</span> a GitHub repo is cloned
            to <code className="bg-white/60 px-1 rounded">/code/&lt;repo-display-name&gt;/</code>{" "}
            (the working directory). A code blob is written to{" "}
            <code className="bg-white/60 px-1 rounded">/code/script</code>. Inline
            commands run with <code className="bg-white/60 px-1 rounded">/data</code> as
            the working directory.
          </div>
          <div>
            <span className="font-semibold">Outputs:</span> anything your script writes
            to <code className="bg-white/60 px-1 rounded">/outputs/</code> is synced to
            GCS and registered as File records on the run after completion.
          </div>
          <div>
            <span className="font-semibold">Logs:</span> by default the run page tails
            pod stdout/stderr. If you set a log file path below (e.g.{" "}
            <code className="bg-white/60 px-1 rounded">/outputs/my-script.log</code>),
            that file replaces the default view post-completion. The OOM detector
            (SIGKILL/exit 137) is automatic.
          </div>
          <div>
            <span className="font-semibold">Entrypoint:</span> the entrypoint command
            below runs from the working directory above. Use the same form you would in
            a shell — for example, <code className="bg-white/60 px-1 rounded">bash run.sh</code>{" "}
            or <code className="bg-white/60 px-1 rounded">python main.py</code>.
          </div>
        </div>
      )}
    </div>
  );
}
