/**
 * The session API emits session_type and resource_profile as open strings. The launcher only
 * offers the known types and profiles, so recreating narrows them explicitly instead of
 * asserting: a value the launcher cannot offer yields no prefill rather than a modal for a
 * type or profile that does not exist.
 */
import { prefillFromNotebookSession } from "@/lib/sessionRecreate";
import type { NotebookSession } from "@/lib/types";

// Shaped like backend/app/api/notebook_sessions.py:_session_response.
const session: NotebookSession = {
  id: 5,
  session_type: "jupyter",
  user: { id: 1, name: null, email: "a@lab.test" },
  experiment: { id: 3, name: "Liver" },
  project: null,
  resource_profile: "large",
  cpu_cores: 8,
  memory_gb: 32,
  requested_disk_gb: null,
  status: "stopped",
  failure_reason: null,
  failure_message: null,
  idle_since: null,
  proxy_url: null,
  started_at: null,
  stopped_at: null,
  created_at: "2026-11-12T10:00:00Z",
  git_branch_name: null,
  git_commit_hash: null,
  environment_version_id: null,
  input_file_ids: null,
  provider_metadata: null,
};

test("known values pass through unchanged", () => {
  const prefill = prefillFromNotebookSession(session);
  expect(prefill).toMatchObject({ session_type: "jupyter", resource_profile: "large", experiment_id: 3 });
});

test.each([
  ["an unknown session type", { session_type: "vscode" }],
  ["an unknown resource profile", { resource_profile: "custom" }],
  ["an inherited property name as a profile", { resource_profile: "toString" }],
])("%s yields no prefill", (_label, change) => {
  expect(prefillFromNotebookSession({ ...session, ...change })).toBeNull();
});
