/**
 * Image-build status on the Components page. Reads poll every 15 s while a component
 * is provisioning and never overlap; a finished build refreshes the page data, but a build that
 * has already finished must not reload the page in a loop; and a failed read is said.
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

test("a slow build-status read is not overlapped by the next poll", async () => {
  render(<InfraComponentsPage />);
  await flush();
  expect(buildReads.length).toBe(1);

  await tick(120_000);
  expect(buildReads.length).toBe(1);

  await settle(() => buildReads[0].resolve({ build_id: "abcdef123456", build_status: "WORKING", image_uri: null }));
  await tick(15_000);
  expect(buildReads.length).toBe(2);
});

test("a build that has already failed does not reload the page in a loop", async () => {
  componentStatus = "build_failed";
  buildStatusReply = () => Promise.resolve({ build_id: "abcdef123456", build_status: "FAILURE", image_uri: null });
  render(<InfraComponentsPage />);
  await flush();
  await tick(60_000);

  // One load, plus at most the one refresh a finished build asks for.
  expect(componentListReads()).toBeLessThanOrEqual(2);
  expect(screen.getByText("Image build failed")).toBeInTheDocument();
});

test("a failed build-status read keeps the last status and says it could not be refreshed", async () => {
  render(<InfraComponentsPage />);
  await flush();
  await settle(() => buildReads[0].resolve({ build_id: "abcdef123456", build_status: "WORKING", image_uri: null }));
  expect(screen.getByText(/Status: WORKING/)).toBeInTheDocument();

  await tick(15_000);
  await settle(() => buildReads[1].reject(new Error("502")));

  expect(screen.getByText(/Status: WORKING/)).toBeInTheDocument();
  expect(screen.getByTestId("build-status-unavailable")).toBeInTheDocument();
});

test("the build-status read carries a signal that aborts when the page closes", async () => {
  const { unmount } = render(<InfraComponentsPage />);
  await flush();
  const call = mockApiGet.mock.calls.find(([url]) => String(url).includes("notebook-image/build-status"))!;
  expect(call[1]?.signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(call[1].signal.aborted).toBe(true);
});

test("a component no longer building shows no build status and is not polled", async () => {
  componentStatus = "enabled";
  render(<InfraComponentsPage />);
  await flush();
  await tick(60_000);
  expect(buildReads).toHaveLength(0);
  expect(screen.queryByText(/Building image/)).not.toBeInTheDocument();
});
