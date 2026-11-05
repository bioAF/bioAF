import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";

// The New Version form. Seen live on blackbird: its old 2 CPU / 8Gi default never fitted the node a
// custom job runs on, and a variable copied from the last version could be added again under the
// same name.

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: "42" }),
  useSearchParams: () => ({ get: () => null }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/pipelines/CustomPipelineLaunchDialog", () => ({
  CustomPipelineLaunchDialog: () => null,
}));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));

import CustomPipelineDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const version = {
  id: 9,
  version_number: 1,
  code_source_type: "code_blob",
  github_repo_id: null,
  code_content: "echo hi",
  entrypoint_command: "bash run.sh",
  environment_version_id: 5,
  cpu_request: "1",
  memory_request: "4Gi",
  log_file_path: null,
  version_trigger: "user",
  status: "active",
  created_by_user_id: 1,
  created_at: "2026-01-01T00:00:00Z",
  variables: [
    {
      id: 1,
      variable_name: "greeting",
      default_value: "hello",
      variable_type: "string",
      reference_category: null,
      is_required: true,
    },
  ],
  qc_template: null,
  qc_config_json: null,
};

function detail(versions: unknown[]) {
  return {
    id: 42,
    name: "pl-live-custom",
    description: null,
    pipeline_key: "pl-live-custom",
    created_by_user_id: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    versions,
  };
}

function serve(versions: unknown[]) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/v1/custom-pipelines/")) return Promise.resolve(detail(versions));
    if (url.startsWith("/api/v1/environments")) return Promise.resolve({ environments: [] });
    if (url.startsWith("/api/v1/github-repos")) return Promise.resolve({ repos: [], total: 0 });
    return Promise.resolve({});
  });
}

async function openNewVersion() {
  render(<CustomPipelineDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "New Version" }));
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

test("a first version defaults to 1 CPU and 4Gi, which a custom job's node can schedule", async () => {
  serve([]);
  await openNewVersion();

  expect((await screen.findByLabelText("CPU")) as HTMLInputElement).toHaveValue("1");
  expect(screen.getByLabelText("Memory") as HTMLInputElement).toHaveValue("4Gi");
});

test("two variables with one name are refused before anything is sent", async () => {
  serve([version]);
  await openNewVersion();

  fireEvent.click(await screen.findByRole("button", { name: "+ Add Variable" }));
  const names = screen.getAllByPlaceholderText("Variable name");
  fireEvent.change(names[names.length - 1], { target: { value: "greeting" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Version" }));

  expect(await screen.findByText(/"greeting" is used more than once/)).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});

test("a version the server refuses shows the server's reason", async () => {
  serve([version]);
  mockPost.mockRejectedValue(
    new Error("This pipeline version asks for 2 CPU and 8Gi of memory, but a custom pipeline job can use at most 1.4 CPU and 5Gi of memory here, so it would never start. Lower its CPU or memory."),
  );
  await openNewVersion();

  fireEvent.click(await screen.findByRole("button", { name: "Create Version" }));

  await waitFor(() => expect(mockPost).toHaveBeenCalled());
  expect(await screen.findByText(/at most 1.4 CPU and 5Gi/)).toBeInTheDocument();
});
