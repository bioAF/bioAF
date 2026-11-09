/**
 * The runs list polled with setInterval: a slow read was overlapped by the next one, and a read
 * for the previous filter or page could land after the current one. Reads are now
 * serialized and keyed by page, size, filter, and sort; the filter stays on screen while a new
 * selection loads.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";
import PipelineRunsPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

type Pending = { url: string; resolve: (v: unknown) => void };
let pending: Pending[];

function run(id: number, status = "running") {
  return {
    id, status, pipeline_name: "rnaseq", experiment: null, submitted_by: null,
    started_at: "2026-06-01T10:00:00Z", completed_at: null, progress: null,
    review_verdict: null, failure_reason: null, cost_estimate: null,
  };
}

async function answer(fragment: string, runs: unknown[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending request matching ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve({ runs, total: runs.length, page: 1, page_size: 25 }));
}

beforeEach(() => {
  jest.useFakeTimers({ doNotFake: ["performance"] });
  jest.setSystemTime(new Date("2026-06-01T11:00:00Z"));
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => new Promise((resolve) => pending.push({ url, resolve })));
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a slow refresh is never overlapped by the next one", async () => {
  render(<PipelineRunsPage />);
  await answer("/api/pipeline-runs?", [run(1)]);

  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);

  await act(async () => {
    jest.advanceTimersByTime(60_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("the previous filter's late answer does not replace the current filter's", async () => {
  render(<PipelineRunsPage />);
  await answer("/api/pipeline-runs?", [run(1)]);

  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "failed" } });
  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "completed" } });
  await answer("status=completed", [run(3, "completed")]);
  await answer("status=failed", [run(2, "failed")]);

  expect(screen.getByText("#3")).toBeInTheDocument();
  expect(screen.queryByText("#2")).not.toBeInTheDocument();
  expect(screen.queryByText("#1")).not.toBeInTheDocument();
});

test("the status filter stays on screen while a new selection loads", async () => {
  render(<PipelineRunsPage />);
  await answer("/api/pipeline-runs?", [run(1)]);
  const filter = screen.getByLabelText("Filter by status");

  fireEvent.change(filter, { target: { value: "failed" } });

  expect(screen.getByLabelText("Filter by status")).toBe(filter);
  expect(screen.queryByText("#1")).not.toBeInTheDocument();
});

test("a filter change aborts the previous selection's read", async () => {
  render(<PipelineRunsPage />);
  await answer("/api/pipeline-runs?", [run(1)]);
  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "failed" } });
  const failedSignal = mockGet.mock.calls.at(-1)?.[1]?.signal as AbortSignal;
  expect(failedSignal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "completed" } });
  expect(failedSignal.aborted).toBe(true);
});

test("a failed read shows the error with Retry, and recovers on the next answer", async () => {
  render(<PipelineRunsPage />);
  await answer("/api/pipeline-runs?", [run(1)]);
  mockGet.mockImplementationOnce(() => Promise.reject(new Error("502")));
  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  expect(screen.getByTestId("runs-load-error")).toBeInTheDocument();

  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  await answer("/api/pipeline-runs?", [run(1)]);
  expect(screen.queryByTestId("runs-load-error")).not.toBeInTheDocument();
});
