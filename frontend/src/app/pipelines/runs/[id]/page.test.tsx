import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

const mockRouter = { push: jest.fn() };
const mockParams = { id: "1" };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => mockParams,
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "fake-token",
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));
jest.mock("@/components/shared/LoadingSpinner", () => ({ LoadingSpinner: () => null }));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/experiments/ReviewPanel", () => ({ ReviewPanel: () => null }));
jest.mock("@/components/references/ReferenceStatusBadge", () => ({ ReferenceStatusBadge: () => null }));
jest.mock("@/components/shared/ProvenanceExportMenu", () => ({ ProvenanceExportMenu: () => null }));
jest.mock("@/components/qc/GenericQCDashboard", () => ({
  GenericQCDashboard: ({ dashboard }: { dashboard: { pipeline_run_id: number } }) => (
    <div data-testid="generic-qc-dashboard">qc-run-{dashboard.pipeline_run_id}</div>
  ),
}));
jest.mock("@/components/shared/PlotModal", () => ({ PlotModal: () => null }));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => "blob:fake-file",
  usePlotThumbnailContentUrl: () => "blob:fake-thumb",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

import PipelineRunDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

function mockApiResponses(runStatus: string, extraRunFields: Record<string, unknown> = {}) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/pipeline-runs/1") {
      return Promise.resolve({
        id: 1,
        status: runStatus,
        k8s_job_name: "bioaf-pipeline-1",
        pipeline_name: "nf-core/scrnaseq",
        custom_pipeline_version_id: null,
        organization_id: 1,
        processes: [],
        ...extraRunFields,
      });
    }
    if (url === "/api/pipeline-runs/1/references") {
      return Promise.resolve([]);
    }
    if (url === "/api/pipeline-runs/1/logs") {
      return Promise.resolve({ stdout: "log line", stderr: "" });
    }
    return Promise.resolve({});
  });
}

beforeEach(() => {
  mockGet.mockReset();
});

function logsCalls(): number {
  return mockGet.mock.calls.filter(([url]) => url === "/api/pipeline-runs/1/logs").length;
}

describe("PipelineRunDetailPage logs auto-refresh", () => {
  test("polls logs endpoint on an interval while run is active", async () => {
    mockApiResponses("running");
    render(<PipelineRunDetailPage />);

    // Let initial async cycle settle (loadRun, state propagation, first loadLogs).
    await waitFor(() => expect(logsCalls()).toBeGreaterThanOrEqual(1));
    const initial = logsCalls();

    // Real-timer wait past the 5s polling interval. Slow but reliable; fake
    // timers don't compose well with setInterval registered before the
    // useFakeTimers switch.
    await new Promise((r) => setTimeout(r, 5500));

    await waitFor(() => expect(logsCalls()).toBeGreaterThan(initial));
  }, 15000);

  test("polled refreshes do not flash the loading spinner", async () => {
    // The loading spinner toggles `logsLoading`, which unmounts the <pre>
    // and re-mounts it -- resetting the user's scroll position. Polled
    // refreshes must suppress the spinner so the <pre> stays mounted and
    // scroll position survives.
    mockApiResponses("running");
    render(<PipelineRunDetailPage />);

    // Wait for the initial logs call so the spinner has had a chance to
    // appear and disappear.
    await waitFor(() => expect(logsCalls()).toBeGreaterThanOrEqual(1));
    await waitFor(() => expect(screen.queryByText("Loading logs...")).toBeNull());

    // Trigger a poll.
    await new Promise((r) => setTimeout(r, 5500));
    await waitFor(() => expect(logsCalls()).toBeGreaterThan(1));

    // After the poll, the spinner must still be hidden -- otherwise the
    // <pre> got unmounted and the user's scroll is gone.
    expect(screen.queryByText("Loading logs...")).toBeNull();
  }, 15000);

  test("does not poll logs once the run reaches a terminal state", async () => {
    mockApiResponses("completed");
    render(<PipelineRunDetailPage />);

    await waitFor(() => expect(logsCalls()).toBeGreaterThanOrEqual(1));
    const initial = logsCalls();

    await new Promise((r) => setTimeout(r, 5500));

    // No interval should be running for a completed run.
    expect(logsCalls()).toBe(initial);
  }, 15000);
});

