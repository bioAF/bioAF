/**
 * Reaching the infrastructure page is not the same as being allowed to read it. The navigation hides
 * the link from a role without infrastructure view, but the URL still opens the page: it says the
 * role lacks access, without a Retry that could never succeed, and reads nothing. A 403 from a
 * view-protected read says the same. A server or network failure is still an error with Retry. The
 * Terraform status needs change_status, so its 403 never implies that view is missing.
 */
import { act, fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
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

let grants = new Set<string>();
let permsLoading = false;
let permsFailed = false;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) => grants.has(`${resource}:${action}`),
    loading: permsLoading,
    failed: permsFailed,
  }),
}));

const TF = { terraform_initialized: true, terraform_state_bucket: "b", gcp_credentials_configured: true, active_run_id: null, active_run_status: null };
const NOT_DEPLOYED = { compute_stack: null, compute_deployed: false, storage_deployed: false, pubsub_configured: false, has_orphaned_clusters: false, cluster: null };
const COMPONENTS = { compute_stack: "kubernetes", compute_deployed: false, storage_deployed: false, components: [] };

const fail = (status: number) => () => Promise.reject(Object.assign(new Error(status === 403 ? "Forbidden" : "Server Error"), { status }));

function serve(overrides: Record<string, () => Promise<unknown>> = {}) {
  mockGet.mockImplementation((url: string) => {
    for (const [fragment, reply] of Object.entries(overrides)) if (url.includes(fragment)) return reply();
    if (url.includes("terraform/status")) return Promise.resolve(TF);
    if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
    if (url.includes("stack/status")) return Promise.resolve(NOT_DEPLOYED);
    if (url.includes("stack/components")) return Promise.resolve(COMPONENTS);
    if (url.includes("orphaned-resources")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
}

const infrastructureReads = () => mockGet.mock.calls.filter(([url]) => String(url).includes("/api/v1/infrastructure/"));

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  grants = new Set(["infrastructure:view", "infrastructure:change_status"]);
  permsLoading = false;
  permsFailed = false;
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function renderPage() {
  await act(async () => {
    render(<InfraComponentsPage />);
  });
  await waitFor(() => expect(screen.queryByText(/Loading infrastructure status/)).not.toBeInTheDocument());
}

test("a role without infrastructure view is told so, with no Retry and no reads", async () => {
  grants = new Set(["experiments:view"]);
  serve();
  await renderPage();
  expect(screen.getByText(/Your role does not include infrastructure view permission/)).toBeInTheDocument();
  expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  expect(infrastructureReads()).toHaveLength(0);
});

test("while permissions load, the page checks them and reads nothing", async () => {
  permsLoading = true;
  serve();
  await act(async () => {
    render(<InfraComponentsPage />);
  });
  expect(screen.getByText(/Checking your permissions/)).toBeInTheDocument();
  expect(infrastructureReads()).toHaveLength(0);
});

test("permissions that could not be read are not presented as a missing grant", async () => {
  permsFailed = true;
  serve();
  await renderPage();
  expect(screen.getByText(/Your permissions could not be confirmed/)).toBeInTheDocument();
  expect(screen.queryByText(/Your role does not include/)).not.toBeInTheDocument();
  expect(infrastructureReads()).toHaveLength(0);
});

test.each(["stack/status", "stack/components"])(
  "a 403 from %s explains the missing access instead of offering Retry",
  async (fragment) => {
    serve({ [fragment]: fail(403) });
    await renderPage();
    expect(screen.getByText(/Your role does not include infrastructure view permission/)).toBeInTheDocument();
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Deploy/ })).not.toBeInTheDocument();
  },
);

test("a 500 from the stack status is still an error with Retry that recovers", async () => {
  let broken = true;
  serve({ "stack/status": () => (broken ? fail(500)() : Promise.resolve(NOT_DEPLOYED)) });
  await renderPage();
  expect(screen.getByText(/Infrastructure status could not be loaded/)).toBeInTheDocument();
  expect(screen.queryByText(/Your role does not include/)).not.toBeInTheDocument();
  broken = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Select Compute Stack")).toBeInTheDocument();
});

test("view without change_status: the Terraform status 403 leaves the page usable", async () => {
  grants = new Set(["infrastructure:view"]);
  serve({ "terraform/status": fail(403) });
  await renderPage();
  expect(screen.queryByText(/Your role does not include infrastructure view permission/)).not.toBeInTheDocument();
  expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  expect(screen.getByText("Recent Operations")).toBeInTheDocument();
});
