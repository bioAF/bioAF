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
