/**
 * The runs list is the fleet view: the one screen that says what is happening across
 * every pipeline right now. Three things stopped it doing that, all in scope for item 7
 * as defects on the same screen as the missing failure reason.
 */
import { render, screen, waitFor, act } from "@/testing/renderWithProviders";
import PipelineRunsPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

/** Started an hour ago and still going. */
const RUNNING_RUN = {
  id: 1,
  status: "running",
  pipeline_name: "rnaseq",
  experiment: null,
  submitted_by: null,
  started_at: new Date(Date.parse("2026-06-01T10:00:00Z")).toISOString(),
  completed_at: null,
  progress: null,
  review_verdict: null,
  failure_reason: null,
  cost_estimate: null,
};

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
  jest.useFakeTimers({ doNotFake: ["performance"] });
  jest.setSystemTime(new Date("2026-06-01T11:00:00Z"));
});
afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
  errorLog.mockRestore();
});

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

// Every sibling surface polls: the run detail every 10s, logs every 5s, environments
// every 5s, cellxgene every 5s. The fleet view did not, so a run that finished, failed
// or was launched from anywhere else never appeared until the user reloaded the page.
test("refreshes on an interval, like every other live surface", async () => {
  mockGet.mockResolvedValue({ runs: [RUNNING_RUN], total: 1, page: 1, page_size: 25 });

  render(<PipelineRunsPage />);
  await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));

  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  await settle();

  expect(mockGet.mock.calls.length).toBeGreaterThan(1);
});

// `formatDuration` read Date.now() once, at render. An in-flight 8-hour run therefore
// read "3m" until the page was reloaded, on the only elapsed-time signal in the app.
test("the duration of an in-flight run advances without a reload", async () => {
  mockGet.mockResolvedValue({ runs: [RUNNING_RUN], total: 1, page: 1, page_size: 25 });

  render(<PipelineRunsPage />);
  await waitFor(() => expect(screen.getByText("1h 0m")).toBeInTheDocument());

  await act(async () => {
    jest.advanceTimersByTime(60_000);
  });
  await settle();

  await waitFor(() => expect(screen.getByText("1h 1m")).toBeInTheDocument());
});

test("a finished run's duration is fixed, and does not tick", async () => {
  mockGet.mockResolvedValue({
    runs: [{ ...RUNNING_RUN, status: "completed", completed_at: "2026-06-01T10:30:00Z" }],
    total: 1,
    page: 1,
    page_size: 25,
  });

  render(<PipelineRunsPage />);
  await waitFor(() => expect(screen.getByText("30m")).toBeInTheDocument());

  await act(async () => {
    jest.advanceTimersByTime(120_000);
  });
  await settle();

  expect(screen.getByText("30m")).toBeInTheDocument();
});

// `setLoadError` was set without clearing `runs`, so the red error row rendered BELOW a
// full table of stale rows, each of which still looked current. And the message
// interpolated the raw server string.
describe("when the list cannot be loaded", () => {
  test("says so without leaking the raw server string", async () => {
    mockGet.mockRejectedValue(new Error("Injected 500: relation does not exist"));

    render(<PipelineRunsPage />);

    await waitFor(() => expect(screen.getByTestId("runs-load-error")).toBeInTheDocument());
    expect(screen.getByTestId("runs-load-error")).not.toHaveTextContent(/relation does not exist/i);
    expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  });

  test("rows that are still on screen are labelled as possibly out of date", async () => {
    mockGet.mockResolvedValueOnce({ runs: [RUNNING_RUN], total: 1, page: 1, page_size: 25 });
    render(<PipelineRunsPage />);
    await waitFor(() => expect(screen.getByText("#1")).toBeInTheDocument());

    // A later refresh fails.
    mockGet.mockRejectedValue(new Error("boom"));
    await act(async () => {
      jest.advanceTimersByTime(10_000);
    });
    await settle();

    // The rows are still useful, so they stay, but they are no longer presented as
    // current: the banner sits above them rather than as a row underneath.
    await waitFor(() => expect(screen.getByTestId("runs-load-error")).toBeInTheDocument());
    expect(screen.getByTestId("runs-load-error")).toHaveTextContent(/out of date/i);
    expect(screen.getByText("#1")).toBeInTheDocument();
  });

  test("recovers on the next successful refresh", async () => {
    mockGet.mockRejectedValueOnce(new Error("boom"));
    render(<PipelineRunsPage />);
    await waitFor(() => expect(screen.getByTestId("runs-load-error")).toBeInTheDocument());

    mockGet.mockResolvedValue({ runs: [RUNNING_RUN], total: 1, page: 1, page_size: 25 });
    await act(async () => {
      jest.advanceTimersByTime(10_000);
    });
    await settle();

    await waitFor(() => expect(screen.queryByTestId("runs-load-error")).not.toBeInTheDocument());
    expect(screen.getByText("#1")).toBeInTheDocument();
  });
});
