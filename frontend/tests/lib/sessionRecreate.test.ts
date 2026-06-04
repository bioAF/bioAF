import { prefillFromNotebookSession, prefillFromWorkNode } from "@/lib/sessionRecreate";
import type { NotebookSession, WorkNode } from "@/lib/types";

const baseNotebook: NotebookSession = {
  id: 99,
  session_type: "rstudio",
  user: null,
  experiment: null,
  project: null,
  resource_profile: "small",
  cpu_cores: 2,
  memory_gb: 8,
  requested_disk_gb: 100,
  status: "failed",
  failure_reason: "resource_exhausted",
  failure_message: "GCE out of resources",
  idle_since: null,
  proxy_url: null,
  started_at: null,
  stopped_at: null,
  created_at: "2026-06-03T12:00:00Z",
  git_branch_name: null,
  git_commit_hash: null,
  environment_version_id: 42,
  input_file_ids: [1, 2, 3],
};

const baseWorkNode: WorkNode = {
  id: 17,
  session_type: "ssh",
  user: null,
  project_id: 7,
  project: { id: 7, name: "Project Alpha" },
  environment_version_id: 88,
  machine_type: "e2-standard-8",
  input_file_ids: [10, 11],
  resource_profile: "custom",
  cpu_cores: 8,
  memory_gb: 32,
  requested_disk_gb: 250,
  status: "failed",
  failure_reason: "resource_exhausted",
  failure_message: "us-central1 all zones out of capacity",
  access_url: null,
  gce_instance_name: null,
  gce_zone: null,
  github_repo_ids: [55, 56],
  heartbeat_at: null,
  started_at: null,
  stopped_at: null,
  created_at: "2026-06-03T12:00:00Z",
};

describe("prefillFromNotebookSession", () => {
  it("copies type, profile, env, files, and the link scope onto the prefill payload", () => {
    const session: NotebookSession = {
      ...baseNotebook,
      experiment: { id: 5, name: "EXP-005" } as never,
    };
    const prefill = prefillFromNotebookSession(session);
    expect(prefill).toEqual({
      session_type: "rstudio",
      resource_profile: "small",
      environment_version_id: 42,
      scope_type: "experiment",
      experiment_id: 5,
      project_id: null,
      input_file_ids: [1, 2, 3],
      source_session_id: 99,
    });
  });

  it("uses project scope when project is set instead of experiment", () => {
    const session: NotebookSession = {
      ...baseNotebook,
      project: { id: 13, name: "Project Beta" },
    };
    const prefill = prefillFromNotebookSession(session);
    expect(prefill.scope_type).toBe("project");
    expect(prefill.project_id).toBe(13);
    expect(prefill.experiment_id).toBeNull();
  });

  it("defaults input_file_ids to [] when the backend response omits the field", () => {
    const session: NotebookSession = { ...baseNotebook, input_file_ids: null };
    expect(prefillFromNotebookSession(session).input_file_ids).toEqual([]);
  });
});

describe("prefillFromWorkNode", () => {
  it("copies machine type, env, project, files, and github repos", () => {
    const prefill = prefillFromWorkNode(baseWorkNode);
    expect(prefill).toEqual({
      project_id: 7,
      environment_version_id: 88,
      machine_type: "e2-standard-8",
      input_file_ids: [10, 11],
      github_repo_ids: [55, 56],
      source_session_id: 17,
    });
  });

  it("defaults nullable lists to [] so the form state starts in a usable shape", () => {
    const node: WorkNode = { ...baseWorkNode, input_file_ids: null, github_repo_ids: null };
    const prefill = prefillFromWorkNode(node);
    expect(prefill.input_file_ids).toEqual([]);
    expect(prefill.github_repo_ids).toEqual([]);
  });
});
