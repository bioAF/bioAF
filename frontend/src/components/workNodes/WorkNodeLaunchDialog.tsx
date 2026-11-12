"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { Alert } from "@/components/shared/Alert";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Input";
import { api } from "@/lib/api";
import { isAbortError } from "@/lib/abort";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import {
  DEFAULT_PROJECTS_PATH,
  PROJECTS_DENIED_MESSAGE,
  projectOptionsFrom,
  projectOptionsFromError,
  type ProjectOptions,
} from "@/lib/projectOptions";
import { resolveWorkNodeProfiles } from "@/lib/workNodeProfiles";
import type { WorkNodeRecreatePrefill } from "@/lib/sessionRecreate";
import { useLaunchSelection } from "@/components/launch/useLaunchSelection";
import type {
  EnvironmentListResponse,
  EnvironmentResponse,
  Experiment,
  ExperimentListResponse,
  GitHubRepo,
  MachineType,
  ProjectListResponse,
  WorkNodeLaunchRequest,
} from "@/lib/types";

const CATEGORY_LABELS: Record<string, string> = {
  standard: "Standard",
  "high-memory": "High Memory",
  gpu: "GPU",
};

interface LaunchOptions {
  projects: ProjectOptions;
  experiments: Experiment[];
  machineTypes: MachineType[];
  environments: EnvironmentResponse[];
}

/**
 * The form's lists. Projects load apart from the rest: a role that may not list them still
 * launches on an experiment, and the picker says why.
 */
async function loadLaunchOptions(signal: AbortSignal): Promise<LaunchOptions> {
  const [projects, expData, machineTypes, envData] = await Promise.all([
    api.get<ProjectListResponse>(DEFAULT_PROJECTS_PATH, { signal }).then(projectOptionsFrom, (e) => {
      if (isAbortError(e)) throw e;
      return projectOptionsFromError(e);
    }),
    api.get<ExperimentListResponse>("/api/experiments?page_size=100", { signal }),
    api.get<MachineType[]>("/api/v1/work-nodes/machine-types", { signal }),
    api.get<EnvironmentListResponse>("/api/v1/environments?type=work_node", { signal }),
  ]);
  return { projects, experiments: expData.experiments, machineTypes, environments: envData.environments };
}

/**
 * The work-node launch dialog. Mounted for one opening: its draft (machine, template, scope,
 * files, repositories, step) and its option reads end when it closes. Sending a launch hands it
 * to the page, which watches provisioning and keeps the outcome after the dialog is gone.
 */
