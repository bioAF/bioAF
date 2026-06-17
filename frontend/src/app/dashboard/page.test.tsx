import { render, screen, waitFor } from "@testing-library/react";
import DashboardPage from "./page";

let mockCloudProvider = "gcp";
jest.mock("@/hooks/useStackOptions", () => ({
  useStackOptions: () => ({
    cloudProvider: mockCloudProvider,
    options: [],
    kubernetesOption: null,
    loading: false,
  }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ roleName: "admin", canAccess: () => true, loading: false }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: jest.fn(() => true),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
}));

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <div /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <div /> }));
jest.mock("@/components/dashboard/DashboardContent", () => ({
  DashboardContent: () => <div data-testid="dashboard-content" />,
}));

const mockPush = jest.fn();
const stableRouter = { push: mockPush };
jest.mock("next/navigation", () => ({
  useRouter: () => stableRouter,
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockCloudProvider = "gcp";
  mockGet.mockReset();
});

describe("Dashboard cloud onboarding banner", () => {
  it("shows the GCP banner pointing at /settings/gcp on a GCP install", async () => {
    mockGet.mockResolvedValue({ gcp_credentials_configured: false });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByTestId("cloud-setup-banner")).toBeInTheDocument());
    expect(mockGet).toHaveBeenCalledWith("/api/v1/settings/gcp");
    expect(screen.getByText(/GCP not configured/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Configure GCP settings/ });
    expect(link).toHaveAttribute("href", "/settings/gcp");
  });

  it("shows the AWS banner pointing at /settings/aws on an AWS install", async () => {
    mockCloudProvider = "aws";
    mockGet.mockResolvedValue({ aws_credentials_configured: false });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByTestId("cloud-setup-banner")).toBeInTheDocument());
    expect(mockGet).toHaveBeenCalledWith("/api/v1/settings/aws");
    expect(screen.getByText(/AWS not configured/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Configure AWS settings/ });
    expect(link).toHaveAttribute("href", "/settings/aws");
  });

  it("hides the banner when the cloud is configured", async () => {
    mockGet.mockResolvedValue({ gcp_credentials_configured: true });
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByTestId("dashboard-content")).toBeInTheDocument());
    expect(screen.queryByTestId("cloud-setup-banner")).not.toBeInTheDocument();
  });
});
