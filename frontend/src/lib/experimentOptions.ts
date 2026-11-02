import { api } from "@/lib/api";
import type { Experiment, ExperimentListResponse } from "@/lib/types";

const PAGE_SIZE = 200;

/**
 * Every experiment for a picker, or every one in a project. A picker has no
 * pagination of its own, so a single page would hide the oldest experiments
 * with nothing to tell the user they exist.
 */
export async function listAllExperiments(projectId?: number | string): Promise<Experiment[]> {
  const scope = projectId ? `project_id=${projectId}&` : "";
  const byId = new Map<number, Experiment>();
  for (let page = 1; ; page++) {
    const data = await api.get<ExperimentListResponse>(
      `/api/experiments?${scope}page=${page}&page_size=${PAGE_SIZE}`,
    );
    for (const experiment of data.experiments) byId.set(experiment.id, experiment);
    if (data.experiments.length < PAGE_SIZE || page * PAGE_SIZE >= data.total) {
      return [...byId.values()];
    }
  }
}
