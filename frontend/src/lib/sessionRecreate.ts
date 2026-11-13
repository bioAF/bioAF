// Pure helpers that produce a "prefill payload" from a finished session.
// The Recreate button in the Notebooks and Work Nodes tables hands one of
// these objects to the launch modal, which copies each field onto its form
// state. The user reviews the prefilled values (input files may have been
// deleted, the environment may have a newer version available, etc.) and
// clicks Launch to submit a fresh session via the existing endpoint.

import { RESOURCE_PROFILES } from "@/lib/types";
import type { NotebookSession, ResourceProfile, SessionType, WorkNode } from "@/lib/types";

export interface NotebookRecreatePrefill {
  session_type: SessionType;
  resource_profile: ResourceProfile;
  environment_version_id: number | null;
  scope_type: "experiment" | "project";
  experiment_id: number | null;
  project_id: number | null;
  input_file_ids: number[];
  source_session_id: number;
}

export interface WorkNodeRecreatePrefill {
  project_id: number | null;
  environment_version_id: number | null;
  machine_type: string | null;
  input_file_ids: number[];
  github_repo_ids: number[];
  source_session_id: number;
}

const SESSION_TYPES: readonly string[] = ["jupyter", "rstudio"] satisfies SessionType[];

// The session API emits type and profile as open strings; the launcher offers only these.
function knownSessionType(value: string): SessionType | null {
  return SESSION_TYPES.includes(value) ? (value as SessionType) : null;
}

function knownResourceProfile(value: string): ResourceProfile | null {
  return Object.prototype.hasOwnProperty.call(RESOURCE_PROFILES, value) ? (value as ResourceProfile) : null;
}

/** The launch form's prefill, or null when the session's type or profile is not one the launcher offers. */
export function prefillFromNotebookSession(s: NotebookSession): NotebookRecreatePrefill | null {
  const sessionType = knownSessionType(s.session_type);
  const resourceProfile = knownResourceProfile(s.resource_profile);
  if (!sessionType || !resourceProfile) return null;
  // A session is tied to either an experiment OR a project, never both.
  // Pick whichever one is set; default to experiment scope so the form
  // toggle has a sensible initial state for legacy rows with neither.
  const hasProject = !!s.project;
  return {
    session_type: sessionType,
    resource_profile: resourceProfile,
    environment_version_id: s.environment_version_id,
    scope_type: hasProject ? "project" : "experiment",
    experiment_id: s.experiment?.id ?? null,
    project_id: s.project?.id ?? null,
    input_file_ids: s.input_file_ids ?? [],
    source_session_id: s.id,
  };
}

export function prefillFromWorkNode(n: WorkNode): WorkNodeRecreatePrefill {
  return {
    project_id: n.project_id,
    environment_version_id: n.environment_version_id,
    machine_type: n.machine_type,
    input_file_ids: n.input_file_ids ?? [],
    github_repo_ids: n.github_repo_ids ?? [],
    source_session_id: n.id,
  };
}
