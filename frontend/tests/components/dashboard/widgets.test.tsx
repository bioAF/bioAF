import { render, screen, waitFor } from "@testing-library/react";
import { InfrastructureHealthWidget } from "@/components/dashboard/InfrastructureHealthWidget";
import { RunningJobsWidget } from "@/components/dashboard/RunningJobsWidget";
import { QueueDepthWidget } from "@/components/dashboard/QueueDepthWidget";
import { CostBudgetWidget } from "@/components/dashboard/CostBudgetWidget";
import { IngestStatusWidget } from "@/components/dashboard/IngestStatusWidget";
import { ActivityFeedWidget } from "@/components/dashboard/ActivityFeedWidget";

// Mock next/navigation
jest.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: jest.fn() }),
}));

// Mock API
const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

describe("Dashboard Widgets", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
  });

  describe("InfrastructureHealthWidget", () => {
    it("renders with mock data", async () => {
      mockApiGet.mockResolvedValueOnce({
        components: [
          { name: "GKE", status: "healthy", enabled: true },
          { name: "GCS", status: "healthy", enabled: true },
        ],
      });
      render(<InfrastructureHealthWidget />);
      await waitFor(() => {
        expect(screen.getByText("GKE")).toBeInTheDocument();
        expect(screen.getByText("GCS")).toBeInTheDocument();
      });
    });

    it("shows loading skeleton", () => {
      mockApiGet.mockReturnValue(new Promise(() => {})); // never resolves
      render(<InfrastructureHealthWidget />);
      expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
    });

    it("handles empty state", async () => {
      mockApiGet.mockResolvedValueOnce({ components: [] });
      render(<InfrastructureHealthWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-empty")).toBeInTheDocument();
      });
    });

    it("handles error state", async () => {
      mockApiGet.mockRejectedValueOnce(new Error("fail"));
      render(<InfrastructureHealthWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-error")).toBeInTheDocument();
      });
    });
  });

  describe("RunningJobsWidget", () => {
    it("renders with mock data", async () => {
      mockApiGet
        .mockResolvedValueOnce({ total: 3 })
        .mockResolvedValueOnce({ total: 1 });
      render(<RunningJobsWidget />);
      await waitFor(() => {
        expect(screen.getByText("3")).toBeInTheDocument();
        expect(screen.getByText("1 pending")).toBeInTheDocument();
      });
    });

    it("shows loading skeleton", () => {
      mockApiGet.mockReturnValue(new Promise(() => {}));
      render(<RunningJobsWidget />);
      expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
    });
  });

  describe("QueueDepthWidget", () => {
    it("renders with mock data", async () => {
      mockApiGet.mockResolvedValueOnce({ runs: [1, 2], total: 5 });
      render(<QueueDepthWidget />);
      await waitFor(() => {
        expect(screen.getByText("5")).toBeInTheDocument();
        expect(screen.getByText("pending jobs")).toBeInTheDocument();
      });
    });

    it("shows loading skeleton", () => {
      mockApiGet.mockReturnValue(new Promise(() => {}));
      render(<QueueDepthWidget />);
      expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
    });
  });

  describe("CostBudgetWidget", () => {
    it("calls the correct endpoint", async () => {
      mockApiGet.mockResolvedValueOnce({
        current_month_spend: 500,
        monthly_budget: 1000,
        budget_remaining: 500,
        projected_month_end: 800,
      });
      render(<CostBudgetWidget />);
      await waitFor(() => {
        expect(mockApiGet).toHaveBeenCalledWith("/api/costs/summary");
      });
    });

    it("renders with mock data", async () => {
      mockApiGet.mockResolvedValueOnce({
        current_month_spend: 500,
        monthly_budget: 1000,
        budget_remaining: 500,
        projected_month_end: 800,
      });
      render(<CostBudgetWidget />);
      await waitFor(() => {
        expect(screen.getByText("$500")).toBeInTheDocument();
        expect(screen.getByText("$1,000")).toBeInTheDocument();
        expect(screen.getByText("50% of monthly budget")).toBeInTheDocument();
      });
    });

    it("shows empty state when no budget configured", async () => {
      mockApiGet.mockResolvedValueOnce({
        current_month_spend: 200,
        monthly_budget: null,
        budget_remaining: null,
        projected_month_end: null,
      });
      render(<CostBudgetWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-empty")).toBeInTheDocument();
      });
    });

    it("handles error state", async () => {
      mockApiGet.mockRejectedValueOnce(new Error("fail"));
      render(<CostBudgetWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-error")).toBeInTheDocument();
      });
    });
  });

  describe("IngestStatusWidget", () => {
    it("renders with mock data", async () => {
      mockApiGet
        .mockResolvedValueOnce({ total: 42 })
        .mockResolvedValueOnce({ files: [1, 2] })
        .mockResolvedValueOnce({ entities: [1] });
      render(<IngestStatusWidget />);
      await waitFor(() => {
        expect(screen.getByText("42")).toBeInTheDocument();
      });
    });

    it("shows loading skeleton", () => {
      mockApiGet.mockReturnValue(new Promise(() => {}));
      render(<IngestStatusWidget />);
      expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
    });
  });

  describe("ActivityFeedWidget", () => {
    it("renders with mock events", async () => {
      mockApiGet.mockResolvedValueOnce({
        events: [
          { id: 1, event_type: "test", summary: "Pipeline completed", created_at: new Date().toISOString() },
          { id: 2, event_type: "test", summary: "File uploaded", created_at: new Date().toISOString() },
        ],
      });
      render(<ActivityFeedWidget />);
      await waitFor(() => {
        expect(screen.getByText("Pipeline completed")).toBeInTheDocument();
        expect(screen.getByText("File uploaded")).toBeInTheDocument();
      });
    });

    it("shows empty state", async () => {
      mockApiGet.mockResolvedValueOnce({ events: [] });
      render(<ActivityFeedWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-empty")).toBeInTheDocument();
      });
    });

    it("expand button links to /activity", async () => {
      mockApiGet.mockResolvedValueOnce({ events: [] });
      render(<ActivityFeedWidget />);
      await waitFor(() => {
        const expandBtn = screen.getByTestId("activity-expand-button");
        expect(expandBtn).toHaveAttribute("href", "/activity");
      });
    });

    it("shows loading skeleton", () => {
      mockApiGet.mockReturnValue(new Promise(() => {}));
      render(<ActivityFeedWidget />);
      expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
    });

    it("handles error state", async () => {
      mockApiGet.mockRejectedValueOnce(new Error("fail"));
      render(<ActivityFeedWidget />);
      await waitFor(() => {
        expect(screen.getByTestId("widget-error")).toBeInTheDocument();
      });
    });
  });
});
