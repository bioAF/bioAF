import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import type { ProjectListResponse } from "@/lib/types";

/** Shown in place of a project picker when the caller may not list projects. */
export const PROJECTS_DENIED_MESSAGE =
  "Your role does not include viewing projects, so a project cannot be chosen here.";

/** Shown in place of an experiment picker when the caller may not list experiments. */
export const EXPERIMENTS_DENIED_MESSAGE =
  "Your role does not include viewing experiments, so an experiment cannot be chosen here.";

export const DEFAULT_PROJECTS_PATH = "/api/projects?page_size=100";

export type ProjectOptions =
  | { status: "loaded"; projects: ProjectListResponse["projects"] }
  | { status: "denied"; projects: [] }
  | { status: "failed"; projects: [] };

/** Duck-typed on `status`: an ApiError answer, without importing the class. */
export function isPermissionDenied(error: unknown): boolean {
  return (error as { status?: number })?.status === 403;
}

/**
 * The project list for a picker, kept apart from the form's other options.
 *
 * A form loads several lists together. Without this, a role that may not list
 * projects loses all of them, or sees an empty picker that reads as "there are no
 * projects". Denial and failure mean different things to the user: one is
 * permanent for their role, the other is worth retrying.
 */
export function loadProjectOptions(path = DEFAULT_PROJECTS_PATH): Promise<ProjectOptions> {
  return api.get<ProjectListResponse>(path).then(projectOptionsFrom, projectOptionsFromError);
}

export function projectOptionsFrom(data: ProjectListResponse): ProjectOptions {
  return { status: "loaded", projects: data.projects };
}

export function projectOptionsFromError(error: unknown): ProjectOptions {
  if (isPermissionDenied(error)) {
    return { status: "denied", projects: [] };
  }
  logError("loading the project list", error);
  return { status: "failed", projects: [] };
}
