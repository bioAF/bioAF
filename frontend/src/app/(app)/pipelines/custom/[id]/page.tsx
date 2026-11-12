"use client";

import { useToast } from "@/components/shared/Toast";
import { Modal } from "@/components/shared/Modal";
import { useConfirm } from "@/hooks/useConfirm";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { CustomPipelineLaunchDialog } from "@/components/pipelines/CustomPipelineLaunchDialog";
import { NewVersionForm } from "@/components/pipelines/custom/NewVersionForm";
import { VersionHistory } from "@/components/pipelines/custom/VersionHistory";
import { PipelineMetadataHeader, type MetadataDraft } from "@/components/pipelines/custom/PipelineMetadataHeader";
import {
  emptyVariableDraft,
  seedVersionDraft,
  versionRequest,
  type EnvVersionOption,
  type VariableDraft,
  type VersionDraft,
} from "@/components/pipelines/custom/versionDraft";
import { usePermissions } from "@/hooks/usePermissions";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import type {
  CustomPipelineDetail,
  EnvironmentDetailResponse,
  EnvironmentListResponse,
  GitHubRepo,
  GitHubRepoListResponse,
} from "@/lib/types";

interface PipelineOptions {
  envOptions: EnvVersionOption[];
  repos: GitHubRepo[];
}

/** Every pipeline template version (for names and the dropdown) and the registered repositories. */
async function loadOptions(signal: AbortSignal): Promise<PipelineOptions> {
  const [envList, repoList] = await Promise.all([
    api.get<EnvironmentListResponse>("/api/v1/environments?type=pipeline", { signal }),
    api
      .get<GitHubRepoListResponse>("/api/v1/github-repos", { signal })
      .catch(() => ({ repos: [] as GitHubRepo[], total: 0 })),
  ]);
  const envDetails = await Promise.all(
    envList.environments.map((env) =>
      api.get<EnvironmentDetailResponse>(`/api/v1/environments/${env.id}`, { signal }).catch(() => null),
    ),
  );
  const envOptions: EnvVersionOption[] = [];
  for (const env of envDetails) {
    if (!env) continue;
    for (const v of env.versions) {
      envOptions.push({
        env_id: env.id,
        env_name: env.name,
        version_id: v.id,
        version_number: v.version_number,
        status: v.status,
      });
    }
  }
  return { envOptions, repos: repoList.repos };
}

export default function CustomPipelineDetailPage() {
  const params = useParams();
  const id = String(params.id);
  // One owner per pipeline: nothing read or drafted for one pipeline reaches another.
  return <CustomPipelineDetailOwner key={id} pipelineId={Number(id)} />;
}

