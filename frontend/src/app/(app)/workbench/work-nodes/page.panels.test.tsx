/**
 * The work-node page's panels: the launch dialog (its draft lives only as long as the dialog),
 * the node detail dialog, and the GitHub repository list. Option lists belong to the opening that
 * read them; the node list, repositories and launch outcomes belong to the page.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/workbench/work-nodes",
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) {
      super(message);
    }
  },
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "t",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "a@x" }),
}));
let mockGranted = new Set(["work_nodes:view", "work_nodes:launch", "work_nodes:stop"]);
const mockPermissions = {
  canAccess: (resource: string, action: string) => mockGranted.has(`${resource}:${action}`),
  roleName: "admin",
  loading: false,
};
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions }));
const mockConfirm = jest.fn();
jest.mock("@/hooks/useConfirm", () => ({
  ...jest.requireActual("@/hooks/useConfirm"),
  useConfirm: () => mockConfirm,
}));

import { api, ApiError } from "@/lib/api";
import WorkNodesPage from "./page";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockDelete = api.delete as jest.Mock;

const machineTypes = [{ name: "e2-standard-8", category: "standard", cpu: 8, memory_gb: 32, gpu: null, description: "" }];

const runningNode = {
  id: 5,
  user: { id: 1, name: "U", email: "u@x" },
  project: null,
  project_id: null,
  machine_type: "e2-standard-8",
  cpu_cores: 8,
  memory_gb: 32,
  status: "running",
  access_url: "ssh://10.0.0.1:22",
  environment_version_id: 21,
  input_file_ids: [],
  github_repo_ids: [3],
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: null,
};

let held: Record<string, Array<(value: unknown) => void>>;
let repos: Array<{ id: number; display_name: string; git_ssh_url: string }>;

function serve({ hold = [] as string[], nodes = [] as unknown[], experiments = ["Exp 42"] } = {}) {
  mockGet.mockImplementation((url: string) => {
    const holdKey = hold.find((h) => url.includes(h));
    if (holdKey && !(held[holdKey]?.length >= 1 && hold.includes(`${holdKey}#once`))) {
      return new Promise((resolve) => (held[holdKey] ??= []).push(resolve));
    }
    if (url.includes("/api/v1/work-nodes/sessions")) return Promise.resolve({ sessions: nodes });
    if (url.includes("/api/v1/work-nodes/machine-types")) return Promise.resolve(machineTypes);
    if (url.includes("/api/v1/github-repos")) return Promise.resolve({ repos });
    if (url.includes("/api/v1/environments/2")) {
      return Promise.resolve({ id: 2, versions: [{ id: 21, version_number: 1, build_number: 1, status: "ready", image_uri: "img" }] });
    }
    if (url.includes("/api/v1/environments")) {
      return Promise.resolve({
        environments: [{ id: 2, name: "Env 2", latest_version: { id: 21, version_number: 1, status: "ready", image_uri: "img" } }],
      });
    }
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [{ id: 7, name: "Project Alpha" }] });
    if (url.includes("/api/experiments")) {
      return Promise.resolve({ experiments: experiments.map((name, i) => ({ id: 42 + i, name, project: null })) });
    }
    return Promise.resolve({});
  });
}

beforeEach(() => {
  held = {};
  repos = [{ id: 3, display_name: "lab-tools", git_ssh_url: "git@github.com:lab/tools.git" }];
  mockGranted = new Set(["work_nodes:view", "work_nodes:launch", "work_nodes:stop"]);
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
  mockConfirm.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

/** The first available machine profile, once the machine types have loaded. */
async function availableProfile(dialog: HTMLElement) {
  let found: HTMLElement | undefined;
  await waitFor(() => {
    found = within(within(dialog).getByRole("group", { name: "Machine Profile" }))
      .getAllByRole("button")
      .find((b) => !b.hasAttribute("disabled"));
    expect(found).toBeDefined();
  });
  return found!;
}

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "New Work Node" }));
  return screen.findByRole("dialog", { name: "Launch Work Node" });
}

