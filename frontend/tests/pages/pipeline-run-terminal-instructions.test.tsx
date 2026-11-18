/**
 * Terminal instructions on the pipeline run detail page: catalog and custom runs, while running,
 * for users whose effective permissions include notebooks:launch. A stopped-run refusal re-reads
 * the run. The polling transition is in app/(app)/pipelines/runs/[id]/page.terminal.test.tsx.
 */
import { act, render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";

// Stable objects: the page keys effects on them, so fresh ones per render never settle.
const mockRouter = { push: jest.fn() };
const mockParams = { id: "42" };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => mockParams,
  usePathname: () => "/pipelines/runs/42",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "a@b.c", role_name: "custom-role", sub: "1" }),
  getToken: () => "token",
}));
jest.mock("@/components/experiments/ReviewPanel", () => ({ ReviewPanel: () => null }));
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));
const perms = { canAccess: jest.fn() };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (...a: unknown[]) => perms.canAccess(...a),
    roleName: "custom-role",
    loading: false,
    failed: false,
    permissions: new Set(),
  }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: (flag: string) => flag === "ssh_exec", capabilities: {}, loading: false }),
}));
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: (...a: unknown[]) => mockApiGet(...a), post: (...a: unknown[]) => mockApiPost(...a) },
  };
});

import { ApiError } from "@/lib/api";

const baseRun = {
  id: 42,
  pipeline_key: "nf-core/rnaseq",
  pipeline_name: "nf-core/rnaseq",
  pipeline_version: "3.12.0",
  experiment: { id: 1, name: "E" },
  submitted_by: { id: 1, name: "A", email: "a@b.c" },
  status: "running",
  parameters: {},
  input_files: null,
  output_files: null,
  progress: null,
  cost_estimate: null,
  error_message: null,
  work_dir: null,
  slurm_job_id: null,
  compute_job_ref: "bioaf-pipeline-42-1731700000",
  provider_metadata: { job_name: "bioaf-pipeline-42-1731700000", namespace: "bioaf-pipelines" },
  actual_cost: null,
  reference_genome: null,
  alignment_algorithm: null,
  resume_from_run_id: null,
  review_verdict: null,
  started_at: "2026-11-16T10:00:00Z",
  completed_at: null,
  created_at: "2026-11-16T10:00:00Z",
  processes: [],
  samples: [],
  custom_pipeline_version_id: null as number | null,
};

const connection = {
  command:
    "kubectl exec -it --context ctx -n bioaf-pipelines bioaf-pipeline-42-1731700000-x --container pipeline -- /bin/sh",
  setup_guide: "First-time setup",
  warning: "Commands you run and changes you make in this terminal are not tracked by bioAF.",
  target_type: "pipeline_job",
  target_id: "bioaf-pipeline-42-1731700000",
  namespace: "bioaf-pipelines",
};

let run: typeof baseRun;

beforeEach(() => {
  run = { ...baseRun };
  perms.canAccess.mockReset();
  perms.canAccess.mockReturnValue(true);
  mockApiPost.mockReset();
  mockApiPost.mockResolvedValue(connection);
  mockApiGet.mockReset();
  mockApiGet.mockImplementation((url: string) => {
    if (url === "/api/pipeline-runs/42") return Promise.resolve(run);
    if (url.includes("/overview"))
      return Promise.resolve({
        pipeline_id: 3,
        pipeline_name: "My tool",
        version_number: 1,
        code_source_type: "code_blob",
        entrypoint_command: "run.sh",
        environment: null,
        cpu_request: "1",
        memory_request: "1Gi",
      });
    if (url.includes("/references")) return Promise.resolve([]);
    if (url.includes("/logs")) return Promise.resolve({ logs: "", log_source: "pod" });
    return Promise.resolve({});
  });
  global.fetch = jest.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve(""), json: () => Promise.resolve({}) });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
});

function renderPage() {
  const Page = require("@/app/(app)/pipelines/runs/[id]/page").default;
  render(<Page />);
}

test.each([
  ["catalog", null],
  ["custom", 9],
])("a running %s run offers terminal instructions", async (_kind, customVersion) => {
  run.custom_pipeline_version_id = customVersion;
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Show instructions" }));

  expect(await screen.findByTestId("connect-command")).toHaveTextContent(connection.command);
  expect(mockApiPost).toHaveBeenCalledWith("/api/pipeline-runs/42/connect", undefined, expect.anything());
  expect(perms.canAccess).toHaveBeenCalledWith("notebooks", "launch");
});

test.each(["completed", "failed", "pending", "cancelled"])("a %s run does not", async (status) => {
  run.status = status;
  renderPage();
  await screen.findByText(/Run #42/);
  expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
});

test("users without the launch grant do not see it", async () => {
  perms.canAccess.mockReturnValue(false);
  renderPage();
  await screen.findByText(/Run #42/);
  expect(screen.queryByText("Terminal instructions")).not.toBeInTheDocument();
});

test("a stopped-run refusal re-reads the run and explains what to do", async () => {
  renderPage();
  const button = await screen.findByRole("button", { name: "Show instructions" });
  const readsBefore = mockApiGet.mock.calls.filter(([u]) => u === "/api/pipeline-runs/42").length;
  mockApiPost.mockRejectedValueOnce(new ApiError(409, "raw", "connection_unavailable", { reason: "not_running" }));

  fireEvent.click(button);

  expect(await screen.findByTestId("connect-error")).toHaveTextContent(
    "This analysis is no longer running. Refresh its details before requesting new instructions.",
  );
  await waitFor(() =>
    expect(mockApiGet.mock.calls.filter(([u]) => u === "/api/pipeline-runs/42").length).toBeGreaterThan(readsBefore),
  );
});
