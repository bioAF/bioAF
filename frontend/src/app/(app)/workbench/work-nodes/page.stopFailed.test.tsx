/**
 * A Stop that could not save or confirm deletion keeps the node and says so plainly.
 *
 * Plan 03A copy: "Stop failed", a plain reason per outcome, and "Retry stop". No discard control.
 */
import { render, screen, waitFor, fireEvent, within } from "@/testing/renderWithProviders";
import WorkNodesPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

function node(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    session_type: "ssh",
    user: { id: 1, name: "Ada", email: "a@b.c" },
    project_id: null,
    project: null,
    environment_version_id: 1,
    machine_type: "n2-standard-4",
    input_file_ids: [],
    resource_profile: "custom",
    cpu_cores: 4,
    memory_gb: 16,
    requested_disk_gb: 100,
    status: "stop_failed",
    stop_failure: "save_failed",
    failure_reason: null,
    failure_message: null,
    access_url: "ssh://ada@34.1.2.3:22",
    gce_instance_name: "bioaf-worknode-7",
    gce_zone: "us-central1-b",
    github_repo_ids: [],
    heartbeat_at: null,
    started_at: "2026-11-24T10:00:00Z",
    stopped_at: null,
    created_at: "2026-11-24T10:00:00Z",
    ...overrides,
  };
}

function serve(nodes: unknown[]) {
  mockGet.mockImplementation((url: string) => {
    if (String(url).includes("/work-nodes/sessions")) return Promise.resolve({ sessions: nodes, total: nodes.length });
    return Promise.resolve({ repos: [], sessions: [], profiles: [] });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

test.each([
  ["save_failed", "Your files could not be saved. The work node has been kept, so you can keep working and try again."],
  ["delete_unconfirmed", "Your files were saved, but bioAF could not finish stopping this work node."],
  ["state_unknown", "bioAF could not check this work node. Try again shortly."],
])("a kept node (%s) shows Stop failed, its plain reason and Retry stop", async (reason, message) => {
  serve([node({ stop_failure: reason })]);
  render(<WorkNodesPage />);

  const row = await screen.findByRole("row", { name: /Ada/ });
  expect(within(row).getByText("Stop failed")).toBeInTheDocument();
  expect(within(row).getByRole("button", { name: "Retry stop" })).toBeInTheDocument();

  fireEvent.click(within(row).getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(message)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Retry stop" })).toBeInTheDocument();
  // No control discards the node or its files.
  expect(within(dialog).queryByRole("button", { name: /discard|force|delete/i })).not.toBeInTheDocument();
});

test("a node kept because its files were not saved stays connectable", async () => {
  serve([node()]);
  render(<WorkNodesPage />);
  const row = await screen.findByRole("row", { name: /Ada/ });
  expect(within(row).getByText("ssh ada@34.1.2.3")).toBeInTheDocument();
});

test("Retry stop calls the same Stop and reports a second failure without closing the details", async () => {
  serve([node()]);
  mockPost.mockResolvedValue(node());
  render(<WorkNodesPage />);
  const row = await screen.findByRole("row", { name: /Ada/ });
  fireEvent.click(within(row).getByRole("button", { name: "Retry stop" }));
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/work-nodes/sessions/7/stop"));
});
