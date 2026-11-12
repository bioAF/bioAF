/**
 * Custom-pipeline detail drafts. The new-version draft is seeded from the latest version when the
 * form opens (or when ?newVersion=1 opened it and the data arrives); a later reload of the
 * pipeline (after a deprecate or a metadata save) never overwrites an edit in progress. The page
 * belongs to one pipeline: a late answer for a previous one is never shown.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";

let mockId = "42";
let mockQuery = "";
const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: mockId }),
  useSearchParams: () => new URLSearchParams(mockQuery),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
const mockConfirm = jest.fn();
jest.mock("@/hooks/useConfirm", () => ({
  ...jest.requireActual("@/hooks/useConfirm"),
  useConfirm: () => mockConfirm,
}));
jest.mock("@/components/pipelines/CustomPipelineLaunchDialog", () => ({
  CustomPipelineLaunchDialog: () => <div data-testid="launch-dialog" />,
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

import CustomPipelineDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockPut = api.put as jest.Mock;

function version(id: number, number: number, entrypoint: string, status = "active") {
  return {
    id,
    version_number: number,
    code_source_type: "code_blob",
    github_repo_id: null,
    code_content: "echo hi",
    entrypoint_command: entrypoint,
    environment_version_id: 5,
    cpu_request: "1",
    memory_request: "4Gi",
    log_file_path: null,
    version_trigger: "user",
    status,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    variables: [],
    qc_template: null,
    qc_config_json: null,
  };
}

function detail(id: number, name: string, versions: unknown[]) {
  return {
    id,
    name,
    description: null,
    pipeline_key: `key-${id}`,
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    versions,
  };
}

const ENV_LIST = { environments: [{ id: 1, name: "Env" }] };
const ENV_DETAIL = { id: 1, name: "Env", versions: [{ id: 5, version_number: 1, status: "ready" }] };

let held: Record<string, Array<(v: unknown) => void>>;
let current: unknown;

function serve({ hold = [] as string[] } = {}) {
  mockGet.mockImplementation((url: string) => {
    const key = hold.find((h) => url === h || url.startsWith(h));
    if (key) return new Promise((resolve) => (held[key] ??= []).push(resolve));
    if (url === "/api/v1/custom-pipelines/42") return Promise.resolve(current);
    if (url === "/api/v1/custom-pipelines/43") return Promise.resolve(detail(43, "Second pipeline", []));
    if (url.startsWith("/api/v1/environments?")) return Promise.resolve(ENV_LIST);
    if (url.startsWith("/api/v1/environments/1")) return Promise.resolve(ENV_DETAIL);
    if (url.startsWith("/api/v1/github-repos")) return Promise.resolve({ repos: [], total: 0 });
    return Promise.resolve({});
  });
}

beforeEach(() => {
  mockId = "42";
  mockQuery = "";
  held = {};
  current = detail(42, "First pipeline", [version(9, 2, "bash v2.sh"), version(8, 1, "bash v1.sh")]);
  mockGet.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  mockConfirm.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("a reload after deprecating a version keeps the new-version edit in progress", async () => {
  serve();
  mockConfirm.mockResolvedValue(true);
  mockPost.mockResolvedValue({});
  render(<CustomPipelineDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "New Version" }));
  const entrypoint = screen.getByLabelText("Entrypoint command");
  expect(entrypoint).toHaveValue("bash v2.sh");
  fireEvent.change(entrypoint, { target: { value: "python edited.py" } });

  current = detail(42, "First pipeline", [version(9, 2, "bash v2.sh", "deprecated"), version(8, 1, "bash v1.sh")]);
  fireEvent.click(screen.getAllByRole("button", { name: "Deprecate" })[0]);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Deprecate" })).toHaveLength(1));

  expect(screen.getByLabelText("Entrypoint command")).toHaveValue("python edited.py");
});

test("a reload never replaces the name being edited", async () => {
  serve();
  mockConfirm.mockResolvedValue(true);
  mockPost.mockResolvedValue({});
  render(<CustomPipelineDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Edit name"), { target: { value: "Renamed" } });

  current = detail(42, "First pipeline", [version(9, 2, "bash v2.sh", "deprecated"), version(8, 1, "bash v1.sh")]);
  fireEvent.click(screen.getAllByRole("button", { name: "Deprecate" })[0]);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Deprecate" })).toHaveLength(1));
  expect(screen.getByLabelText("Edit name")).toHaveValue("Renamed");
});

test("?newVersion=1 seeds from the latest version once the template list arrives", async () => {
  mockQuery = "newVersion=1";
  serve({ hold: ["/api/v1/environments?"] });
  render(<CustomPipelineDetailPage />);
  await screen.findByRole("heading", { name: "First pipeline" });

  await act(async () => held["/api/v1/environments?"][0](ENV_LIST));
  await waitFor(() => expect(screen.getByLabelText("Entrypoint command")).toHaveValue("bash v2.sh"));
  await waitFor(() => expect(screen.getByLabelText("Pipeline Template")).toHaveValue("5"));
});

test("?launch=1 opens the launch dialog only when a version can be launched", async () => {
  mockQuery = "launch=1";
  current = detail(42, "First pipeline", [version(9, 1, "bash v1.sh", "deprecated")]);
  serve();
  render(<CustomPipelineDetailPage />);
  await screen.findByRole("heading", { name: "First pipeline" });
  expect(screen.queryByTestId("launch-dialog")).not.toBeInTheDocument();
});

test("a late answer for the previous pipeline is never shown", async () => {
  serve({ hold: ["/api/v1/custom-pipelines/42"] });
  const view = render(<CustomPipelineDetailPage />);
  mockId = "43";
  view.rerender(<CustomPipelineDetailPage />);
  expect(await screen.findByRole("heading", { name: "Second pipeline" })).toBeInTheDocument();

  await act(async () => held["/api/v1/custom-pipelines/42"][0](detail(42, "First pipeline", [])));
  expect(screen.queryByRole("heading", { name: "First pipeline" })).not.toBeInTheDocument();
});

test("Create Version sends once however fast it is pressed, and can be retried after a failure", async () => {
  serve();
  mockPost.mockImplementationOnce(() => Promise.reject(new Error("Quota reached"))).mockResolvedValue({});
  render(<CustomPipelineDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "New Version" }));
  await waitFor(() => expect(screen.getByLabelText("Pipeline Template")).toHaveValue("5"));
  const create = screen.getByRole("button", { name: "Create Version" });
  act(() => {
    create.click();
    create.click();
  });
  expect(mockPost).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Quota reached")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Create Version" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
});

test("QC config that is not an object is refused with the same rule the field shows", async () => {
  serve();
  render(<CustomPipelineDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "New Version" }));
  fireEvent.click(screen.getByRole("button", { name: /QC dashboard config/ }));
  fireEvent.change(screen.getByLabelText(/QC config JSON/), { target: { value: "[1, 2]" } });
  expect(screen.getByText("JSON: Must be a JSON object")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Create Version" }));
  const form = screen.getByText("New Version", { selector: "h3" }).parentElement!;
  expect(await within(form).findByText(/QC config JSON must be an object/)).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});
