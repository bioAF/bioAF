/**
 * Work nodes are re-read every 10 s while one is starting or stopping. Reads must not
 * overlap, a failed background read must keep the list but say so, and switching the session
 * bucket must not show the previous bucket's nodes as the new bucket's, or let its late answer in.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => mockRouter }));
// A stable canAccess, as the real hook's is (useCallback): the page's effects depend on it.
const mockPermissions = { canAccess: () => true, loading: false };
const mockCapabilities = { capabilities: {}, has: () => true, loading: false };
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions }));
jest.mock("@/hooks/useCapabilities", () => ({ useCapabilities: () => mockCapabilities }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import WorkNodesPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function node(id: number, status: string, machine_type = `machine-${id}`) {
  return {
    id, status, machine_type, user: { id: 1, name: "Ada", email: "a@b.c" }, project: null,
    cpu_cores: 4, memory_gb: 16, started_at: null, access_url: null,
  };
}

function sessionReads() {
  return pending.filter((p) => p.url.includes("/work-nodes/sessions"));
}

async function answer(fragment: string, sessions: unknown[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve({ sessions }));
}

async function fail(fragment: string) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  const [p] = pending.splice(i, 1);
  await act(async () => p.reject(new Error("502")));
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) =>
    url.includes("/work-nodes/sessions")
      ? new Promise((resolve, reject) => pending.push({ url, resolve, reject }))
      : Promise.resolve({ repos: [], sessions: [], profiles: [], environments: [], machine_types: [] }),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("while a node is starting, a slow read is never overlapped by the next", async () => {
  render(<WorkNodesPage />);
  await answer("bucket=active", [node(1, "starting")]);

  await tick(10_000);
  expect(sessionReads()).toHaveLength(1);
  await tick(60_000);
  expect(sessionReads()).toHaveLength(1);
});

test("a failed background read keeps the nodes and says they may be out of date", async () => {
  render(<WorkNodesPage />);
  await answer("bucket=active", [node(1, "starting")]);
  await tick(10_000);
  await fail("bucket=active");

  expect(screen.getByText("machine-1")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toBeInTheDocument();
});

test("a new bucket hides the previous bucket's nodes and ignores its late answer", async () => {
  render(<WorkNodesPage />);
  await answer("bucket=active", [node(1, "running")]);

  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(screen.queryByText("machine-1")).not.toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Filter sessions" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "All" }));
  await answer("bucket=all", [node(3, "stopped")]);
  await answer("bucket=recent", [node(2, "running")]);

  expect(screen.getByText("machine-3")).toBeInTheDocument();
  expect(screen.queryByText("machine-2")).not.toBeInTheDocument();
});
