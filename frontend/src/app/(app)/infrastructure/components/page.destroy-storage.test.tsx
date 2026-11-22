/**
 * Destroy Storage is unavailable while bioAF could not confirm the storage buckets are its own
 * (storage lifecycle 11G; signed-off mockup M15).
 */
import { act, render, screen, waitFor } from "@/testing/renderWithProviders";
import InfraComponentsPage from "./page";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: jest.fn() },
}));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true, getToken: () => "t", getCurrentUser: () => ({}) }));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/infrastructure/components",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

const STACK = {
  compute_stack: null,
  compute_deployed: false,
  storage_deployed: true,
  pubsub_configured: false,
  has_orphaned_clusters: false,
  cluster: null,
};

function serve(unconfirmed: number) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("terraform/status")) {
      return Promise.resolve({
        terraform_initialized: true,
        terraform_state_bucket: "b",
        gcp_credentials_configured: true,
        active_run_id: null,
        active_run_status: null,
      });
    }
    if (url.includes("storage/health")) return Promise.resolve({ checked_at: null, stores: [], unconfirmed });
    if (url.includes("stack/status")) return Promise.resolve(STACK);
    if (url.includes("stack/components")) {
      return Promise.resolve({ compute_stack: null, compute_deployed: false, storage_deployed: true, components: [] });
    }
    if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
    if (url.includes("orphaned-resources")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

async function renderPage() {
  await act(async () => {
    render(<InfraComponentsPage />);
  });
  await waitFor(() => expect(screen.queryByText(/Loading infrastructure status/)).not.toBeInTheDocument());
}

test("unconfirmed buckets make Destroy Storage unavailable with the M15 reason", async () => {
  serve(2);
  await renderPage();
  const destroy = await screen.findByRole("button", { name: "Destroy Storage" });
  await waitFor(() => expect(destroy).toBeDisabled());
  expect(destroy).toHaveAttribute("title", "Unavailable until an administrator confirms the storage buckets.");
});

test("confirmed buckets leave Destroy Storage available", async () => {
  serve(0);
  await renderPage();
  const destroy = await screen.findByRole("button", { name: "Destroy Storage" });
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/v1/infrastructure/storage/health", expect.anything()));
  expect(destroy).toBeEnabled();
});
