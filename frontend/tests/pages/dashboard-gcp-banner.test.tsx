/**
 * Tests for the GCP credentials banner on the Dashboard page.
 * Tests 24-25: banner shows when unconfigured, hides when configured.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import DashboardPage from "@/app/dashboard/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "admin@bioaf.org", role_name: "admin", sub: "1" }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: () => true,
    roleName: "admin",
    loading: false,
    permissions: new Set(),
  }),
  clearPermissionsCache: jest.fn(),
}));

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: jest.fn().mockResolvedValue({}),
  },
}));

// Mock all dashboard widgets to keep tests simple
jest.mock("@/components/dashboard/InfrastructureHealthWidget", () => ({
  InfrastructureHealthWidget: () => <div data-testid="infra-widget" />,
}));
jest.mock("@/components/dashboard/RunningJobsWidget", () => ({
  RunningJobsWidget: () => <div data-testid="running-jobs-widget" />,
}));
jest.mock("@/components/dashboard/QueueDepthWidget", () => ({
  QueueDepthWidget: () => <div data-testid="queue-depth-widget" />,
}));
jest.mock("@/components/dashboard/CostBudgetWidget", () => ({
  CostBudgetWidget: () => <div data-testid="cost-budget-widget" />,
}));
jest.mock("@/components/dashboard/IngestStatusWidget", () => ({
  IngestStatusWidget: () => <div data-testid="ingest-status-widget" />,
}));
jest.mock("@/components/dashboard/ActivityFeedWidget", () => ({
  ActivityFeedWidget: () => <div data-testid="activity-feed-widget" />,
}));

describe("Dashboard GCP banner", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockPush.mockReset();
  });

  // Test 24: Banner shows when gcp_credentials_configured is false
  it("shows GCP configuration banner when credentials are not configured", async () => {
    mockApiGet.mockResolvedValue({ gcp_credentials_configured: false });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByTestId("gcp-setup-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("gcp-setup-banner")).toHaveTextContent(/GCP/i);
  });

  // Test 25: Banner does not show when gcp_credentials_configured is true
  it("does not show GCP banner when credentials are configured", async () => {
    mockApiGet.mockResolvedValue({ gcp_credentials_configured: true });

    render(<DashboardPage />);

    // Give it time to load
    await waitFor(() => {
      expect(screen.getByTestId("dashboard")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("gcp-setup-banner")).not.toBeInTheDocument();
  });

  it("banner links to /settings/gcp", async () => {
    mockApiGet.mockResolvedValue({ gcp_credentials_configured: false });

    render(<DashboardPage />);

    await waitFor(() => {
      const banner = screen.getByTestId("gcp-setup-banner");
      const link = banner.querySelector("a");
      expect(link).toHaveAttribute("href", "/settings/gcp");
    });
  });

  it("banner is dismissible", async () => {
    mockApiGet.mockResolvedValue({ gcp_credentials_configured: false });

    render(<DashboardPage />);

    await waitFor(() => screen.getByTestId("gcp-setup-banner"));

    const dismissBtn = screen.getByTestId("gcp-banner-dismiss");
    fireEvent.click(dismissBtn);

    expect(screen.queryByTestId("gcp-setup-banner")).not.toBeInTheDocument();
  });
});
