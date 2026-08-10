import { render, screen, waitFor } from "@testing-library/react";
import PipelineRunsPage from "@/app/(app)/pipelines/runs/page";

const mockPush = jest.fn();
const mockRouter = { push: mockPush };
jest.mock("next/navigation", () => ({
  usePathname: () => "/pipelines/runs",
  useRouter: () => mockRouter,
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "test@bioaf.org", role: "admin", sub: "1" }),
}));


const mockHas = jest.fn();
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    has: (flag: string) => mockHas(flag),
    capabilities: {},
    loading: false,
  }),
}));

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

const mockRun = {
  id: 42,
  pipeline_name: "bioAF System Test",
  experiment: { id: 1, name: "Test Experiment" },
  status: "running",
  failure_reason: null,
  review_verdict: null,
  progress: null,
  submitted_by: { id: 1, name: "Admin", email: "admin@test.com" },
  cost_estimate: 0.5,
  started_at: "2026-03-11T10:00:00Z",
  completed_at: null,
};

describe("Pipeline Runs List - cost gating", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ runs: [mockRun], total: 1 });
    mockHas.mockReset();
    mockHas.mockReturnValue(true);
  });

  it("shows the estimated cost column when the backend has cost_estimation", async () => {
    render(<PipelineRunsPage />);
    await screen.findByText("bioAF System Test");
    expect(screen.getByText("Est. $/hr")).toBeInTheDocument();
    expect(screen.getByText("$0.50/hr")).toBeInTheDocument();
  });

  it("hides the estimated cost column when the backend lacks cost_estimation", async () => {
    mockHas.mockImplementation((flag: string) => flag !== "cost_estimation");
    render(<PipelineRunsPage />);
    await screen.findByText("bioAF System Test");
    expect(screen.queryByText("Est. $/hr")).not.toBeInTheDocument();
    expect(screen.queryByText("$0.50/hr")).not.toBeInTheDocument();
  });
});
