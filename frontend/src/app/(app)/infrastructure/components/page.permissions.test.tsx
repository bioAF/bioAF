/**
 * Infrastructure actions follow their endpoint permissions: component and cluster changes,
 * teardown and destroying storage need `configure`; starting a deployment needs `deploy`;
 * abandoning a Terraform run needs `create`. A control the role may not use stays visible,
 * disabled, with the reason; it sends nothing, and loading or unreadable permissions allow
 * nothing. Reading the page needs only `view`, and nothing is sent on mount.
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
const mockPermissions = { granted: new Set<string>(), loading: false, failed: false };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) => mockPermissions.granted.has(`${resource}:${action}`),
    loading: mockPermissions.loading,
    failed: mockPermissions.failed,
  }),
}));

function grant(...actions: string[]) {
  mockPermissions.granted = new Set(["view", ...actions].map((a) => `infrastructure:${a}`));
}

const POOL = { name: "p", machine_type: "n2-standard-4", min_nodes: 0, max_nodes: 5, current_nodes: 0, spot: false, status: "RUNNING" };

function serve({ deployed, activeRun = false, storageOnly = false }: { deployed: boolean; activeRun?: boolean; storageOnly?: boolean }) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("terraform/status")) {
      return Promise.resolve({
        terraform_initialized: true,
        terraform_state_bucket: "b",
        gcp_credentials_configured: true,
        active_run_id: activeRun ? 12 : null,
        active_run_status: activeRun ? "applying" : null,
      });
    }
    if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
    if (url.includes("stack/status")) {
      return Promise.resolve({
        compute_stack: deployed ? "kubernetes" : null,
        compute_deployed: deployed,
        storage_deployed: deployed || storageOnly,
        pubsub_configured: false,
        has_orphaned_clusters: false,
        cluster: deployed
          ? { cluster_name: "bioaf", status: "RUNNING", node_count: 0, pipeline_pool: POOL, interactive_pool: POOL }
          : null,
      });
    }
    if (url.includes("stack/components")) {
      return Promise.resolve({
        compute_stack: "kubernetes",
        compute_deployed: deployed,
        storage_deployed: deployed,
        components: deployed
          ? [{ key: "nextflow", name: "Nextflow", category: "pipeline_orchestration", description: "", cost_estimate: "", dependencies: [], status: "disabled", configurable: false }]
          : [],
      });
    }
    if (url.includes("cluster/config")) {
      return Promise.resolve({
        k8s_pipeline_machine_type: "n2-standard-4",
        k8s_pipeline_max_nodes: 5,
        k8s_pipeline_use_spot: false,
        k8s_pipeline_disk_size_gb: 100,
        k8s_pipeline_disk_type: "pd-standard",
        k8s_interactive_machine_type: "n2-standard-4",
        k8s_interactive_max_nodes: 5,
      });
    }
    if (url.includes("orphaned-resources")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  mockPermissions.loading = false;
  mockPermissions.failed = false;
  grant();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function renderPage() {
  await act(async () => {
    render(<InfraComponentsPage />);
  });
  await waitFor(() => expect(screen.queryByText(/Loading infrastructure status/)).not.toBeInTheDocument());
}

function expectGated(button: HTMLElement, reason: RegExp) {
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleDescription(reason);
}

describe("a deployed stack", () => {
  test("view only: every change is disabled with a reason, and nothing is sent", async () => {
    serve({ deployed: true, activeRun: true });
    await renderPage();
    const reason = /does not include infrastructure/;
    expectGated(screen.getByRole("button", { name: "Teardown" }), reason);
    expectGated(screen.getByRole("button", { name: "Enable" }), reason);
    expectGated(screen.getByRole("button", { name: "Abandon" }), reason);
    fireEvent.click(screen.getByRole("button", { name: "Teardown" }));
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    fireEvent.click(screen.getByRole("button", { name: "Abandon" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("configure without deploy or create: component and teardown, not abandon", async () => {
    grant("configure");
    serve({ deployed: true, activeRun: true });
    await renderPage();
    expect(screen.getByRole("button", { name: "Teardown" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Enable" })).toBeEnabled();
    expectGated(screen.getByRole("button", { name: "Abandon" }), /create permission/);
  });

  test("deploy without configure: no teardown, component change or cluster redeploy", async () => {
    grant("deploy");
    serve({ deployed: true });
    await renderPage();
    expectGated(screen.getByRole("button", { name: "Teardown" }), /configure permission/);
    expectGated(screen.getByRole("button", { name: "Enable" }), /configure permission/);
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(await screen.findByRole("button", { name: "Apply and redeploy" })).toHaveAccessibleDescription(/configure permission/);
  });

  test("create: Abandon keeps its confirmation and then abandons the run", async () => {
    grant("create");
    serve({ deployed: true, activeRun: true });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Abandon" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Abandon/ }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/terraform/abandon/12"));
  });

  test("configure: teardown still needs its checkbox confirmation before anything is sent", async () => {
    grant("configure");
    serve({ deployed: true });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Teardown" }));
    const dialog = await screen.findByRole("dialog");
    const confirmButton = within(dialog).getAllByRole("button").find((b) => /tear\s*down/i.test(b.textContent ?? ""))!;
    expect(confirmButton).toBeDisabled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  test.each([
    ["loading", () => (mockPermissions.loading = true), /Checking your permissions/],
    ["unreadable", () => (mockPermissions.failed = true), /could not be confirmed/],
  ])("%s permissions allow nothing", async (_label, setup, reason) => {
    // Infrastructure view is unconfirmed, so the page shows why and mounts no control at all.
    grant("configure", "deploy", "create");
    setup();
    serve({ deployed: true, activeRun: true });
    await renderPage();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Teardown" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abandon" })).not.toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });
});

describe("a stack not yet deployed", () => {
  test("view or configure only: Deploy is disabled with a reason", async () => {
    grant("configure");
    serve({ deployed: false });
    await renderPage();
    const deploy = screen.getByRole("button", { name: /^Deploy/ });
    expectGated(deploy, /deploy permission/);
    fireEvent.click(deploy);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("deploy: the region confirmation opens and starts the deployment", async () => {
    grant("deploy");
    serve({ deployed: false });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^Deploy/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^Deploy/ }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/v1/infrastructure/stack/deploy-background", { stack_type: "kubernetes" }),
    );
  });

  test("storage left behind: Destroy Storage needs configure", async () => {
    grant("deploy");
    serve({ deployed: false, storageOnly: true });
    await renderPage();
    expectGated(screen.getByRole("button", { name: "Destroy Storage" }), /configure permission/);
  });
});

test("nothing is sent on mount, whatever the role", async () => {
  grant("configure", "deploy", "create");
  serve({ deployed: true, activeRun: true });
  await renderPage();
  expect(mockPost).not.toHaveBeenCalled();
});
