import { render, waitFor } from "@testing-library/react";

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

function mockApiResponses(runStatus: string) {
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
