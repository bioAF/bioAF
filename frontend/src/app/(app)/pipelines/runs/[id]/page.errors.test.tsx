/**
 * A failed load must not claim the run does not exist.
 *
 * This page swallowed the detail load and then rendered "Run not found", which
 * is worse than rendering emptiness: it is a false statement about the user's
 * data. A 500, an expired session and a deleted run all read identically.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import PipelineRunDetailPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => ({ id: "42" }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
// ApiError must be the real class: the page distinguishes a 404 from an outage
// with `instanceof`, and a bare object mock would make that check throw.
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed load reports the failure instead of 'Run not found'", async () => {
  mockGet.mockRejectedValue(new Error("Backend unavailable"));
  render(<PipelineRunDetailPage />);

  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(
    /this run could not be loaded/i,
  );
  expect(screen.getByTestId("error-message")).toHaveTextContent(/logs/i);
  // The technical text belongs in the logs, never on screen.
  expect(screen.getByTestId("error-message")).not.toHaveTextContent(/backend unavailable/i);
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  expect(screen.queryByText(/run not found/i)).not.toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("a run that really is missing still says so", async () => {
  // A 404 is a genuine "not found", and must keep reading that way.
  const { ApiError } = jest.requireActual("@/lib/api");
  mockGet.mockRejectedValue(new ApiError(404, "Not found"));
  render(<PipelineRunDetailPage />);

  await waitFor(() => expect(screen.getByText(/run not found/i)).toBeInTheDocument());
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});

/**
 * `loadReport` used `fetch` with no `res.ok` check and piped `res.text()` straight into
 * `<iframe srcDoc>` under the heading "Nextflow Report". `fetch` does not reject on
 * 4xx/5xx, so a 404 body, a 500 stack trace or an nginx error page was displayed to the
 * user as the pipeline's own report.
 */
describe("the Report tab", () => {
  const COMPLETED_RUN = {
    id: 42,
    status: "completed",
    pipeline_name: "rnaseq",
    pipeline_key: "rnaseq",
    experiment: null,
    submitted_by: null,
    created_at: "2026-06-01T00:00:00Z",
    started_at: "2026-06-01T00:00:00Z",
    completed_at: "2026-06-01T01:00:00Z",
    error_message: null,
    progress: null,
    parameters: {},
    compute_job_ref: null,
    processes: [],
  };

  afterEach(() => {
    (global.fetch as jest.Mock | undefined)?.mockRestore?.();
  });

  function mountReport(response: Partial<Response>) {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/api/pipeline-runs/42")) return Promise.resolve(COMPLETED_RUN);
      return Promise.resolve({});
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => "<html>report</html>",
      ...response,
    }) as unknown as typeof fetch;
  }

  test("a 500 body is not rendered as the run's report", async () => {
    const { toastMock } = jest.requireMock("@/components/shared/Toast");
    mountReport({
      ok: false,
      status: 500,
      text: async () => "<html><body>Internal Server Error</body></html>",
    });

    render(<PipelineRunDetailPage />);
    const reportTab = await screen.findByRole("button", { name: /^report$/i });
    reportTab.click();

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringMatching(/run report could not be loaded/i)
      )
    );
    expect(document.querySelector("iframe")).toBeNull();
  });

  test("a real report still renders", async () => {
    mountReport({});

    render(<PipelineRunDetailPage />);
    const reportTab = await screen.findByRole("button", { name: /^report$/i });
    reportTab.click();

    await waitFor(() => expect(document.querySelector("iframe")).not.toBeNull());
    expect(document.querySelector("iframe")?.getAttribute("srcdoc")).toContain("report");
  });
});

/**
 * Item 7. A run that dies BEFORE Nextflow writes a trace showed no reason at all.
 *
 * The OOM banner, the preemption banner and the plain `run.error_message` were all
 * nested inside `{run.progress && (...)}`, so they render only when there is a progress
 * payload. `progress_json` is exactly what is missing in this failure class:
 * `_populate_progress` in pipeline_monitor_service.py returns early on an adapter
 * exception or an empty process list, and unlike the `completed` branch, the `failed`
 * branch has no fallback payload. Image pull failure, scheduling failure, node
 * provisioning failure and a bad Nextflow config all land there.
 *
 * The backend writes a full explanatory paragraph into `error_message` for exactly
 * these cases. The UI threw it away and rendered a red `failed` pill, a duration, seven
 * tabs and nothing else. The recovery panels were unreachable in the one case that
 * needs them most.
 */
describe("a run that failed before any progress was recorded", () => {
  const FAILED_NO_PROGRESS = {
    id: 42,
    status: "failed",
    pipeline_name: "rnaseq",
    pipeline_key: "rnaseq",
    experiment: null,
    submitted_by: null,
    created_at: "2026-06-01T00:00:00Z",
    started_at: "2026-06-01T00:00:00Z",
    completed_at: "2026-06-01T00:04:00Z",
    error_message: "Pipeline failed: the container image could not be pulled.",
    failure_reason: "task_error",
    progress: null, // <- the whole point
    parameters: {},
    compute_job_ref: null,
    processes: [],
  };

  function mountRun(run: Record<string, unknown>) {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/api/pipeline-runs/42")) return Promise.resolve(run);
      return Promise.resolve({});
    });
  }

  test("still shows the reason the backend recorded", async () => {
    mountRun(FAILED_NO_PROGRESS);

    render(<PipelineRunDetailPage />);

    expect(await screen.findByTestId("run-failure-reason")).toHaveTextContent(
      /container image could not be pulled/i
    );
  });

  test("still offers the OOM recovery panel", async () => {
    mountRun({
      ...FAILED_NO_PROGRESS,
      failure_reason: "oom",
      error_message: "Pipeline failed: out of memory.",
    });

    render(<PipelineRunDetailPage />);

    expect(await screen.findByText(/ran out of memory/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /update node size/i })).toBeInTheDocument();
  });

  test("still offers the preemption recovery panel", async () => {
    mountRun({ ...FAILED_NO_PROGRESS, failure_reason: "preemption_exhausted" });

    render(<PipelineRunDetailPage />);

    expect(await screen.findByText(/spot instance reclamation/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-run pipeline/i })).toBeInTheDocument();
  });

  test("a run with progress still shows its reason exactly once", async () => {
    // The banners must not be rendered twice once they are lifted out of the progress
    // card: the progress card is still there for runs that have a payload.
    mountRun({
      ...FAILED_NO_PROGRESS,
      progress: {
        total_processes: 4,
        completed: 2,
        running: 0,
        failed: 1,
        cached: 1,
        percent_complete: 50,
        retries: [],
      },
    });

    render(<PipelineRunDetailPage />);

    await waitFor(() => expect(screen.getByTestId("run-failure-reason")).toBeInTheDocument());
    expect(screen.getAllByTestId("run-failure-reason")).toHaveLength(1);
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  test("a healthy completed run shows no failure block", async () => {
    mountRun({ ...FAILED_NO_PROGRESS, status: "completed", error_message: null, failure_reason: null });

    render(<PipelineRunDetailPage />);

    await waitFor(() => expect(screen.queryByTestId("error-message")).not.toBeInTheDocument());
    expect(screen.queryByTestId("run-failure-reason")).not.toBeInTheDocument();
    expect(screen.queryByText(/ran out of memory/i)).not.toBeInTheDocument();
  });
});
