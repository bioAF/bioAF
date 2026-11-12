/**
 * The infrastructure page tells apart "could not read" from "not deployed". A failed first read of
 * the stack status is an error with a retry, never the not-deployed view that offers Deploy. A read
 * the role may not make (terraform status needs change_status) leaves the rest of the page usable.
 * A failed refresh keeps what was shown and says it may be out of date. Reads carry a signal and
 * stop when the page goes away.
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
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

const TF = { terraform_initialized: true, terraform_state_bucket: "b", gcp_credentials_configured: true, active_run_id: null, active_run_status: null };
const NOT_DEPLOYED = { compute_stack: null, compute_deployed: false, storage_deployed: false, pubsub_configured: false, has_orphaned_clusters: false, cluster: null };
const COMPONENTS = { compute_stack: "kubernetes", compute_deployed: false, storage_deployed: false, components: [] };

let failStack: "never" | "always" | "after-first" = "never";
let stackReads = 0;

function serve(overrides: Record<string, () => Promise<unknown>> = {}) {
  mockGet.mockImplementation((url: string) => {
    for (const [fragment, reply] of Object.entries(overrides)) if (url.includes(fragment)) return reply();
    if (url.includes("terraform/status")) return Promise.resolve(TF);
    if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
    if (url.includes("stack/status")) {
      stackReads += 1;
      const fail = failStack === "always" || (failStack === "after-first" && stackReads > 1);
      return fail ? Promise.reject(Object.assign(new Error("Server Error"), { status: 500 })) : Promise.resolve(NOT_DEPLOYED);
    }
    if (url.includes("stack/components")) return Promise.resolve(COMPONENTS);
    if (url.includes("orphaned-resources")) return Promise.resolve({ items: [], total: 0 });
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  failStack = "never";
  stackReads = 0;
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

async function renderPage() {
  let view: ReturnType<typeof render> | undefined;
  await act(async () => {
    view = render(<InfraComponentsPage />);
  });
  await waitFor(() => expect(screen.queryByText(/Loading infrastructure status/)).not.toBeInTheDocument());
  return view!;
}

test("a failed stack status read is an error with a retry, not an offer to deploy", async () => {
  failStack = "always";
  serve();
  await renderPage();
  expect(screen.getByText(/Infrastructure status could not be loaded/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Deploy/ })).not.toBeInTheDocument();
  expect(screen.queryByText("Select Compute Stack")).not.toBeInTheDocument();

  failStack = "never";
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Select Compute Stack")).toBeInTheDocument();
});

test("a terraform status the role may not read leaves the rest of the page", async () => {
  serve({ "terraform/status": () => Promise.reject(Object.assign(new Error("Forbidden"), { status: 403 })) });
  await renderPage();
  expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  expect(screen.getByText("Recent Operations")).toBeInTheDocument();
});

test("a failed refresh keeps the page and says it may be out of date", async () => {
  const deployed = {
    ...NOT_DEPLOYED,
    compute_stack: "kubernetes",
    compute_deployed: true,
    storage_deployed: true,
  };
  let reads = 0;
  serve({
    "stack/status": () => {
      reads += 1;
      return reads === 1 ? Promise.resolve(deployed) : Promise.reject(Object.assign(new Error("Server Error"), { status: 500 }));
    },
    "stack/components": () =>
      Promise.resolve({
        ...COMPONENTS,
        compute_deployed: true,
        components: [
          { key: "nextflow", name: "Nextflow", category: "pipeline_orchestration", description: "", cost_estimate: "", dependencies: [], status: "disabled", configurable: false },
        ],
      }),
  });
  mockPost.mockResolvedValue({});
  await renderPage();
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  expect(await screen.findByTestId("stale-notice")).toHaveTextContent(/Infrastructure status could not be refreshed/);
  expect(screen.getByRole("button", { name: "Teardown" })).toBeInTheDocument();
});

test("a read still in flight is aborted when the page goes away", async () => {
  let stackSignal: AbortSignal | undefined;
  mockGet.mockImplementation((url: string, controls?: { signal?: AbortSignal }) => {
    if (url.includes("stack/status")) {
      stackSignal = controls?.signal;
      return new Promise(() => {});
    }
    return Promise.reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
  let view: ReturnType<typeof render> | undefined;
  await act(async () => {
    view = render(<InfraComponentsPage />);
  });
  expect(screen.getByText(/Loading infrastructure status/)).toBeInTheDocument();
  expect(stackSignal?.aborted).toBe(false);
  view!.unmount();
  expect(stackSignal?.aborted).toBe(true);
});
