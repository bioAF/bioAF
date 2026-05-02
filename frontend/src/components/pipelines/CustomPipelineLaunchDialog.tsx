"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";
import type {
  CustomPipelineDetail,
  CustomPipelineVariable,
  CustomPipelineVersion,
  ExperimentListResponse,
  FileListResponse,
  FileResponse,
  ProjectListResponse,
} from "@/lib/types";

interface EnvVersionOption {
  env_id: number;
  env_name: string;
  version_id: number;
  version_number: number;
  status: string;
}

interface RepoLookup {
  id: number;
  display_name: string;
  git_ssh_url: string;
}

interface Props {
  pipeline: CustomPipelineDetail;
  envOptionsById: Map<number, EnvVersionOption>;
  repoById: Map<number, RepoLookup>;
  onClose: () => void;
  onLaunched: (runId: number) => void;
}

interface ProjectOption {
  id: number;
  name: string;
}

interface ExperimentOption {
  id: number;
  name: string;
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

function defaultVariableValues(
  variables: CustomPipelineVariable[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const v of variables) {
    if (v.default_value != null) {
      out[v.variable_name] = v.default_value;
    } else if (v.variable_type === "boolean") {
      out[v.variable_name] = "false";
    } else {
      out[v.variable_name] = "";
    }
  }
  return out;
}

export function CustomPipelineLaunchDialog({
  pipeline,
  envOptionsById,
  repoById,
  onClose,
  onLaunched,
}: Props) {
  const activeVersions = useMemo(
    () => pipeline.versions.filter((v) => v.status === "active"),
    [pipeline.versions],
  );

  const latestActive = activeVersions[0] ?? null;

  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(
    latestActive?.id ?? null,
  );
  const [showVersionModal, setShowVersionModal] = useState(false);
  const [expandedVersionIds, setExpandedVersionIds] = useState<Set<number>>(new Set());

  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [experiments, setExperiments] = useState<ExperimentOption[]>([]);
  const [files, setFiles] = useState<FileResponse[]>([]);
  const [sampleNames, setSampleNames] = useState<Record<number, string>>({});
  const [loadingFiles, setLoadingFiles] = useState(false);

  const [projectId, setProjectId] = useState<number | null>(null);
  const [experimentId, setExperimentId] = useState<number | null>(null);
  const [selectedFileIds, setSelectedFileIds] = useState<number[]>([]);

  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const [variableErrors, setVariableErrors] = useState<Record<string, string>>({});

  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const selectedVersion = useMemo(
    () => activeVersions.find((v) => v.id === selectedVersionId) ?? null,
    [activeVersions, selectedVersionId],
  );

  // Seed variable values whenever the chosen version changes.
  useEffect(() => {
    if (selectedVersion) {
      setVariableValues(defaultVariableValues(selectedVersion.variables));
      setVariableErrors({});
    }
  }, [selectedVersion]);

  // Load projects on open.
  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<ProjectListResponse>("/api/projects?page_size=100");
        setProjects(data.projects.map((p) => ({ id: p.id, name: p.name })));
      } catch {
        setProjects([]);
      }
    })();
  }, []);

  // Load experiments when project changes.
  useEffect(() => {
    setExperimentId(null);
    if (projectId == null) {
      setExperiments([]);
      return;
    }
    void (async () => {
      try {
        const data = await api.get<ExperimentListResponse>(
          `/api/experiments?project_id=${projectId}&page_size=200`,
        );
        setExperiments(
          data.experiments.map((e) => ({ id: e.id, name: e.name })),
        );
      } catch {
        setExperiments([]);
      }
    })();
  }, [projectId]);

  // Load files when target changes.
  useEffect(() => {
    setSelectedFileIds([]);
    setFiles([]);
    setSampleNames({});

    const params = new URLSearchParams();
    if (experimentId != null) params.set("experiment_id", String(experimentId));
    else if (projectId != null) params.set("project_id", String(projectId));
    params.set("page_size", "500");

    setLoadingFiles(true);
    void (async () => {
      try {
        const data = await api.get<FileListResponse>(`/api/files?${params}`);
        setFiles(data.files);

        if (experimentId != null) {
          const sampleIds = new Set<number>();
          for (const file of data.files) {
            for (const sid of file.sample_ids || []) sampleIds.add(sid);
          }
          if (sampleIds.size > 0) {
            try {
              const samplesData = await api.get<{
                samples: { id: number; sample_id_unique: string | null }[];
              }>(`/api/experiments/${experimentId}/samples?page_size=500`);
              const names: Record<number, string> = {};
              for (const s of samplesData.samples) {
                names[s.id] = s.sample_id_unique || `Sample ${s.id}`;
              }
              setSampleNames(names);
            } catch {
              setSampleNames({});
            }
          }
        }
      } catch {
        setFiles([]);
      } finally {
        setLoadingFiles(false);
      }
    })();
  }, [projectId, experimentId]);

  function toggleVersionDetails(versionId: number) {
    setExpandedVersionIds((prev) => {
      const next = new Set(prev);
      if (next.has(versionId)) next.delete(versionId);
      else next.add(versionId);
      return next;
    });
  }

  function setVariableValue(name: string, value: string) {
    setVariableValues((prev) => ({ ...prev, [name]: value }));
    setVariableErrors((prev) => {
      const { [name]: _omit, ...rest } = prev;
      return rest;
    });
  }

  function validateVariables(): boolean {
    if (!selectedVersion) return false;
    const errors: Record<string, string> = {};
    for (const v of selectedVersion.variables) {
      const raw = variableValues[v.variable_name] ?? "";
      const value = raw.trim();
      if (v.is_required && !value) {
        errors[v.variable_name] = "Required";
        continue;
      }
      if (!value) continue;
      if (v.variable_type === "number" && Number.isNaN(Number(value))) {
        errors[v.variable_name] = "Must be a number";
      } else if (
        v.variable_type === "boolean" &&
        !["true", "false"].includes(value.toLowerCase())
      ) {
        errors[v.variable_name] = "Must be true or false";
      }
    }
    setVariableErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function handleLaunch() {
    if (!selectedVersion) return;
    if (selectedFileIds.length === 0) {
      setLaunchError("Select at least one input file.");
      return;
    }
    if (!validateVariables()) return;

    setLaunching(true);
    setLaunchError(null);
    try {
      const variables = selectedVersion.variables
        .map((v) => ({
          variable_name: v.variable_name,
          variable_value: (variableValues[v.variable_name] ?? "").trim(),
        }))
        .filter((entry) => entry.variable_value !== "");

      const body = {
        version_id: selectedVersion.id,
        project_id: projectId,
        experiment_id: experimentId,
        input_file_ids: selectedFileIds,
        variables,
      };
      const run = await api.post<{ id: number }>(
        `/api/v1/custom-pipelines/${pipeline.id}/launch`,
        body,
      );
      onLaunched(run.id);
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : "Launch failed");
    } finally {
      setLaunching(false);
    }
  }

  const launchDisabled =
    launching ||
    selectedVersion == null ||
    selectedFileIds.length === 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <div className="px-6 py-4 border-b flex items-center justify-between">
          <h2 className="font-semibold text-lg">Launch {pipeline.name}</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 text-xl">
            ×
          </button>
        </div>

        <div className="overflow-y-auto p-6 space-y-6">
          {activeVersions.length === 0 ? (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-700">
              No active versions available. Create a version before launching.
            </div>
          ) : (
            <>
              {/* Version selection */}
              <section>
                <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
                  Pipeline Version
                </div>
                <div className="flex items-center justify-between bg-gray-50 border rounded px-3 py-2">
                  <div className="text-sm">
                    {selectedVersion ? (
                      <>
                        <span className="font-mono font-semibold">
                          v{selectedVersion.version_number}
                        </span>
                        <span className="text-gray-400 ml-2">
                          {new Date(selectedVersion.created_at).toLocaleDateString()}
                        </span>
                      </>
                    ) : (
                      <span className="text-gray-500">Select a version</span>
                    )}
                  </div>
                  <button
                    onClick={() => setShowVersionModal(true)}
                    className="text-sm text-bioaf-600 hover:underline"
                  >
                    Change Version
                  </button>
                </div>
              </section>

              {/* Target selection */}
              <section>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs uppercase tracking-wide text-gray-500 block mb-1">
                      Project (optional)
                    </label>
                    <select
                      value={projectId ?? ""}
                      onChange={(e) =>
                        setProjectId(e.target.value ? Number(e.target.value) : null)
                      }
                      className="w-full border rounded px-3 py-2 text-sm bg-white"
                    >
                      <option value="">All projects</option>
                      {projects.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs uppercase tracking-wide text-gray-500 block mb-1">
                      Experiment (optional)
                    </label>
                    <select
                      value={experimentId ?? ""}
                      onChange={(e) =>
                        setExperimentId(e.target.value ? Number(e.target.value) : null)
                      }
                      disabled={projectId == null}
                      className="w-full border rounded px-3 py-2 text-sm bg-white disabled:bg-gray-100 disabled:text-gray-400"
                    >
                      <option value="">
                        {projectId == null ? "Select a project first" : "All experiments"}
                      </option>
                      {experiments.map((e) => (
                        <option key={e.id} value={e.id}>
                          {e.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </section>

              {/* File picker */}
              <section>
                <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">
                  Input Files
                </div>
                {loadingFiles ? (
                  <p className="text-sm text-gray-500">Loading files...</p>
                ) : files.length === 0 ? (
                  <p className="text-sm text-gray-500">
                    No files available
                    {projectId != null || experimentId != null
                      ? " for the selected target."
                      : ". Adjust filters or upload files."}
                  </p>
                ) : (
                  <FileTreeSelector
                    files={files}
                    sampleNames={sampleNames}
                    onSelectionChange={setSelectedFileIds}
                  />
                )}
                {selectedFileIds.length > 0 && (
                  <p className="text-xs text-gray-500 mt-1">
                    {selectedFileIds.length} file(s) selected
                  </p>
                )}
              </section>

              {/* Variables */}
              {selectedVersion && selectedVersion.variables.length > 0 && (
                <section>
                  <div className="text-xs uppercase tracking-wide text-gray-500 mb-2">
                    Variables
                  </div>
                  <div className="space-y-3">
                    {selectedVersion.variables.map((v) => (
                      <VariableInput
                        key={v.id}
                        variable={v}
                        value={variableValues[v.variable_name] ?? ""}
                        error={variableErrors[v.variable_name]}
                        onChange={(value) => setVariableValue(v.variable_name, value)}
                      />
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {launchError && (
            <p className="text-sm text-red-600">{launchError}</p>
          )}
        </div>

        <div className="px-6 py-4 border-t flex items-center justify-end gap-2">
          <button onClick={onClose} className="border px-4 py-2 rounded text-sm">
            Cancel
          </button>
          <button
            onClick={handleLaunch}
            disabled={launchDisabled}
            className="bg-bioaf-600 text-white px-4 py-2 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
          >
            {launching ? "Launching..." : "Launch"}
          </button>
        </div>
      </div>

      {showVersionModal && (
        <VersionPickerModal
          versions={activeVersions}
          selectedVersionId={selectedVersionId}
          envOptionsById={envOptionsById}
          repoById={repoById}
          expandedVersionIds={expandedVersionIds}
          onToggleDetails={toggleVersionDetails}
          onSelect={(id) => {
            setSelectedVersionId(id);
            setShowVersionModal(false);
          }}
          onClose={() => setShowVersionModal(false)}
        />
      )}
    </div>
  );
}

function VariableInput({
  variable,
  value,
  error,
  onChange,
}: {
  variable: CustomPipelineVariable;
  value: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="text-sm text-gray-700 font-mono block mb-1">
        {variable.variable_name}
        {variable.is_required && <span className="text-red-500 ml-1">*</span>}
        <span className="text-xs text-gray-400 ml-2 font-sans">
          ({variable.variable_type})
        </span>
      </label>
      {variable.variable_type === "boolean" ? (
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={value.toLowerCase() === "true"}
            onChange={(e) => onChange(e.target.checked ? "true" : "false")}
          />
          {value.toLowerCase() === "true" ? "true" : "false"}
        </label>
      ) : variable.variable_type === "number" ? (
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      )}
      {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  );
}

function VersionPickerModal({
  versions,
  selectedVersionId,
  envOptionsById,
  repoById,
  expandedVersionIds,
  onToggleDetails,
  onSelect,
  onClose,
}: {
  versions: CustomPipelineVersion[];
  selectedVersionId: number | null;
  envOptionsById: Map<number, EnvVersionOption>;
  repoById: Map<number, RepoLookup>;
  expandedVersionIds: Set<number>;
  onToggleDetails: (id: number) => void;
  onSelect: (id: number) => void;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        <div className="px-6 py-4 border-b flex items-center justify-between">
          <h3 className="font-semibold">Select Pipeline Version</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 text-xl">
            ×
          </button>
        </div>
        <div className="overflow-y-auto p-4 space-y-2">
          {versions.length === 0 && (
            <p className="text-sm text-gray-500">No active versions.</p>
          )}
          {versions.map((version, idx) => {
            const previous = versions[idx + 1] ?? null;
            const change = changeLabel(version, previous);
            const expanded = expandedVersionIds.has(version.id);
            const env = envOptionsById.get(version.environment_version_id);
            const repo =
              version.github_repo_id != null
                ? repoById.get(version.github_repo_id)
                : null;
            const isSelected = version.id === selectedVersionId;
            return (
              <div
                key={version.id}
                className={`border rounded-lg p-3 ${
                  isSelected ? "border-bioaf-500 bg-bioaf-50" : "border-gray-200"
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => onSelect(version.id)}
                    className="flex-1 text-left"
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-semibold">
                        v{version.version_number}
                      </span>
                      <span
                        className={`px-2 py-0.5 text-xs rounded-full ${TONE_CLASSES[change.tone]}`}
                      >
                        {change.label}
                      </span>
                      <span className="text-xs text-gray-400">
                        {new Date(version.created_at).toLocaleString()}
                      </span>
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => onToggleDetails(version.id)}
                    className="text-sm text-bioaf-600 hover:underline"
                  >
                    {expanded ? "Hide Details" : "Show Details"}
                  </button>
                </div>

                {expanded && (
                  <div className="mt-3 pl-2 border-l-2 border-gray-200 space-y-2 text-sm">
                    <div>
                      <span className="text-xs text-gray-500 uppercase">Code source: </span>
                      {version.code_source_type === "github_repo" ? (
                        <span className="font-mono text-gray-700">
                          {repo
                            ? `${repo.display_name} (${repo.git_ssh_url})`
                            : `GitHub repo #${version.github_repo_id}`}
                        </span>
                      ) : version.code_source_type === "code_blob" ? (
                        <span>Code blob</span>
                      ) : (
                        <span>Inline command</span>
                      )}
                    </div>
                    <div>
                      <span className="text-xs text-gray-500 uppercase">Entrypoint: </span>
                      <code className="font-mono bg-gray-100 px-2 py-0.5 rounded">
                        {version.entrypoint_command}
                      </code>
                    </div>
                    <div>
                      <span className="text-xs text-gray-500 uppercase">Environment: </span>
                      {env
                        ? `${env.env_name} v${env.version_number}`
                        : `Environment version #${version.environment_version_id}`}
                    </div>
                    <div>
                      <span className="text-xs text-gray-500 uppercase">Resources: </span>
                      <span className="font-mono">
                        CPU {version.cpu_request} / Memory {version.memory_request}
                      </span>
                    </div>
                    {version.variables.length > 0 && (
                      <div>
                        <span className="text-xs text-gray-500 uppercase">Variables: </span>
                        <span className="text-gray-700">
                          {version.variables
                            .map(
                              (v) =>
                                `${v.variable_name}${v.is_required ? "*" : ""}: ${v.variable_type}`,
                            )
                            .join(", ")}
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="px-6 py-3 border-t flex items-center justify-end">
          <button onClick={onClose} className="border px-4 py-2 rounded text-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
