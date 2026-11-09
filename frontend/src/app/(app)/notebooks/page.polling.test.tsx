/**
 * Notebook sessions are re-read every 10 s while one is starting, and the notebook image build
 * status every 15 s while it builds (plan 13). The session interval could overlap a slow read; a
 * bucket's late answer could replace the current bucket's; and each bucket change started another
 * recursive build-status timer that nothing ever cancelled.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
const mockComponents = {
  components: [{ key: "jupyterhub", enabled: true }, { key: "rstudio", enabled: true }],
  loading: false,
  failed: false,
  refetch: jest.fn(),
};
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => mockComponents }));

import NotebooksPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];
let buildStatus: { build_id: string | null; build_status: string | null; image_uri: string | null };
let buildStatusReads: number;

function session(id: number, status: string) {
  return {
    id, session_type: "jupyter", status, user: { id: 1, name: `User ${id}`, email: "a@b.c" },
    experiment: null, project: null, cpu_cores: 2, memory_gb: 8, started_at: null,
    proxy_url: null, failure_reason: null,
  };
}

function sessionReads() {
  return pending.filter((p) => p.url.includes("/notebooks/sessions"));
}

async function answer(fragment: string, sessions: unknown[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve({ sessions }));
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  buildStatusReads = 0;
  buildStatus = { build_id: null, build_status: null, image_uri: null };
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/notebooks/sessions")) {
      return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
    }
    if (url.includes("build-status")) {
      buildStatusReads += 1;
      return Promise.resolve(buildStatus);
    }
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [], total: 0 });
    return Promise.resolve({});
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("while a session is starting, a slow read is never overlapped by the next", async () => {
  render(<NotebooksPage />);
  await answer("bucket=active", [session(1, "starting")]);

  await tick(10_000);
  expect(sessionReads()).toHaveLength(1);
  await tick(60_000);
  expect(sessionReads()).toHaveLength(1);
});

test("a bucket's late answer does not replace the current bucket's", async () => {
  render(<NotebooksPage />);
  await answer("bucket=active", [session(1, "running")]);

  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(screen.queryByText("User 1")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  await answer("bucket=all", [session(3, "stopped")]);
  await answer("bucket=recent", [session(2, "running")]);

  expect(screen.getByText("User 3")).toBeInTheDocument();
  expect(screen.queryByText("User 2")).not.toBeInTheDocument();
});

test("the image build is watched by one poll, however often the bucket changes", async () => {
  buildStatus = { build_id: "abcdef123", build_status: "WORKING", image_uri: null };
  render(<NotebooksPage />);
  await answer("bucket=active", []);
  await tick(0);
  expect(buildStatusReads).toBe(1);

  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  await tick(15_000);

  expect(buildStatusReads).toBe(2);
});

test("watching the image build stops when it finishes", async () => {
  buildStatus = { build_id: "abcdef123", build_status: "WORKING", image_uri: null };
  render(<NotebooksPage />);
  await tick(0);
  buildStatus = { build_id: "abcdef123", build_status: "SUCCESS", image_uri: "img" };
  await tick(15_000);
  expect(buildStatusReads).toBe(2);
  await tick(60_000);
  expect(buildStatusReads).toBe(2);
});

test("watching the image build stops when the page closes mid-build", async () => {
  buildStatus = { build_id: "abcdef123", build_status: "WORKING", image_uri: null };
  const { unmount } = render(<NotebooksPage />);
  await tick(0);
  expect(buildStatusReads).toBe(1);

  unmount();
  await tick(60_000);
  expect(buildStatusReads).toBe(1);
});
