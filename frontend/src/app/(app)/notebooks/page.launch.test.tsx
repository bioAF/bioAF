/**
 * The notebook launch dialog's selections (scope, workbench template and version, input files)
 * belong to one opening of the dialog and to the selection that asked. A late answer for an
 * earlier experiment or template never replaces the current one, a recreated session's prefill
 * survives option reads that arrive later, and a launch is sent once however fast it is pressed,
 * including through the "launch without inputs" confirmation.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({
    components: [
      { key: "jupyterhub", category: "analysis", enabled: true },
      { key: "rstudio", category: "analysis", enabled: true },
    ],
    loading: false,
    refetch: jest.fn(),
  }),
}));

import NotebooksPage from "./page";

function file(id: number, experimentId: number, filename: string) {
  return {
    id,
    filename,
    gcs_uri: `gs://b/${filename}`,
    storage_uri: `gs://b/${filename}`,
    size_bytes: 10,
    md5_checksum: null,
    file_type: "other",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: experimentId,
    sample_ids: [],
    source_type: "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function env(id: number, name: string, versionId: number) {
  return {
    id,
    name,
    description: null,
    version_count: 1,
    latest_version: { id: versionId, version_number: 1, status: "ready", image_uri: `img:${id}` },
    visibility: "team",
    created_at: "2026-01-01T00:00:00Z",
  };
}

function envDetail(id: number, versionId: number) {
  return {
    id,
    name: `Env ${id}`,
    versions: [
      {
        id: versionId,
        version_number: versionId,
        build_number: 1,
        status: "ready",
        definition_format: "dockerfile",
        image_uri: `img:${id}:${versionId}`,
        created_at: "2026-01-01T00:00:00Z",
      },
    ],
  };
}

const finishedSession = {
  id: 9,
  session_type: "rstudio",
  user: { id: 1, name: "U", email: "u@x" },
  experiment: { id: 42, name: "Exp 42" },
  project: null,
  resource_profile: "medium",
  cpu_cores: 2,
  memory_gb: 8,
  status: "stopped",
  environment_version_id: 77,
  input_file_ids: [],
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: "2026-01-01T01:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
};

type Held = Record<string, Array<(value: unknown) => void>>;
let held: Held;

function serve({ hold = [], sessions = [] as unknown[] }: { hold?: string[]; sessions?: unknown[] } = {}) {
  mockGet.mockImplementation((url: string) => {
    const holdKey = hold.find((h) => url.includes(h));
    if (holdKey) return new Promise((resolve) => (held[holdKey] ??= []).push(resolve));
    if (url.includes("/api/v1/notebooks/sessions")) return Promise.resolve({ sessions, total: sessions.length });
    if (url.includes("/api/experiments/42/files")) return Promise.resolve({ files: [file(1, 42, "a42.h5ad")] });
    if (url.includes("/api/experiments/43/files")) return Promise.resolve({ files: [file(2, 43, "b43.h5ad")] });
    if (url.includes("/api/experiments/44/files")) return Promise.resolve({ files: [file(3, 44, "notes.txt")] });
    if (url.includes("/samples")) return Promise.resolve({ samples: [] });
    if (url.includes("/api/experiments")) {
      return Promise.resolve({
        experiments: [
          { id: 42, name: "Exp 42" },
          { id: 43, name: "Exp 43" },
          { id: 44, name: "Exp 44" },
        ],
      });
    }
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(envDetail(1, 11));
    if (url.includes("/api/v1/environments/2")) return Promise.resolve(envDetail(2, 21));
    if (url.includes("/api/v1/environments")) {
      return Promise.resolve({ environments: [env(1, "Env one", 11), env(2, "Env two", 21)] });
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
  fireEvent.click(await screen.findByRole("button", { name: "Launch Session" }));
  return screen.findByRole("dialog", { name: "Launch Notebook Session" });
}

function lastPost() {
  return mockPost.mock.calls.filter(([url]) => url === "/api/v1/notebooks/sessions").at(-1)?.[1];
}

test("a late file list for an earlier experiment never replaces the current one", async () => {
  serve({ hold: ["/api/experiments/42/files"] });
  render(<NotebooksPage />);
  const dialog = await openDialog();
  const experiment = within(dialog).getByRole("combobox", { name: "Experiment" });
  await waitFor(() => expect(within(experiment).getByRole("option", { name: "Exp 43" })).toBeInTheDocument());

  fireEvent.change(experiment, { target: { value: "42" } });
  fireEvent.change(experiment, { target: { value: "43" } });
  expect(await within(dialog).findByText("b43.h5ad")).toBeInTheDocument();

  await act(async () => held["/api/experiments/42/files"][0]({ files: [file(1, 42, "a42.h5ad")] }));
  expect(within(dialog).queryByText("a42.h5ad")).not.toBeInTheDocument();
  expect(within(dialog).getByText("b43.h5ad")).toBeInTheDocument();
});

test("a late template detail for an earlier choice never sets the version", async () => {
  mockPost.mockResolvedValue({});
  serve({ hold: ["/api/v1/environments/1"] });
  render(<NotebooksPage />);
  const dialog = await openDialog();
  const template = within(dialog).getByRole("combobox", { name: "Workbench Template" });
  await waitFor(() => expect(within(template).getByRole("option", { name: /Env two/ })).toBeInTheDocument());

  fireEvent.change(template, { target: { value: "1" } });
  fireEvent.change(template, { target: { value: "2" } });
  await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Template version" })).toHaveValue("21"));

  await act(async () => held["/api/v1/environments/1"][0](envDetail(1, 11)));
  expect(within(dialog).getByRole("combobox", { name: "Template version" })).toHaveValue("21");

  fireEvent.change(within(dialog).getByRole("combobox", { name: "Experiment" }), { target: { value: "43" } });
  await within(dialog).findByText("b43.h5ad");
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Jupyter" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(lastPost()).toMatchObject({ environment_version_id: 21, image_uri: "img:2:21" });
});

test("closing and reopening starts clean, and the old opening's files never arrive", async () => {
  serve({ hold: ["/api/experiments/42/files"] });
  render(<NotebooksPage />);
  let dialog = await openDialog();
  fireEvent.change(await within(dialog).findByRole("combobox", { name: "Experiment" }), { target: { value: "42" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  dialog = await openDialog();
  await act(async () => held["/api/experiments/42/files"][0]({ files: [file(1, 42, "a42.h5ad")] }));
  expect(within(dialog).queryByText("a42.h5ad")).not.toBeInTheDocument();
});

test("a recreated session keeps its template version when option reads arrive later", async () => {
  mockPost.mockResolvedValue({});
  serve({ sessions: [finishedSession] });
  render(<NotebooksPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Recreate" }));

  const confirmDialog = await screen.findByRole("dialog", { name: "Launch without inputs?" });
  fireEvent.click(within(confirmDialog).getByRole("button", { name: /Launch anyway/i }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(lastPost()).toMatchObject({
    session_type: "rstudio",
    resource_profile: "medium",
    experiment_id: 42,
    environment_version_id: 77,
  });
});

test("a launch is sent once, however fast it is pressed", async () => {
  let finish: (v: unknown) => void = () => {};
  mockPost.mockImplementation(() => new Promise((r) => (finish = r)));
  serve();
  render(<NotebooksPage />);
  const dialog = await openDialog();
  fireEvent.change(await within(dialog).findByRole("combobox", { name: "Experiment" }), { target: { value: "42" } });
  await within(dialog).findByText("a42.h5ad");
  await waitFor(() => expect(within(dialog).getByText(/1 file selected/)).toBeInTheDocument());

  const jupyter = within(dialog).getByRole("button", { name: "Launch Jupyter" });
  const rstudio = within(dialog).getByRole("button", { name: "Launch RStudio" });
  act(() => {
    jupyter.click();
    jupyter.click();
    rstudio.click();
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
  await act(async () => finish({}));
});

test("the no-inputs confirmation launches once, however fast it is pressed", async () => {
  mockPost.mockImplementation(() => new Promise(() => {}));
  serve();
  render(<NotebooksPage />);
  const dialog = await openDialog();
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Jupyter" }));
  const confirmDialog = await screen.findByRole("dialog", { name: "Launch without inputs?" });
  const anyway = within(confirmDialog).getByRole("button", { name: /Launch anyway/i });
  act(() => {
    anyway.click();
    anyway.click();
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
});

test("a launch failure that arrives after the dialog closed does not show in the next opening", async () => {
  let fail: (e: Error) => void = () => {};
  mockPost.mockImplementation(() => new Promise((_, reject) => (fail = reject)));
  serve();
  render(<NotebooksPage />);
  let dialog = await openDialog();
  fireEvent.change(await within(dialog).findByRole("combobox", { name: "Experiment" }), { target: { value: "42" } });
  await waitFor(() => expect(within(dialog).getByText(/1 file selected/)).toBeInTheDocument());
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Jupyter" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  dialog = await openDialog();
  await act(async () => fail(new Error("Quota exceeded")));
  expect(within(dialog).queryByText("Quota exceeded")).not.toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Launch Jupyter" })).toBeEnabled();
});
