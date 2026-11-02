import { api } from "@/lib/api";
import type { Experiment, ExperimentListResponse } from "@/lib/types";

const PAGE_SIZE = 200;

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
  page: (url: string) => Promise<{ items: T[]; total: number }>,
): Promise<T[]> {
  const byId = new Map<number, T>();
  for (let n = 1; ; n++) {
    const { items, total } = await page(`${path}page=${n}&page_size=${PAGE_SIZE}`);
    for (const item of items) byId.set(item.id, item);
    if (items.length < PAGE_SIZE || n * PAGE_SIZE >= total) return [...byId.values()];
  }
}

/** Every experiment for a picker, or every one in a project. */
export function listAllExperiments(projectId?: number | string): Promise<Experiment[]> {
  const scope = projectId ? `project_id=${projectId}&` : "";
  return readEveryPage(`/api/experiments?${scope}`, async (url) => {
    const data = await api.get<ExperimentListResponse>(url);
    return { items: data.experiments, total: data.total };
  });
}

/** Every sample in the Organization, each with its experiment's name. */
export function listAllSamples(): Promise<SampleOption[]> {
  return readEveryPage("/api/samples?", async (url) => {
    const data = await api.get<SampleOptionListResponse>(url);
    return { items: data.samples, total: data.total };
  });
}