describe("PipelineRunDetailPage Results tab", () => {
  function mockResultsResponses(extraRunFields: Record<string, unknown> = {}) {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/pipeline-runs/1") {
        return Promise.resolve({
          id: 1,
          status: "completed",
          pipeline_name: "nf-core/scrnaseq",
          custom_pipeline_version_id: null,
          organization_id: 1,
          processes: [],
          ...extraRunFields,
        });
      }
      if (url === "/api/pipeline-runs/1/references") return Promise.resolve([]);
      if (url === "/api/qc-dashboards/by-run/1") {
        return Promise.resolve({
          id: 7,
          pipeline_run_id: 1,
          experiment_id: 2,
          qc_config: { sections: [], chart_sections: [] },
          raw_metrics: {},
          metrics: { quality_rating: "good" },
          summary_text: "Looks good",
          plots: [],
          status: "ready",
          generated_at: "2026-05-14T00:00:00Z",
          created_at: "2026-05-14T00:00:00Z",
        });
      }
      if (url.startsWith("/api/plots")) {
        return Promise.resolve({
          plots: [
            {
              id: 11,
              title: "UMAP",
              file: { id: 99, file_type: "png", storage_deleted: false },
              experiment_id: 2,
              experiment_name: "Exp A",
              project_name: "Proj A",
              pipeline_run_id: 1,
              pipeline_run_name: "nf-core/scrnaseq #1",
              notebook_session_id: null,
              notebook_session_type: null,
              source_type: "pipeline",
              tags: [],
              thumbnail_url: null,
              indexed_at: "2026-05-14T00:00:00Z",
            },
          ],
          total: 1,
          page: 1,
          page_size: 24,
        });
      }
      return Promise.resolve({});
    });
  }

  test("renders a Results tab before the Review tab", async () => {
    mockResultsResponses();
    render(<PipelineRunDetailPage />);
    const results = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: "Results" });
      if (!btn) throw new Error("Results tab not rendered yet");
      return btn;
    });
    const review = screen.getByRole("button", { name: "Review" });
    // Results must come before Review in DOM order.
    expect(results.compareDocumentPosition(review) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("clicking Results loads the QC dashboard inline and shows Plot Archive entries", async () => {
    mockResultsResponses();
    render(<PipelineRunDetailPage />);
    const results = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: "Results" });
      if (!btn) throw new Error("Results tab not rendered yet");
      return btn;
    });
    await act(async () => {
      fireEvent.click(results);
    });
    // QC dashboard embedded inline.
    await waitFor(() => expect(screen.queryByTestId("generic-qc-dashboard")).toBeTruthy());
    expect(screen.getByText("qc-run-1")).toBeTruthy();
    // Plot archive entry for this run.
    expect(screen.getByText("UMAP")).toBeTruthy();
    // Deep-link to full pages.
    expect(screen.getByRole("link", { name: /open in qc dashboards/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /open in plot archive/i })).toBeTruthy();
  });

  test("shows static QC plots from dashboard.plots below the interactive dashboard", async () => {
    // The standalone Results > QC Dashboards page renders dashboard.plots
    // (the static QCPlot items the extractor saved with the dashboard)
    // beneath GenericQCDashboard. The Results tab must match that, so
    // reviewers see the same set of figures inline as they would on the
    // full QC dashboard page.
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/pipeline-runs/1") {
        return Promise.resolve({
          id: 1,
          status: "completed",
          pipeline_name: "nf-core/scrnaseq",
          custom_pipeline_version_id: null,
          organization_id: 1,
          processes: [],
        });
      }
      if (url === "/api/pipeline-runs/1/references") return Promise.resolve([]);
      if (url === "/api/qc-dashboards/by-run/1") {
        return Promise.resolve({
          id: 7,
          pipeline_run_id: 1,
          experiment_id: 2,
          qc_config: { sections: [], chart_sections: [] },
          raw_metrics: {},
          metrics: { quality_rating: "good" },
          summary_text: "Looks good",
          plots: [
            { plot_type: "umap", title: "UMAP clusters", file_id: 101 },
            { plot_type: "violin", title: "Mito % distribution", file_id: 102 },
          ],
          status: "ready",
          generated_at: "2026-05-14T00:00:00Z",
          created_at: "2026-05-14T00:00:00Z",
        });
      }
      if (url.startsWith("/api/plots")) {
        return Promise.resolve({ plots: [], total: 0, page: 1, page_size: 24 });
      }
      return Promise.resolve({});
    });

    render(<PipelineRunDetailPage />);
    const results = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: "Results" });
      if (!btn) throw new Error("Results tab not rendered yet");
      return btn;
    });
    await act(async () => {
      fireEvent.click(results);
    });
    await waitFor(() =>
      expect(screen.queryByText("UMAP clusters")).toBeTruthy(),
    );
    expect(screen.getByText("Mito % distribution")).toBeTruthy();
  });

  test("Results tab handles a run with no QC dashboard yet", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/pipeline-runs/1") {
        return Promise.resolve({
          id: 1,
          status: "completed",
          pipeline_name: "nf-core/scrnaseq",
          custom_pipeline_version_id: null,
          organization_id: 1,
          processes: [],
        });
      }
      if (url === "/api/pipeline-runs/1/references") return Promise.resolve([]);
      if (url === "/api/qc-dashboards/by-run/1") {
        return Promise.reject(new Error("404"));
      }
      if (url.startsWith("/api/plots")) {
        return Promise.resolve({ plots: [], total: 0, page: 1, page_size: 24 });
      }
      return Promise.resolve({});
    });

    render(<PipelineRunDetailPage />);
    const results = await waitFor(() => {
      const btn = screen.queryByRole("button", { name: "Results" });
      if (!btn) throw new Error("Results tab not rendered yet");
      return btn;
    });
    await act(async () => {
      fireEvent.click(results);
    });
    await waitFor(() =>
      expect(screen.queryByText(/no qc dashboard yet/i)).toBeTruthy(),
    );
    expect(screen.queryByText(/no plots/i)).toBeTruthy();
  });
});

