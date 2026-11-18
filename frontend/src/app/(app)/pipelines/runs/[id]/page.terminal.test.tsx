/**
 * Expanded terminal instructions belong to a running run: when the page's 10 s re-read finds the
 * run finished, the instructions and the control disappear. Uses the same pending-read harness as
 * page.polling.test.tsx.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

// Stable objects: the page keys effects on them, so fresh ones per render never settle.
const mockRouter = { push: jest.fn() };
const mockParams = { id: "1" };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => mockParams,
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (r: string, a: string) => r === "notebooks" && a === "launch",
    roleName: "custom-role",
    loading: false,
    failed: false,
    permissions: new Set(),
  }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: (flag: string) => flag === "ssh_exec", capabilities: {}, loading: false }),
}));
jest.mock("@/components/experiments/ReviewPanel", () => ({ ReviewPanel: () => null }));

import PipelineRunDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void };
let pending: Pending[];

const RUN = "/api/pipeline-runs/1";

function run(status: string) {
  return {
    id: 1,
    status,
    compute_job_ref: "bioaf-pipeline-1-1731700000",
    pipeline_name: "nf-core/scrnaseq",
    custom_pipeline_version_id: null,
    organization_id: 1,
    processes: [],
    parameters: {},
  };
}

async function answer(url: string, value: unknown) {
  const i = pending.findIndex((p) => p.url === url);
  if (i < 0) throw new Error(`no pending ${url}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve(value));
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith("/references")) return Promise.resolve([]);
    return new Promise((resolve) => pending.push({ url, resolve }));
  });
  mockPost.mockReset();
  mockPost.mockResolvedValue({
    command: "kubectl exec -it --context ctx -n bioaf-pipelines p --container pipeline -- /bin/sh",
    setup_guide: "setup",
    warning: "not tracked by bioAF",
    target_type: "pipeline_job",
    target_id: "bioaf-pipeline-1-1731700000",
    namespace: "bioaf-pipelines",
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("expanded instructions disappear when the re-read finds the run finished", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running"));
  await answer(`${RUN}/logs`, { stdout: "", stderr: "" });

  await act(async () => fireEvent.click(screen.getByTestId("connect-button")));
  expect(screen.getByTestId("connect-command")).toBeInTheDocument();

  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  await answer(RUN, run("completed"));

  expect(screen.queryByTestId("connect-command")).not.toBeInTheDocument();
  expect(screen.queryByTestId("connect-section")).not.toBeInTheDocument();
});