function CustomPipelineDetailOwner({ pipelineId }: { pipelineId: number }) {
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const searchParams = useSearchParams();
  const autoLaunch = searchParams.get("launch") === "1";
  const autoNewVersion = searchParams.get("newVersion") === "1";
  // Carried here by the catalog from the experiment the user pressed Launch on.
  const experimentParam = searchParams.get("experiment");
  const launchedFromExperimentId =
    experimentParam && /^\d+$/.test(experimentParam) ? Number(experimentParam) : null;
  const { canAccess, loading: permsLoading } = usePermissions();

  const detail = useResource<CustomPipelineDetail>(
    Number.isNaN(pipelineId) ? null : `custom-pipeline:${pipelineId}`,
    (signal) => api.get<CustomPipelineDetail>(`/api/v1/custom-pipelines/${pipelineId}`, { signal }),
  );
  const options = useResource<PipelineOptions>("custom-pipeline:options", loadOptions);
  const pipeline = detail.data ?? null;
  const envOptions = useMemo(() => options.data?.envOptions ?? [], [options.data]);
  const repos = useMemo(() => options.data?.repos ?? [], [options.data]);
  const loadError = detail.data === undefined ? detail.error : options.data === undefined ? options.error : null;

  const [meta, setMeta] = useState<MetadataDraft | null>(null);
  const metaGuard = useSubmitGuard();
  const [metaError, setMetaError] = useState<string | null>(null);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showLaunchDialog, setShowLaunchDialog] = useState(false);
  const [expandedVersionIds, setExpandedVersionIds] = useState<Set<number>>(new Set());

  // The new-version draft: seeded when the form opens and the data it copies is here, then the
  // user's until the form closes. A reload of the pipeline never replaces it.
  const [showNewVersionForm, setShowNewVersionForm] = useState(false);
  const [versionForm, setVersionDraft] = useState<VersionDraft | null>(null);
  const versionGuard = useSubmitGuard();
  const [versionError, setVersionError] = useState<string | null>(null);

  useEffect(() => {
    if (showNewVersionForm && versionForm === null && pipeline && options.data) {
      setVersionDraft(seedVersionDraft(pipeline, options.data.envOptions));
    }
  }, [showNewVersionForm, versionForm, pipeline, options.data]);

  // ?launch=1 and ?newVersion=1 act on the first answer only, not on every reload.
  const deepLinksApplied = useRef(false);
  useEffect(() => {
    if (!pipeline || deepLinksApplied.current) return;
    deepLinksApplied.current = true;
    if (autoLaunch && pipeline.versions.some((v) => v.status === "active")) setShowLaunchDialog(true);
    if (autoNewVersion) setShowNewVersionForm(true);
  }, [pipeline, autoLaunch, autoNewVersion]);

  const envOptionsById = useMemo(() => {
    const map = new Map<number, EnvVersionOption>();
    for (const o of envOptions) map.set(o.version_id, o);
    return map;
  }, [envOptions]);

  const readyEnvOptions = useMemo(() => envOptions.filter((o) => o.status === "ready"), [envOptions]);

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

  function handleSaveMeta() {
    if (!pipeline || !meta) return;
    void metaGuard.run(async () => {
      setMetaError(null);
      try {
        await api.put(`/api/v1/custom-pipelines/${pipeline.id}`, {
          name: meta.name,
          description: meta.description || null,
        });
        await detail.refresh();
        setMeta(null);
      } catch (err) {
        setMetaError(err instanceof Error ? err.message : "Failed to save");
      }
    });
  }

  async function handleDelete() {
    if (!pipeline) return;
    setDeleting(true);
    try {
      await api.delete(`/api/v1/custom-pipelines/${pipeline.id}`);
      router.push("/pipelines/custom");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
      setDeleting(false);
    }
  }

  async function handleDeprecate(versionId: number) {
    if (!pipeline) return;
    const ok = await confirm({
      title: "Deprecate this version?",
      message: "It will no longer be launchable.",
      confirmLabel: "Deprecate",
    });
    if (!ok) return;
    try {
      await api.post(`/api/v1/custom-pipelines/${pipeline.id}/versions/${versionId}/deprecate`, {});
      await detail.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to deprecate");
    }
  }

  function updateDraft(update: (prev: VersionDraft) => VersionDraft) {
    setVersionDraft((prev) => (prev ? update(prev) : prev));
  }

  function closeVersionForm() {
    setShowNewVersionForm(false);
    setVersionDraft(null);
    setVersionError(null);
  }

  function handleCreateVersion() {
    if (!pipeline || !versionForm) return;
    const request = versionRequest(versionForm);
    if (!request.ok) {
      setVersionError(request.error);
      return;
    }
    void versionGuard.run(async () => {
      setVersionError(null);
      try {
        await api.post(`/api/v1/custom-pipelines/${pipeline.id}/versions`, request.body);
        closeVersionForm();
        await detail.refresh();
      } catch (err) {
        setVersionError(err instanceof Error ? err.message : "Failed to create version");
      }
    });
  }

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {detail.loading ? (
        <ContentLoading />
      ) : loadError || !pipeline ? (
        <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
          {loadError ? (loadError instanceof Error ? loadError.message : "Failed to load pipeline") : "Pipeline not found"}
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

          <PipelineMetadataHeader
            pipeline={pipeline}
            draft={meta}
            onDraftChange={setMeta}
            saving={metaGuard.busy}
            error={metaError}
            onEdit={() => {
              setMeta({ name: pipeline.name, description: pipeline.description ?? "" });
              setMetaError(null);
            }}
            onSave={handleSaveMeta}
            onCancel={() => {
              setMeta(null);
              setMetaError(null);
            }}
            canLaunch={canLaunch}
            canEdit={canEdit}
            canDelete={canDelete}
            onLaunch={() => setShowLaunchDialog(true)}
            onDelete={() => setShowDeleteConfirm(true)}
          />

          <Card padding="none">
            <div className="px-6 py-4 border-b flex items-center justify-between">
              <h2 className="font-semibold text-lg">Versions</h2>
              {canEdit && (
                <Button size="sm"
                  onClick={() => (showNewVersionForm ? closeVersionForm() : setShowNewVersionForm(true))}>
                  {showNewVersionForm ? "Cancel" : "New Version"}
                </Button>
              )}
            </div>

            {showNewVersionForm && canEdit && (
              <div className="p-6 border-b bg-gray-50">
                <h3 className="font-semibold mb-4">New Version</h3>
                {versionForm ? (
                  <>
                    <NewVersionForm
                      versionForm={versionForm}
                      setVersionForm={(next) =>
                        updateDraft((prev) => (typeof next === "function" ? next(prev) : next))
                      }
                      readyEnvOptions={readyEnvOptions}
                      repos={repos}
                      addVariable={() =>
                        updateDraft((prev) => ({ ...prev, variables: [...prev.variables, emptyVariableDraft()] }))
                      }
                      updateVariable={(idx: number, patch: Partial<VariableDraft>) =>
                        updateDraft((prev) => ({
                          ...prev,
                          variables: prev.variables.map((v, i) => (i === idx ? { ...v, ...patch } : v)),
                        }))
                      }
                      removeVariable={(idx: number) =>
                        updateDraft((prev) => ({ ...prev, variables: prev.variables.filter((_, i) => i !== idx) }))
                      }
                    />
                    {versionError && (
                      <p className="text-sm text-red-600 mt-3">{versionError}</p>
                    )}
                    <div className="mt-4 flex gap-2">
                      <Button onClick={handleCreateVersion} busy={versionGuard.busy} busyLabel="Creating...">
                        Create Version
                      </Button>
                      <button onClick={closeVersionForm} className="border px-4 py-2 rounded text-sm">
                        Cancel
                      </button>
                    </div>
                  </>
                ) : (
                  <ContentLoading />
                )}
              </div>
            )}

            <VersionHistory
              pipeline={pipeline}
              envOptionsById={envOptionsById}
              repoById={repoById}
              expandedVersionIds={expandedVersionIds}
              toggleVersion={toggleVersion}
              canEdit={canEdit}
              onDeprecate={(versionId) => void handleDeprecate(versionId)}
            />
          </Card>

          {showLaunchDialog && (
            <CustomPipelineLaunchDialog
              pipeline={pipeline}
              envOptionsById={envOptionsById}
              repoById={repoById}
              initialExperimentId={launchedFromExperimentId}
              onClose={() => setShowLaunchDialog(false)}
              onLaunched={(runId) => {
                setShowLaunchDialog(false);
                router.push(`/pipelines/runs/${runId}`);
              }}
            />
          )}

          <Modal
            open={showDeleteConfirm}
            title="Delete Pipeline"
            onClose={() => setShowDeleteConfirm(false)}
            size="sm"
            footer={
              <>
              <Button variant="danger"
                onClick={handleDelete}
                disabled={deleting}>
                {deleting ? "Deleting..." : "Delete"}
              </Button>
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="border py-2 rounded text-sm"
              >
                Cancel
              </button>
              </>
            }
          >
            <p className="text-sm text-gray-600 mb-4">
              This will soft-delete <strong>{pipeline.name}</strong>. Existing pipeline
              runs are preserved.
            </p>
          </Modal>
        </>
      )}
    </main>
  );
}