test("closing the launch dialog abandons its draft", async () => {
  serve();
  render(<WorkNodesPage />);
  let dialog = await openDialog();
  const profile = await availableProfile(dialog);
  fireEvent.click(profile);
  expect(profile).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  dialog = await openDialog();
  await availableProfile(dialog);
  expect(within(within(dialog).getByRole("group", { name: "Machine Profile" })).queryByRole("button", { pressed: true })).toBeNull();
});

test("option lists read for an earlier opening never fill a later one", async () => {
  let call = 0;
  serve();
  const base = mockGet.getMockImplementation()!;
  mockGet.mockImplementation((url: string, controls?: unknown) => {
    if (url.startsWith("/api/experiments?")) {
      call += 1;
      if (call === 1) return new Promise((resolve) => (held.first ??= []).push(resolve));
      return Promise.resolve({ experiments: [{ id: 50, name: "New Exp", project: null }] });
    }
    return base(url, controls);
  });
  render(<WorkNodesPage />);
  let dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  dialog = await openDialog();
  await within(dialog).findByRole("option", { name: "New Exp" });

  await act(async () => held.first[0]({ experiments: [{ id: 49, name: "Old Exp", project: null }] }));
  expect(within(dialog).queryByRole("option", { name: "Old Exp" })).not.toBeInTheDocument();
  expect(within(dialog).getByRole("option", { name: "New Exp" })).toBeInTheDocument();
});

test("the detail dialog stops a running node after confirmation and closes", async () => {
  mockConfirm.mockResolvedValue(true);
  mockPost.mockResolvedValue({});
  serve({ nodes: [runningNode] });
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Details" }));
  const detail = await screen.findByRole("dialog", { name: "Work Node Details" });
  expect(within(detail).getByText("~/repos/lab-tools")).toBeInTheDocument();

  fireEvent.click(within(detail).getByRole("button", { name: "Stop Work Node" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/work-nodes/sessions/5/stop"));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Work Node Details" })).not.toBeInTheDocument());
});

test("a declined stop sends nothing", async () => {
  mockConfirm.mockResolvedValue(false);
  serve({ nodes: [runningNode] });
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  await waitFor(() => expect(mockConfirm).toHaveBeenCalled());
  expect(mockPost).not.toHaveBeenCalled();
});

test("removing a repository refreshes the list", async () => {
  mockDelete.mockImplementation(async () => {
    repos = [];
  });
  serve();
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "GitHub Repos (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/api/v1/github-repos/3"));
  expect(await screen.findByRole("button", { name: "GitHub Repos (0)" })).toBeInTheDocument();
});

test("without launch and stop permission the page is read-only", async () => {
  mockGranted = new Set(["work_nodes:view"]);
  serve({ nodes: [runningNode] });
  render(<WorkNodesPage />);
  await screen.findByRole("button", { name: "Details" });
  expect(screen.queryByRole("button", { name: "New Work Node" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Recreate" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "GitHub Repos (1)" }));
  expect(screen.queryByRole("form", { name: "Add GitHub repo" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
});

test("a failed launch can be tried again from a new opening", async () => {
  mockPost.mockRejectedValueOnce(new ApiError(409, "Zone exhausted")).mockResolvedValueOnce({});
  serve();
  render(<WorkNodesPage />);

  for (let i = 0; i < 2; i++) {
    const dialog = await openDialog();
    fireEvent.click(await availableProfile(dialog));
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Workbench template" }), { target: { value: "2" } });
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Version" })).toBeInTheDocument());
    fireEvent.click(within(dialog).getByRole("button", { name: "Project" }));
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Select project" }), { target: { value: "7" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Next: Review" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Launch Work Node" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Launch without inputs?" })).getByRole("button", { name: "Launch anyway" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(i + 1));
  }
  expect(await screen.findByText("Zone exhausted")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryAllByRole("status").some((s) => /provisioning/i.test(s.textContent ?? ""))).toBe(false));
});
