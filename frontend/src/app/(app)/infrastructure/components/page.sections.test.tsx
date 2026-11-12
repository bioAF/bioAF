/**
 * Recent operations and the cluster configuration each report their own read. A failed first read
 * is an error with a Retry, never an empty history or a blank panel; Retry recovers; a failed
 * refresh keeps what was shown and says it may be out of date. One section failing leaves the
 * others working, and retrying the configuration keeps the edits not yet applied.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";
import InfraComponentsPage from "./page";

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true, getToken: () => "t", getCurrentUser: () => ({}) }));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => mockRouter, usePathname: () => "/infrastructure/components" }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

const POOL = { name: "p", machine_type: "n2-standard-4", min_nodes: 0, max_nodes: 5, current_nodes: 0, spot: false, status: "RUNNING" };
const STACK = {
  compute_stack: "kubernetes",
  compute_deployed: true,
  storage_deployed: true,
  pubsub_configured: false,
  has_orphaned_clusters: false,
  cluster: { cluster_name: "bioaf-lab", status: "RUNNING", node_count: 0, pipeline_pool: POOL, interactive_pool: POOL },
};
const COMPONENTS = {
  compute_stack: "kubernetes",
  compute_deployed: true,
  storage_deployed: true,
  components: [
    { key: "nextflow", name: "Nextflow", category: "pipeline_orchestration", description: "", cost_estimate: "", dependencies: [], status: "disabled", configurable: false },
  ],
};
const CONFIG = {
  k8s_pipeline_machine_type: "n2-standard-4",
  k8s_pipeline_max_nodes: 5,
  k8s_pipeline_use_spot: false,
  k8s_pipeline_disk_size_gb: 100,
  k8s_pipeline_disk_type: "pd-standard",
  k8s_interactive_machine_type: "n2-standard-4",
  k8s_interactive_max_nodes: 5,
};
const RUN = {
  id: 1,
  action: "apply",
  module_name: "compute",
  status: "completed",
  resources_planned: 1,
  resources_completed: 1,
  triggered_by_user_id: 1,
  started_at: "2026-03-11T00:00:00Z",
  completed_at: "2026-03-11T00:01:00Z",
  error_message: null,
  plan_json: null,
  terraform_state_url: null,
};

const failure = () => Promise.reject(Object.assign(new Error("Server Error"), { status: 500 }));
/** Per-read answers by call number (1-based); the last one repeats. */
let answers: Record<string, Array<() => Promise<unknown>>>;
let calls: Record<string, number>;

function serve() {
  mockGet.mockImplementation((url: string) => {
    for (const fragment of Object.keys(answers)) {
      if (url.includes(fragment)) {
        calls[fragment] = (calls[fragment] ?? 0) + 1;
        const list = answers[fragment];
        return list[Math.min(calls[fragment], list.length) - 1]();
      }
    }
    if (url.includes("terraform/status")) {
      return Promise.resolve({ terraform_initialized: true, terraform_state_bucket: "b", gcp_credentials_configured: true, active_run_id: null, active_run_status: null });
    }
    if (url.includes("stack/status")) return Promise.resolve(STACK);
    if (url.includes("stack/components")) return Promise.resolve(COMPONENTS);
    if (url.includes("orphaned-resources")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  answers = {
    "terraform/runs": [() => Promise.resolve({ runs: [RUN] })],
    "cluster/config": [() => Promise.resolve(CONFIG)],
  };
  calls = {};
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function renderPage() {
  await act(async () => {
    render(<InfraComponentsPage />);
  });
  await waitFor(() => expect(screen.queryByText(/Loading infrastructure status/)).not.toBeInTheDocument());
}

/** A component change reloads the page's reads. */
async function reloadThroughAComponentChange() {
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await waitFor(() => expect(mockPost).toHaveBeenCalled());
}

/** The pipeline pool's Max Nodes field (the interactive pool has one too). */
const pipelineMaxNodes = () => screen.getAllByLabelText("Max Nodes")[0];

const history = () => screen.getByRole("region", { name: "Recent Operations" });

describe("recent operations", () => {
  test("a failed first read is an error with Retry, not an empty history, and Retry recovers", async () => {
    answers["terraform/runs"] = [failure, () => Promise.resolve({ runs: [RUN] })];
    serve();
    await renderPage();
    expect(within(history()).getByText(/Terraform operations could not be loaded/)).toBeInTheDocument();
    expect(within(history()).queryByText(/No Terraform operations recorded yet/)).not.toBeInTheDocument();

    fireEvent.click(within(history()).getByRole("button", { name: "Retry" }));
    expect(await within(history()).findByText("apply")).toBeInTheDocument();
    expect(within(history()).queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  test("an empty history is said only after a successful empty answer", async () => {
    answers["terraform/runs"] = [() => Promise.resolve({ runs: [] })];
    serve();
    await renderPage();
    expect(within(history()).getByText(/No Terraform operations recorded yet/)).toBeInTheDocument();
  });

  test("a failed refresh keeps the rows and says they may be out of date", async () => {
    answers["terraform/runs"] = [() => Promise.resolve({ runs: [RUN] }), failure];
    serve();
    await renderPage();
    await reloadThroughAComponentChange();
    expect(await within(history()).findByTestId("stale-notice")).toHaveTextContent(/Terraform operations could not be refreshed/);
    expect(within(history()).getByText("apply")).toBeInTheDocument();
  });

  test("the rest of the page keeps working while the history fails", async () => {
    answers["terraform/runs"] = [failure];
    serve();
    await renderPage();
    expect(screen.getByText("bioaf-lab")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/stack/components/nextflow/toggle"),
    );
  });
});

describe("cluster configuration", () => {
  test("a failed read shows an error in the panel, not a blank one, and Retry recovers", async () => {
    answers["cluster/config"] = [failure, () => Promise.resolve(CONFIG)];
    serve();
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(screen.getByText(/Cluster configuration could not be loaded/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(pipelineMaxNodes()).toHaveValue(5));
    expect(screen.queryByText(/Cluster configuration could not be loaded/)).not.toBeInTheDocument();
  });

  test("a failed configuration read leaves cluster status and components usable", async () => {
    answers["cluster/config"] = [failure];
    serve();
    await renderPage();
    expect(screen.getByText("bioaf-lab")).toBeInTheDocument();
    expect(screen.getAllByText(/Max Nodes: 5/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Infrastructure status could not be loaded/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable" })).toBeEnabled();
  });

  test("a failed refresh keeps the form with a stale notice, and Retry keeps unapplied edits", async () => {
    answers["cluster/config"] = [
      () => Promise.resolve(CONFIG),
      failure,
      () => Promise.resolve({ ...CONFIG, k8s_interactive_max_nodes: 6 }),
    ];
    serve();
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    await waitFor(() => expect(pipelineMaxNodes()).toBeInTheDocument());
    fireEvent.change(pipelineMaxNodes(), { target: { value: "9" } });

    await reloadThroughAComponentChange();
    const notice = await screen.findByText(/Cluster configuration could not be refreshed/);
    expect(pipelineMaxNodes()).toHaveValue(9);

    fireEvent.click(within(notice.closest("[data-testid='stale-notice']") as HTMLElement).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByText(/Cluster configuration could not be refreshed/)).not.toBeInTheDocument());
    expect(pipelineMaxNodes()).toHaveValue(9);
    expect(calls["terraform/runs"]).toBe(2);
  });
});
