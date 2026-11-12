/**
 * The work-node launch dialog's selections belong to one opening and to the selection that asked:
 * a late answer for an earlier experiment or template never replaces the current one, a recreated
 * node's prefill survives option reads that arrive later, and a launch is sent once however fast
 * it is pressed. Closing the dialog abandons the draft, not a launch already sent: its progress
 * and outcome stay on the page, and a new launch can start while it provisions.
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
const mockPermissions = { canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() };
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions }));

import { api, ApiError } from "@/lib/api";
import WorkNodesPage from "./page";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const machineTypes = [{ name: "e2-standard-8", category: "standard", cpu: 8, memory_gb: 32, gpu: null, description: "" }];

function file(id: number, experimentId: number, filename: string) {
  return {
    id,
    filename,
    gcs_uri: `gs://b/${filename}`,
    storage_uri: `gs://b/${filename}`,
    size_bytes: 10,
    file_type: "other",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: experimentId,
    sample_ids: [],
    source_type: "upload",
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function env(id: number, versionId: number) {
  return {
    id,
    name: `Env ${id}`,
    version_count: 1,
    latest_version: { id: versionId, version_number: 1, status: "ready", image_uri: `img:${id}`, build_number: 1 },
    visibility: "team",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function envDetail(id: number, versionId: number) {
  return {
    id,
    name: `Env ${id}`,
    versions: [
      { id: versionId, version_number: 1, build_number: versionId, status: "ready", definition_format: "dockerfile", image_uri: `img:${id}` },
    ],
  };
}

const stoppedNode = {
  id: 5,
  user: { id: 1, name: "U", email: "u@x" },
  project: { id: 7, name: "Project Alpha" },
  project_id: 7,
  machine_type: "e2-standard-8",
  cpu_cores: 8,
  memory_gb: 32,
  status: "stopped",
  environment_version_id: 21,
  input_file_ids: [3],
  github_repo_ids: [],
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: "2026-01-01T01:00:00Z",
};

let held: Record<string, Array<(value: unknown) => void>>;

function serve({ hold = [] as string[], nodes = [] as unknown[] } = {}) {
  mockGet.mockImplementation((url: string) => {
    const holdKey = hold.find((h) => url.includes(h));
    if (holdKey) return new Promise((resolve) => (held[holdKey] ??= []).push(resolve));
    if (url.includes("/api/v1/work-nodes/sessions")) return Promise.resolve({ sessions: nodes });
    if (url.includes("/api/v1/work-nodes/machine-types")) return Promise.resolve(machineTypes);
    if (url.includes("/api/v1/github-repos")) return Promise.resolve({ repos: [] });
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(envDetail(1, 11));
    if (url.includes("/api/v1/environments/2")) return Promise.resolve(envDetail(2, 21));
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [env(1, 11), env(2, 21)] });
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [{ id: 7, name: "Project Alpha" }] });
    if (url.includes("/api/experiments/42/files")) return Promise.resolve({ files: [file(1, 42, "a42.h5ad")] });
    if (url.includes("/api/experiments/43/files")) return Promise.resolve({ files: [file(2, 43, "b43.h5ad")] });
    if (url.includes("/samples")) return Promise.resolve({ samples: [] });
    if (url.includes("/api/experiments")) {
      return Promise.resolve({
        experiments: [
          { id: 42, name: "Exp 42", project: null },
          { id: 43, name: "Exp 43", project: null },
        ],
      });
    }
    return Promise.resolve({});
  });
}

beforeEach(() => {
  held = {};
  mockGet.mockReset();
  mockPost.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "New Work Node" }));
  const dialog = await screen.findByRole("dialog", { name: "Launch Work Node" });
  await waitFor(() =>
    expect(within(dialog).getByRole("combobox", { name: "Workbench template" })).toBeInTheDocument(),
  );
  return dialog;
}

/** With files, the experiment's default file is selected; without, the node links to a project. */
async function fillAndReview(dialog: HTMLElement, { envId = "2", withFiles = true } = {}) {
  fireEvent.click(within(within(dialog).getByRole("group", { name: "Machine Profile" })).getAllByRole("button").find((b) => !b.hasAttribute("disabled"))!);
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Workbench template" }), { target: { value: envId } });
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Version" })).toBeInTheDocument());
  if (withFiles) {
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Select experiment" }), { target: { value: "43" } });
    await waitFor(() => expect(within(dialog).getByText(/1 file selected/)).toBeInTheDocument());
  } else {
    fireEvent.click(within(dialog).getByRole("button", { name: "Project" }));
    fireEvent.change(within(dialog).getByRole("combobox", { name: "Select project" }), { target: { value: "7" } });
  }
  fireEvent.click(within(dialog).getByRole("button", { name: "Next: Review" }));
}

