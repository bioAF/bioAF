"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useToast } from "@/components/shared/Toast";
import { logError } from "@/lib/errorReporting";
import type { EnvironmentDetailResponse, FileListResponse, FileResponse } from "@/lib/types";

/**
 * The selections a compute launch dialog shares between notebooks and work nodes: what the
 * session is linked to (an experiment or a project), which workbench template and version it
 * runs, and which of the experiment's files it mounts.
 *
 * Each opening of the dialog owns its reads. The experiment's files are read for the opening and
 * the experiment that asked, and a template's versions for the opening and the template that
 * asked, so a late answer for an earlier choice, or an earlier opening, is never shown. A
 * recreated session's prefill is kept: option reads that arrive later only fill what is still
 * unset. Payloads, resource choices and confirmation rules stay with each launch flow.
 */

export type LaunchScope = "experiment" | "project";

export interface LaunchPrefill {
  scope: LaunchScope;
  experimentId: number | null;
  projectId: number | null;
  environmentId: number | null;
  versionId: number | null;
  fileIds: number[];
}

export interface ExperimentFiles {
  files: FileResponse[];
  sampleNames: Record<number, string>;
}

/** An experiment's files, with the sample labels the file tree groups them by. */
export async function loadExperimentFiles(experimentId: number, signal: AbortSignal): Promise<ExperimentFiles> {
  const data = await api.get<FileListResponse>(`/api/experiments/${experimentId}/files?page_size=500`, { signal });
  const sampleIds = new Set<number>();
  for (const file of data.files) {
    for (const sid of file.sample_ids || []) sampleIds.add(sid);
  }
  const sampleNames: Record<number, string> = {};
  if (sampleIds.size > 0) {
    try {
      const samples = await api.get<{ samples: { id: number; external_id: string | null }[] }>(
        `/api/experiments/${experimentId}/samples?page_size=500`,
        { signal },
      );
      for (const s of samples.samples) sampleNames[s.id] = s.external_id || `Sample ${s.id}`;
    } catch (e) {
      if (signal.aborted) throw e;
      // Labels are a convenience; the files still mount without them.
    }
  }
  return { files: data.files, sampleNames };
}

/** The version a template defaults to: its first ready version with a built image. */
export function defaultVersionId(detail: EnvironmentDetailResponse): number | null {
  return detail.versions.find((v) => v.status === "ready" && v.image_uri)?.id ?? null;
}

interface Selection {
  scope: LaunchScope;
  experimentId: number | null;
  projectId: number | null;
  environmentId: number | null;
  versionId: number | null;
  /** Whether the template's versions are read (a user's choice) or the version is taken as given. */
  resolveVersions: boolean;
  fileIds: number[];
}

const EMPTY: Selection = {
  scope: "experiment",
  experimentId: null,
  projectId: null,
  environmentId: null,
  versionId: null,
  resolveVersions: false,
  fileIds: [],
};

export function useLaunchSelection({ templateError }: { templateError: string }) {
  const toast = useToast();
  // 0 while closed; each opening (or recreate) takes the next number.
  const [opening, setOpening] = useState(0);
  const openings = useRef(0);
  const [selection, setSelection] = useState<Selection>(EMPTY);
  const { scope, experimentId, environmentId, resolveVersions } = selection;

  const files = useResource<ExperimentFiles>(
    opening && scope === "experiment" && experimentId ? `launch:${opening}:files:${experimentId}` : null,
    (signal) => loadExperimentFiles(experimentId!, signal),
    { onError: (e) => logError(`loading the files for experiment ${experimentId}`, e) },
  );

  const template = useResource<EnvironmentDetailResponse>(
    opening && environmentId && resolveVersions ? `launch:${opening}:template:${environmentId}` : null,
    (signal) => api.get<EnvironmentDetailResponse>(`/api/v1/environments/${environmentId}`, { signal }),
    {
      onSuccess: (detail) =>
        setSelection((s) => (s.versionId === null ? { ...s, versionId: defaultVersionId(detail) } : s)),
      onError: (e) => toast.error(e instanceof Error ? e.message : templateError),
    },
  );

  /**
   * Starts an opening. With a prefill (recreate) the selection is replaced by it; with `keep`
   * the previous choices carry over and only the file selection starts empty; otherwise the
   * selection starts empty.
   */
  const begin = useCallback((options: { prefill?: LaunchPrefill; keep?: boolean } = {}) => {
    openings.current += 1;
    setOpening(openings.current);
    setSelection((s) => {
      if (options.prefill) return { ...options.prefill, resolveVersions: false };
      if (options.keep) return { ...s, fileIds: [] };
      return EMPTY;
    });
  }, []);

  const end = useCallback(() => {
    openings.current += 1;
    setOpening(0);
  }, []);

  /** Whether `opening` (captured when an action started) is still the dialog's current opening. */
  const isCurrent = useCallback((captured: number) => captured !== 0 && captured === openings.current, []);

  function setScope(next: LaunchScope) {
    setSelection((s) => (s.scope === next ? s : { ...s, scope: next, experimentId: null, projectId: null, fileIds: [] }));
  }

  /** `projectId` is given when the experiment's project should become the launch's project. */
  function selectExperiment(id: number | null, projectId?: number | null) {
    setSelection((s) => ({
      ...s,
      experimentId: id,
      projectId: projectId === undefined ? s.projectId : projectId,
      fileIds: [],
    }));
  }

  function selectProject(id: number | null) {
    setSelection((s) => ({ ...s, projectId: id }));
  }

  /** A user's choice of template: its versions are read and the default version applied. */
  function chooseEnvironment(id: number) {
    setSelection((s) => ({ ...s, environmentId: id, versionId: null, resolveVersions: true }));
  }

  /** A template set without asking (a default or a prefill): the version is taken as given. */
  function presetEnvironment(id: number | null, versionId: number | null) {
    setSelection((s) => ({ ...s, environmentId: id, versionId, resolveVersions: false }));
  }

  function selectVersion(id: number | null) {
    setSelection((s) => ({ ...s, versionId: id }));
  }

  const setFileIds = useCallback((ids: number[]) => setSelection((s) => ({ ...s, fileIds: ids })), []);

  return {
    opening,
    begin,
    end,
    isCurrent,
    ...selection,
    setScope,
    selectExperiment,
    selectProject,
    chooseEnvironment,
    presetEnvironment,
    selectVersion,
    setFileIds,
    files: {
      files: files.data?.files ?? [],
      sampleNames: files.data?.sampleNames ?? {},
      loaded: files.data !== undefined,
      // An empty list reached from a failed read is not "no files": the picker says it failed.
      failed: files.data === undefined && files.error != null,
    },
    templateDetail: template.data ?? null,
  };
}

export type LaunchSelection = ReturnType<typeof useLaunchSelection>;
