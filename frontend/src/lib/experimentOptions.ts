import { api } from "@/lib/api";
import type { Experiment, ExperimentListResponse } from "@/lib/types";

// The experiments list holds at most 100 a page (backend/app/api/experiments.py); samples allow more.
export const EXPERIMENT_PAGE_SIZE = 100;
const SAMPLE_PAGE_SIZE = 200;

export interface SampleOption {
  id: number;
  external_id: string | null;
  organism: string | null;
  tissue_type: string | null;
  qc_status: string | null;
  experiment_id: number;
  experiment_name: string;
}

interface SampleOptionListResponse {
  samples: SampleOption[];
  total: number;
}

/**
 * A picker has no pagination of its own, so a single page would hide the oldest
 * records with nothing to tell the user they exist.
 */
async function readEveryPage<T extends { id: number }>(
  path: string,
  size: number,
  page: (url: string) => Promise<{ items: T[]; total: number }>,
): Promise<T[]> {
  const byId = new Map<number, T>();
  for (let n = 1; ; n++) {
    const { items, total } = await page(`${path}page=${n}&page_size=${size}`);
    for (const item of items) byId.set(item.id, item);
    if (items.length < size || n * size >= total) return [...byId.values()];
  }
}

/** Every experiment for a picker, or every one in a project. */
export function listAllExperiments(projectId?: number | string): Promise<Experiment[]> {
  const scope = projectId ? `project_id=${projectId}&` : "";
  return readEveryPage(`/api/experiments?${scope}`, EXPERIMENT_PAGE_SIZE, async (url) => {
    const data = await api.get<ExperimentListResponse>(url);
    return { items: data.experiments, total: data.total };
  });
}

/** Every sample in the Organization, each with its experiment's name. */
export function listAllSamples(): Promise<SampleOption[]> {
  return readEveryPage("/api/samples?", SAMPLE_PAGE_SIZE, async (url) => {
    const data = await api.get<SampleOptionListResponse>(url);
    return { items: data.samples, total: data.total };
  });
}

/** What a picker needs to show and choose an experiment. */
export interface ExperimentOption {
  id: number;
  name: string;
  code?: string | null;
  project?: { id: number; name: string } | null;
}

export interface ExperimentPage {
  experiments: ExperimentOption[];
  total: number;
}

/** One page of the Organization's experiments (newest first), by name and project, for a picker. */
export function experimentPage(
  query: { name: string; projectId: number | null; page: number },
  signal?: AbortSignal,
): Promise<ExperimentPage> {
  const params = new URLSearchParams({ page: String(query.page), page_size: String(EXPERIMENT_PAGE_SIZE) });
  if (query.name) params.set("name", query.name);
  if (query.projectId != null) params.set("project_id", String(query.projectId));
  return api.get<ExperimentPage>(`/api/experiments?${params}`, { signal });
}

/** One experiment by id: the label of a selection that is not on the page shown. */
export function experimentById(id: number, signal?: AbortSignal): Promise<ExperimentOption> {
  return api.get<ExperimentOption>(`/api/experiments/${id}`, { signal });
}