describe("PipelineRunDetailPage step retries surface", () => {
  test("does not render a retries pill on a clean run", async () => {
    mockApiResponses("completed", {
      progress: {
        total_processes: 17,
        completed: 17,
        running: 0,
        failed: 0,
        cached: 0,
        percent_complete: 100,
      },
    });
    render(<PipelineRunDetailPage />);
    await waitFor(() => expect(screen.queryByText("Started")).toBeTruthy());
    expect(screen.queryByTestId("retries-pill")).toBeNull();
    expect(screen.queryByText("Step retries")).toBeNull();
  });

  test("renders the retries pill with count and opens a modal listing retried steps", async () => {
    mockApiResponses("completed", {
      progress: {
        total_processes: 17,
        completed: 17,
        running: 0,
        failed: 0,
        cached: 0,
        percent_complete: 100,
        retries: [
          { name: "NFCORE_SCRNASEQ:SCRNASEQ:STARSOLO:STAR_ALIGN", attempts: 2 },
          { name: "NFCORE_SCRNASEQ:SCRNASEQ:STARSOLO:STAR_GENOMEGENERATE", attempts: 2 },
          { name: "NFCORE_SCRNASEQ:SCRNASEQ:MTX_CONVERSION:MTX_TO_H5AD", attempts: 2 },
        ],
      },
    });
    render(<PipelineRunDetailPage />);

    const pill = await waitFor(() => {
      const el = screen.queryByTestId("retries-pill");
      if (!el) throw new Error("retries pill not rendered yet");
      return el;
    });
    expect(pill.textContent).toContain("3");
    expect(screen.queryByTestId("retries-modal")).toBeNull();

    await act(async () => {
      fireEvent.click(pill);
    });

    await waitFor(() => expect(screen.queryByTestId("retries-modal")).toBeTruthy());
    expect(screen.queryByText("NFCORE_SCRNASEQ:SCRNASEQ:STARSOLO:STAR_ALIGN")).toBeTruthy();
    expect(screen.queryByText("NFCORE_SCRNASEQ:SCRNASEQ:STARSOLO:STAR_GENOMEGENERATE")).toBeTruthy();
    expect(screen.queryByText("NFCORE_SCRNASEQ:SCRNASEQ:MTX_CONVERSION:MTX_TO_H5AD")).toBeTruthy();
  });
});
