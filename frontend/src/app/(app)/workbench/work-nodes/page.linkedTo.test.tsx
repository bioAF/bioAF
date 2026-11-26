/**
 * Plan 03.5E: a Work Node is labelled with the Experiment it was launched for, in the list and its details.
 * Project-only nodes show the Project; legacy unlinked nodes stay visibly unlinked.
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
    status: "running",
    stop_failure: null,
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
  [
    "an Experiment with a Project",
    { project_id: 3, project: { id: 3, name: "Atlas" }, experiment_id: 9, experiment: { id: 9, name: "Organoid timecourse" } },
    "Experiment: Organoid timecourse",
  ],
  [
    "a standalone Experiment",
    { project_id: null, project: null, experiment_id: 9, experiment: { id: 9, name: "Organoid timecourse" } },
    "Experiment: Organoid timecourse",
  ],
  [
    "a Project only",
    { project_id: 3, project: { id: 3, name: "Atlas" }, experiment_id: null, experiment: null },
    "Project: Atlas",
  ],
])("a node launched for %s shows that link in the list and its details", async (_case, link, label) => {
  serve([node(link)]);
  render(<WorkNodesPage />);

  const row = await screen.findByRole("row", { name: /Ada/ });
  expect(within(row).getByText(label)).toBeInTheDocument();

  fireEvent.click(within(row).getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Linked to")).toBeInTheDocument();
  expect(within(dialog).getByText(label)).toBeInTheDocument();
});

test("a legacy node with no recorded link stays unlinked", async () => {
  serve([node({ experiment_id: null, experiment: null })]);
  render(<WorkNodesPage />);

  const row = await screen.findByRole("row", { name: /Ada/ });
  expect(within(row).queryByText(/^(Experiment|Project):/)).not.toBeInTheDocument();

  fireEvent.click(within(row).getByRole("button", { name: "Details" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByText("Linked to")).not.toBeInTheDocument();
});
