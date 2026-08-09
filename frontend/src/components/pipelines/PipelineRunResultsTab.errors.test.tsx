/**
 * The Results tab must not report a failed read as a finding about the run.
 *
 * Two claims were reached from a rejected promise:
 *
 *   "No QC dashboard yet for this run. Dashboards are generated automatically
 *    when the run completes."   <- from any rejection, 500 included
 *   "No plots indexed for this run yet."
 *
 * Both were proven on the deployed demo against run #28 by failing
 * /api/qc-dashboards/by-run/* and /api/plots. A completed run with QC generated
 * rendered as a run whose QC had never been generated, which is the same defect
 * class as "Experiment not found" for a 500.
 *
 * A 404 is different and must keep its wording: there genuinely is no dashboard.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import { PipelineRunResultsTab } from "./PipelineRunResultsTab";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
  fileContentUrl: jest.fn(async () => "blob:x"),
  plotThumbnailContentUrl: jest.fn(async () => "blob:y"),
}));
jest.mock("@/components/qc/QCAiReviewSection", () => ({
  QCAiReviewSection: () => null,
}));
jest.mock("@/components/qc/GenericQCDashboard", () => ({
  GenericQCDashboard: () => <div>dashboard body</div>,
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

/** Route each URL this component fetches to its own outcome. */
function routeGets(handlers: Record<string, () => unknown>) {
  mockGet.mockImplementation((url: string) => {
    for (const [frag, fn] of Object.entries(handlers)) {
      if (url.includes(frag)) return Promise.resolve(fn());
    }
    return Promise.reject(new Error(`unrouted ${url}`));
  });
}

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a 500 on the QC read is not 'no QC dashboard yet'", async () => {
  mockGet.mockImplementation((url: string) =>
    url.includes("qc-dashboards")
      ? Promise.reject(new Error("boom"))
      : Promise.resolve({ plots: [] }),
  );
  render(<PipelineRunResultsTab pipelineRunId={28} />);

  await waitFor(() =>
    expect(screen.getByTestId("qc-dashboard-load-failed")).toBeInTheDocument(),
  );
  expect(screen.queryByText(/No QC dashboard yet for this run/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  expect(errorLog).toHaveBeenCalled();
});

test("a run that genuinely has no QC dashboard still says so", async () => {
  const { ApiError } = jest.requireActual("@/lib/api");
  mockGet.mockImplementation((url: string) =>
    url.includes("qc-dashboards")
      ? Promise.reject(new ApiError(404, "Not found"))
      : Promise.resolve({ plots: [] }),
  );
  render(<PipelineRunResultsTab pipelineRunId={28} />);

  await waitFor(() =>
    expect(screen.getByText(/No QC dashboard yet for this run/i)).toBeInTheDocument(),
  );
  expect(screen.queryByTestId("qc-dashboard-load-failed")).not.toBeInTheDocument();
});

test("a 500 on the plots read is not 'no plots indexed'", async () => {
  mockGet.mockImplementation((url: string) =>
    url.includes("/api/plots")
      ? Promise.reject(new Error("boom"))
      : Promise.resolve({ plots: [], metrics: [] }),
  );
  render(<PipelineRunResultsTab pipelineRunId={28} />);

  await waitFor(() =>
    expect(screen.getByTestId("run-plots-load-failed")).toBeInTheDocument(),
  );
  expect(screen.queryByText(/No plots indexed for this run yet/i)).not.toBeInTheDocument();
});

test("a run that really has no plots still says so", async () => {
  routeGets({
    "qc-dashboards": () => ({ plots: [], metrics: [] }),
    "/api/plots": () => ({ plots: [] }),
  });
  render(<PipelineRunResultsTab pipelineRunId={28} />);

  await waitFor(() =>
    expect(screen.getByText(/No plots indexed for this run yet/i)).toBeInTheDocument(),
  );
  expect(screen.queryByTestId("run-plots-load-failed")).not.toBeInTheDocument();
});
