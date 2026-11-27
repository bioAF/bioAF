/** Plan 03B: a work node's details show the SSH login rule it was launched with and what it applied. */
import { render, screen } from "@/testing/renderWithProviders";
import { WorkNodeDetailModal } from "./WorkNodeDetailModal";
import type { WorkNode } from "@/lib/types";

function node(overrides: Partial<WorkNode> = {}): WorkNode {
  return {
    id: 7, session_type: "ssh", user: null, project_id: null, project: null, environment_version_id: 1,
    machine_type: "n2-standard-4", input_file_ids: [], resource_profile: "custom", cpu_cores: 4, memory_gb: 16,
    requested_disk_gb: 100, status: "running", failure_reason: null, failure_message: null,
    access_url: "ssh://sci@34.1.2.3:22", gce_instance_name: "bioaf-worknode-7", gce_zone: "us-central1-b",
    github_repo_ids: [], heartbeat_at: null, started_at: null, stopped_at: null, created_at: "2026-11-24T10:00:00Z",
    ...overrides,
  } as WorkNode;
}

test.each([
  [3, "applied", "Revision 3, in force on this work node"],
  [3, "failed_closed", "Revision 3 could not be applied as set; this work node accepts key login only"],
  [0, "applied", "No range: password or key from anywhere"],
  [2, null, "Revision 2 (not reported by the work node yet)"],
])("revision %s reported %s", (revision, status, text) => {
  render(
    <WorkNodeDetailModal
      node={node({ ssh_policy_revision: revision, ssh_policy_status: status } as Partial<WorkNode>)}
      repos={[]}
      canStop={false}
      onStop={() => {}}
      onClose={() => {}}
    />,
  );
  expect(screen.getByText("SSH login rule")).toBeInTheDocument();
  expect(screen.getByText(text)).toBeInTheDocument();
});

test("a node launched before login rules existed shows no rule row", () => {
  render(<WorkNodeDetailModal node={node()} repos={[]} canStop={false} onStop={() => {}} onClose={() => {}} />);
  expect(screen.queryByText("SSH login rule")).not.toBeInTheDocument();
});

// Plan 03.5D: how reporting stands, next to Last Heartbeat. Proposed wording, pending owner sign-off.
test.each([
  ["current", "Reporting normally"],
  ["stale", "Not reporting. The work node keeps running; missing reports do not stop it."],
  ["unreachable", "Not reporting. The work node keeps running; missing reports do not stop it."],
  ["awaiting", "Waiting for the first report"],
  ["unavailable", "This work node was started before reporting was available"],
])("reporting %s", (status, text) => {
  render(
    <WorkNodeDetailModal
      node={node({ report_status: status } as Partial<WorkNode>)}
      repos={[]}
      canStop={false}
      onStop={() => {}}
      onClose={() => {}}
    />,
  );
  expect(screen.getByText("Reporting")).toBeInTheDocument();
  expect(screen.getByText(text)).toBeInTheDocument();
});

test("a stopped node shows no reporting row", () => {
  render(
    <WorkNodeDetailModal
      node={node({ status: "stopped", report_status: null } as Partial<WorkNode>)}
      repos={[]}
      canStop={false}
      onStop={() => {}}
      onClose={() => {}}
    />,
  );
  expect(screen.queryByText("Reporting")).not.toBeInTheDocument();
});
