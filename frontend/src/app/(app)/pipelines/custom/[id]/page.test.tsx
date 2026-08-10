import { render, screen, waitFor } from "@/testing/renderWithProviders";

const mockGetParam = jest.fn();
// One router object for the whole file: the page's load effect lists `router`
// in its dependencies, so a fresh object per render would re-fetch forever.
const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: "42" }),
  useSearchParams: () => ({ get: (k: string) => mockGetParam(k) }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));

// Stand in for the dialog so the assertion is about what the page hands it.
jest.mock("@/components/pipelines/CustomPipelineLaunchDialog", () => ({
  CustomPipelineLaunchDialog: ({ initialExperimentId }: { initialExperimentId?: number | null }) => (
    <div data-testid="launch-dialog" data-initial-experiment={String(initialExperimentId ?? "")} />
  ),
}));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));

import CustomPipelineDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const detail = {
  id: 42,
  name: "In-house RNA-seq",
  description: null,
  pipeline_key: "in-house-rnaseq",
  created_by_user_id: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  versions: [
    {
      id: 9,
      version_number: 3,
      code_source_type: "code_blob",
      github_repo_id: null,
      code_content: "echo hi",
      entrypoint_command: "bash run.sh",
      environment_version_id: 5,
      cpu_request: "2",
      memory_request: "8Gi",
      log_file_path: null,
      version_trigger: "manual",
      status: "active",
      created_by_user_id: 1,
      created_at: "2026-01-01T00:00:00Z",
      variables: [],
      qc_template: null,
      qc_config_json: null,
    },
  ],
};

beforeEach(() => {
  mockGetParam.mockReset();
  mockGetParam.mockImplementation((k: string) => (k === "launch" ? "1" : null));
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/v1/custom-pipelines/")) return Promise.resolve(detail);
    if (url.startsWith("/api/v1/environments")) return Promise.resolve({ environments: [] });
    if (url.startsWith("/api/v1/github-repos")) return Promise.resolve({ repos: [], total: 0 });
    return Promise.resolve({});
  });
});

test("hands the launch dialog the experiment the user arrived with", async () => {
  mockGetParam.mockImplementation((k: string) =>
    k === "launch" ? "1" : k === "experiment" ? "15" : null,
  );

  render(<CustomPipelineDetailPage />);

  const dialog = await screen.findByTestId("launch-dialog");
  expect(dialog).toHaveAttribute("data-initial-experiment", "15");
});

test("hands it nothing when the page was opened without an experiment", async () => {
  render(<CustomPipelineDetailPage />);

  const dialog = await screen.findByTestId("launch-dialog");
  expect(dialog).toHaveAttribute("data-initial-experiment", "");
});

test("ignores an experiment parameter that is not a number", async () => {
  mockGetParam.mockImplementation((k: string) =>
    k === "launch" ? "1" : k === "experiment" ? "not-an-id" : null,
  );

  render(<CustomPipelineDetailPage />);

  const dialog = await screen.findByTestId("launch-dialog");
  expect(dialog).toHaveAttribute("data-initial-experiment", "");
});

test("does not open the dialog on its own without ?launch=1", async () => {
  mockGetParam.mockImplementation((k: string) => (k === "experiment" ? "15" : null));

  render(<CustomPipelineDetailPage />);

  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByTestId("launch-dialog")).not.toBeInTheDocument();
});
