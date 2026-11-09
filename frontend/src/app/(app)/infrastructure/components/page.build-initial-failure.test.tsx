/**
 * Owner review 3, same class as the notebook page: while a component is provisioning, a failed
 * FIRST build-status read was re-checked but never said, because the "could not be refreshed"
 * line sat inside the building panel, which needs a successful answer.
 */
import { render, screen, act } from "@/testing/renderWithProviders";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
  ApiError: class extends Error {},
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "mock-token",
  getCurrentUser: () => ({ role: "admin", email: "test@test.com" }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/infrastructure/components",
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

import InfraComponentsPage from "./page";

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let buildReads: Pending[];
let componentStatus: string;
let buildStatusReply: (() => Promise<unknown>) | null;

const component = () => ({
  key: "jupyterhub",
  name: "JupyterHub",
  category: "analysis",
  description: "Notebooks",
  cost_estimate: "$50",
  dependencies: [],
  status: componentStatus,
  configurable: false,
});

function serve() {
  mockApiGet.mockImplementation((url: string) => {
    if (url.includes("terraform/status")) {
      return Promise.resolve({ terraform_initialized: true, gcp_credentials_configured: true, active_run_id: null, active_run_status: null });
    }
    if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
    if (url.includes("stack/status")) {
      return Promise.resolve({ compute_stack: "kubernetes", compute_deployed: true, storage_deployed: true, cluster: null });
    }
    if (url.includes("stack/components")) {
      return Promise.resolve({ compute_stack: "kubernetes", compute_deployed: true, storage_deployed: true, components: [component()] });
    }
    if (url.includes("notebook-image/build-status")) {
      if (buildStatusReply) return buildStatusReply();
      return new Promise((resolve, reject) => buildReads.push({ resolve, reject }));
    }
    return Promise.reject(new Error("Not found"));
  });
}

function componentListReads() {
  return mockApiGet.mock.calls.filter(([url]) => String(url).includes("stack/components")).length;
}

async function flush() {
  await act(async () => {
    for (let n = 0; n < 20; n++) await Promise.resolve();
  });
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await flush();
}

async function settle(fn: () => void) {
  await act(async () => {
    fn();
  });
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  buildReads = [];
  componentStatus = "provisioning";
  buildStatusReply = null;
  mockApiGet.mockReset();
  mockApiPost.mockReset();
  serve();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a failed first build-status read while provisioning is said, and clears when a read succeeds", async () => {
  render(<InfraComponentsPage />);
  await flush();
  await settle(() => buildReads[0].reject(new Error("502")));

  expect(screen.getByTestId("build-status-load-failed")).toHaveTextContent(/could not be read/i);

  await tick(15_000);
  await settle(() => buildReads[1].resolve({ build_id: "abcdef123456", build_status: "WORKING", image_uri: null }));
  expect(screen.queryByTestId("build-status-load-failed")).not.toBeInTheDocument();
  expect(screen.getByText(/Status: WORKING/)).toBeInTheDocument();
});