test("a late file list for an earlier experiment never replaces the current one", async () => {
  serve({ hold: ["/api/experiments/42/files"] });
  render(<WorkNodesPage />);
  const dialog = await openDialog();
  const experiment = within(dialog).getByRole("combobox", { name: "Select experiment" });
  fireEvent.change(experiment, { target: { value: "42" } });
  fireEvent.change(experiment, { target: { value: "43" } });
  expect(await within(dialog).findByText("b43.h5ad")).toBeInTheDocument();

  await act(async () => held["/api/experiments/42/files"][0]({ files: [file(1, 42, "a42.h5ad")] }));
  expect(within(dialog).queryByText("a42.h5ad")).not.toBeInTheDocument();
});

test("a late template detail for an earlier choice never sets the version", async () => {
  mockPost.mockResolvedValue({});
  serve({ hold: ["/api/v1/environments/1"] });
  render(<WorkNodesPage />);
  const dialog = await openDialog();
  const template = within(dialog).getByRole("combobox", { name: "Workbench template" });
  fireEvent.change(template, { target: { value: "1" } });
  await fillAndReview(dialog);

  await act(async () => held["/api/v1/environments/1"][0](envDetail(1, 11)));
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Work Node" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(mockPost.mock.calls[0][1]).toMatchObject({ environment_version_id: 21, experiment_id: 43 });
});

test("closing and reopening starts clean, and the old opening's files never arrive", async () => {
  serve({ hold: ["/api/experiments/42/files"] });
  render(<WorkNodesPage />);
  let dialog = await openDialog();
  fireEvent.change(within(dialog).getByRole("combobox", { name: "Select experiment" }), { target: { value: "42" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  dialog = await openDialog();
  expect(within(dialog).getByRole("combobox", { name: "Select experiment" })).toHaveValue("");
  await act(async () => held["/api/experiments/42/files"][0]({ files: [file(1, 42, "a42.h5ad")] }));
  expect(within(dialog).queryByText("a42.h5ad")).not.toBeInTheDocument();
});

test("a recreated node keeps its prefill when the option lists arrive later", async () => {
  mockPost.mockResolvedValue({});
  serve({ nodes: [stoppedNode], hold: ["/api/v1/work-nodes/machine-types"] });
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Recreate" }));
  const dialog = await screen.findByRole("dialog", { name: "Launch Work Node" });

  await act(async () => held["/api/v1/work-nodes/machine-types"][0](machineTypes));
  fireEvent.click(within(dialog).getByRole("button", { name: "Next: Review" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Work Node" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(mockPost.mock.calls[0][1]).toMatchObject({
    project_id: 7,
    environment_version_id: 21,
    machine_type: "e2-standard-8",
    input_file_ids: [3],
  });
});

test("the no-inputs confirmation launches once, however fast it is pressed", async () => {
  mockPost.mockImplementation(() => new Promise(() => {}));
  serve();
  render(<WorkNodesPage />);
  const dialog = await openDialog();
  await fillAndReview(dialog, { withFiles: false });
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Work Node" }));
  const confirm = await screen.findByRole("dialog", { name: "Launch without inputs?" });
  const anyway = within(confirm).getByRole("button", { name: "Launch anyway" });
  act(() => {
    anyway.click();
    anyway.click();
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
});

test("a launch still provisioning does not block the next one, and each outcome is kept", async () => {
  const replies: Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }> = [];
  mockPost.mockImplementation(() => new Promise((resolve, reject) => replies.push({ resolve, reject })));
  serve();
  render(<WorkNodesPage />);

  for (let i = 0; i < 2; i++) {
    const dialog = await openDialog();
    await fillAndReview(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Launch Work Node" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(i + 1));
  }

  await act(async () => replies[0].reject(new ApiError(409, "Zone exhausted")));
  await act(async () => replies[1].resolve({}));
  expect(screen.getByText("Zone exhausted")).toBeInTheDocument();
});
