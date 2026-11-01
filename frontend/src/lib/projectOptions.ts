import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import type { ProjectListResponse } from "@/lib/types";

/** Shown in place of a project picker when the caller may not list projects. */
export const PROJECTS_DENIED_MESSAGE =
  "Your role does not include viewing projects, so a project cannot be chosen here.";

export type ProjectOptions =
  | { status: "loaded"; projects: ProjectListResponse["projects"] }
  | { status: "denied"; projects: [] }
  | { status: "failed"; projects: [] };

/**
 * The project list for a picker, kept apart from the form's other options.
 *
 * A form loads several lists together. Without this, a role that may not list
 * projects loses all of them, or sees an empty picker that reads as "there are no
 * projects". Denial and failure mean different things to the user: one is
 * permanent for their role, the other is worth retrying.
 */
export async function loadProjectOptions(path = "/api/projects?page_size=100"): Promise<ProjectOptions> {
  try {
    const data = await api.get<ProjectListResponse>(path);
    return { status: "loaded", projects: data.projects };
  } catch (error) {
    if ((error as { status?: number })?.status === 403) {
      return { status: "denied", projects: [] };
    }
    logError("loading the project list", error);
    return { status: "failed", projects: [] };
  }
}
