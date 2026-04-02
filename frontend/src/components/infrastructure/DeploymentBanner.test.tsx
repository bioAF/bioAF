import { render, screen, waitFor, act } from "@testing-library/react";
import { DeploymentBanner } from "./DeploymentBanner";

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
  isAuthenticated: () => true,
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe("DeploymentBanner", () => {
  test("renders nothing when no active deployment", async () => {
    mockGet.mockResolvedValue({
      terraform_initialized: true,
      gcp_credentials_configured: true,
      active_run_id: null,
      active_run_status: null,
      last_completed_module: null,
    });

    const { container } = render(<DeploymentBanner />);

    await waitFor(() => {
      expect(container.querySelector("[data-testid='deployment-banner']")).not.toBeInTheDocument();
    });
  });

  test("shows banner when deployment is in progress", async () => {
    mockGet.mockResolvedValue({
      terraform_initialized: true,
      gcp_credentials_configured: true,
      active_run_id: 42,
      active_run_status: "applying",
      last_completed_module: null,
    });

    render(<DeploymentBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("deployment-banner")).toBeInTheDocument();
      expect(screen.getByText(/Infrastructure is deploying/)).toBeInTheDocument();
    });
  });

  test("banner links to infrastructure page", async () => {
    mockGet.mockResolvedValue({
      terraform_initialized: true,
      gcp_credentials_configured: true,
      active_run_id: 42,
      active_run_status: "applying",
      last_completed_module: null,
    });

    render(<DeploymentBanner />);

    await waitFor(() => {
      const link = screen.getByText("View progress");
      expect(link).toHaveAttribute("href", "/infrastructure/components");
    });
  });

  test("shows storage toast when storage deploy completes", async () => {
    // First poll: active deployment
    mockGet.mockResolvedValueOnce({
      terraform_initialized: true,
      gcp_credentials_configured: true,
      active_run_id: 42,
      active_run_status: "applying",
      last_completed_module: null,
    });

    render(<DeploymentBanner />);

    await waitFor(() => {
      expect(screen.getByTestId("deployment-banner")).toBeInTheDocument();
    });

    // Second poll: storage complete, compute not started yet
    mockGet.mockResolvedValueOnce({
      terraform_initialized: true,
      gcp_credentials_configured: true,
      active_run_id: null,
      active_run_status: null,
      last_completed_module: "storage",
    });

    await act(async () => {
      jest.advanceTimersByTime(10000);
    });

    await waitFor(() => {
      expect(screen.getByTestId("deployment-toast")).toBeInTheDocument();
      expect(screen.getByText(/Storage deployed successfully/)).toBeInTheDocument();
    });
  });
});