export function WorkNodeLaunchDialog({
  prefill,
  repos,
  onClose,
  onLaunch,
}: {
  prefill: WorkNodeRecreatePrefill | null;
  repos: GitHubRepo[];
  onClose: () => void;
  onLaunch: (request: WorkNodeLaunchRequest) => void;
}) {
  const toast = useToast();
  const selection = useLaunchSelection({ templateError: "Could not select that template." });
  const {
    scope: scopeType,
    experimentId: selectedExperimentId,
    projectId: selectedProjectId,
    environmentId: selectedEnvId,
    versionId: selectedVersionId,
    templateDetail: envDetail,
    fileIds: selectedFileIds,
  } = selection;
  const { files: experimentFiles, sampleNames, failed: experimentFilesFailed } = selection.files;
  const { begin, end, presetEnvironment } = selection;

  useEffect(() => {
    begin(
      prefill
        ? {
            prefill: {
              scope: "project",
              experimentId: null,
              projectId: prefill.project_id,
              environmentId: null,
              versionId: prefill.environment_version_id,
              fileIds: prefill.input_file_ids,
            },
          }
        : {},
    );
    return end;
    // One opening per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const options = useResource<LaunchOptions>(
    selection.opening ? `work-node-launch:${selection.opening}` : null,
    loadLaunchOptions,
    {
      onSuccess: (data) => {
        if (data.projects.status === "failed") toast.error(loadFailureMessage("Projects"));
      },
      onError: (e) => {
        logError("loading the work node form options", e);
        toast.error(loadFailureMessage("Form options"));
      },
    },
  );
  const projects = options.data?.projects.projects ?? [];
  const projectsDenied = options.data?.projects.status === "denied";
  const experiments = options.data?.experiments ?? [];
  const machineTypes = options.data?.machineTypes ?? [];
  const environments = options.data?.environments ?? [];
  const workNodeProfiles = resolveWorkNodeProfiles(machineTypes);

  // A recreated node names its template once the list arrives; the prefilled version is kept.
  const prefillVersion = prefill?.environment_version_id ?? null;
  const environmentList = options.data?.environments;
  useEffect(() => {
    if (!prefillVersion || selectedEnvId !== null || selectedVersionId !== prefillVersion) return;
    const owner = environmentList?.find((env) => env.latest_version?.id === prefillVersion);
    if (owner) presetEnvironment(owner.id, prefillVersion);
  }, [environmentList, prefillVersion, selectedEnvId, selectedVersionId, presetEnvironment]);

  const [launchStep, setLaunchStep] = useState(1);
  const [selectedMachineType, setSelectedMachineType] = useState<string>(prefill?.machine_type ?? "");
  const [showAdvancedMachines, setShowAdvancedMachines] = useState(false);
  const [selectedRepoIds, setSelectedRepoIds] = useState<number[]>(prefill?.github_repo_ids ?? []);
  const [showConfirmLaunch, setShowConfirmLaunch] = useState(false);
  const guard = useSubmitGuard();

  function handleScopeChange(scope: "experiment" | "project") {
    selection.setScope(scope);
  }

  function handleProjectSelect(projectId: number) {
    selection.selectProject(projectId);
  }

  function handleExperimentSelect(experimentId: number) {
    // A standalone experiment has no project; the node is then tied to the experiment alone.
    const exp = experiments.find((e) => e.id === experimentId);
    selection.selectExperiment(experimentId, exp?.project?.id ?? null);
  }

  function handleEnvSelect(envId: number) {
    selection.chooseEnvironment(envId);
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
    if (!scopeSelected || !selectedVersionId || !selectedMachineType || guard.busy) return;
    const missingFiles = selectedFileIds.length === 0;
    const missingRepos = repos.length > 0 && selectedRepoIds.length === 0;
    if (missingFiles || missingRepos) {
      setShowConfirmLaunch(true);
      return;
    }
    void performLaunch();
  }

  /** One launch per opening, through the confirmation too. */
  function performLaunch() {
    return guard.run(async () => {
      if (!scopeSelected || !selectedVersionId || !selectedMachineType) return;
      setShowConfirmLaunch(false);
      onLaunch({
        // project_id may be null for a standalone experiment; experiment_id ties
        // the node (and its outputs) to the experiment, like notebook sessions.
        project_id: selectedProjectId ?? undefined,
        experiment_id: scopeType === "experiment" ? (selectedExperimentId ?? undefined) : undefined,
        environment_version_id: selectedVersionId,
        machine_type: selectedMachineType,
        input_file_ids: selectedFileIds.length > 0 ? selectedFileIds : undefined,
        github_repo_ids: selectedRepoIds.length > 0 ? selectedRepoIds : undefined,
      });
    });
  }

  return (
    <>
      <Modal
        open
        title="Launch Work Node"
        onClose={onClose}
        size="xl"
        footer={
          <>
            {launchStep === 1 ? (
              <Button
                size="lg"
                className="flex-1"
                onClick={() => setLaunchStep(2)}
                disabled={!scopeSelected || !selectedVersionId || !selectedMachineType}
              >
                Next: Review
              </Button>
            ) : (
              <>
                <Button variant="secondary" size="lg" onClick={() => setLaunchStep(1)}>
                  Back
                </Button>
                <Button size="lg" className="flex-1" onClick={handleLaunch} disabled={guard.busy}>
                  Launch Work Node
                </Button>
              </>
            )}
            <Button variant="secondary" size="lg" onClick={onClose}>
              Cancel
            </Button>
          </>
        }
      >
        {/* Step indicator: 2 steps */}
        <div className="mb-4 flex gap-2">
          {[1, 2].map((s) => (
            <div
              key={s}
              className={`h-1 flex-1 rounded ${s <= launchStep ? "bg-bioaf-600" : "bg-gray-200"}`}
            />
          ))}
        </div>

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
                          ? "border-bioaf-500 bg-bioaf-50"
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
                  className="text-xs text-bioaf-600 hover:underline"
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
                                  ? "border-bioaf-500 bg-bioaf-50"
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
                <Alert tone="warning">
                  No work node templates found. Create one from the{" "}
                  <a href="/environments" className="underline font-medium">
                    Workbench Templates
                  </a>{" "}
                  page with type &quot;Work Node&quot;.
                </Alert>
              ) : (
                <div className="flex gap-3">
                  <Select
                    aria-label="Workbench template"
                    value={selectedEnvId || ""}
                    onChange={(e) =>
                      e.target.value ? handleEnvSelect(Number(e.target.value)) : null
                    }
                    width="auto"
                    className="flex-1"
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
                  </Select>
                  {envDetail &&
                    envDetail.versions.filter(
                      (v) => v.status === "ready" && v.image_uri
                    ).length > 0 && (
                      <Select
                        aria-label="Version"
                        value={selectedVersionId || ""}
                        onChange={(e) =>
                          selection.selectVersion(e.target.value ? Number(e.target.value) : null)
                        }
                        width="auto"
                        className="flex-1"
                      >
                        {envDetail.versions
                          .filter((v) => v.status === "ready" && v.image_uri)
                          .map((v) => (
                            <option key={v.id} value={v.id}>
                              v{v.version_number}.{v.build_number} (ready)
                            </option>
                          ))}
                      </Select>
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
                <Select
                  aria-label="Select experiment"
                  value={selectedExperimentId || ""}
                  onChange={(e) =>
                    e.target.value ? handleExperimentSelect(Number(e.target.value)) : null
                  }
                >
                  <option value="">No experiment</option>
                  {experiments.map((exp) => (
                    <option key={exp.id} value={exp.id}>
                      {exp.name}
                      {exp.code ? ` (${exp.code})` : ""}
                    </option>
                  ))}
                </Select>
              ) : (
                <Select
                  aria-label="Select project"
                  value={selectedProjectId || ""}
                  onChange={(e) =>
                    e.target.value ? handleProjectSelect(Number(e.target.value)) : null
                  }
                  disabled={projectsDenied}
                >
                  <option value="">No project</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.code ? ` (${p.code})` : ""}
                    </option>
                  ))}
                </Select>
              )}
              {scopeType === "project" && projectsDenied && (
                <p className="mt-1 text-xs text-gray-500">{PROJECTS_DENIED_MESSAGE}</p>
              )}
            </section>

            {scopeType === "experiment" && selectedExperimentId && (
              <section>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Input Files</h3>
                {experimentFilesFailed ? (
                  <p
                    data-testid="experiment-files-load-failed"
                    className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2"
                  >
                    {loadFailureMessage("This experiment's files")}
                  </p>
                ) : !selection.files.loaded ? null : experimentFiles.length === 0 ? (
                  <p className="text-xs text-gray-500">
                    No files found for this experiment.
                  </p>
                ) : (
                  <FileTreeSelector
                    key={`${selection.opening}:${selectedExperimentId}`}
                    files={experimentFiles}
                    sampleNames={sampleNames}
                    onSelectionChange={selection.setFileIds}
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

    {showConfirmLaunch && (
      <Modal
        open
        title="Launch without inputs?"
        onClose={() => setShowConfirmLaunch(false)}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowConfirmLaunch(false)}>
              Cancel
            </Button>
            <Button onClick={() => void performLaunch()}>Launch anyway</Button>
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
    </>
  );
}
