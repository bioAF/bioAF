import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import { DeployRecoveryModal } from "./DeployRecoveryModal";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
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
const mockPost = api.post as jest.Mock;

const defaultProps = {
  open: true,
  onClose: jest.fn(),
  onRecovered: jest.fn(),
  onStartFresh: jest.fn(),
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  defaultProps.onClose.mockReset();
  defaultProps.onRecovered.mockReset();
  defaultProps.onStartFresh.mockReset();
});

describe("DeployRecoveryModal", () => {
  test("shows loading state while checking", async () => {
    // Never resolve so we stay in loading
    mockGet.mockReturnValue(new Promise(() => {}));
    render(<DeployRecoveryModal {...defaultProps} />);
    expect(screen.getByText(/Checking deployment status/)).toBeInTheDocument();
  });

  test("shows recovery option when cluster is RUNNING", async () => {
    mockGet.mockResolvedValue({
      recoverable: [
        {
          id: 1,
          resource_name: "bioaf-demo-abc123",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "abc123",
          gke_status: "RUNNING",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
      provisioning: [],
      dead: [],
    });

    render(<DeployRecoveryModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Previous deployment found/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Resume Deployment/)).toBeInTheDocument();
    expect(screen.getByText(/Start Fresh/)).toBeInTheDocument();
  });

  test("shows provisioning state when cluster is still starting", async () => {
    mockGet.mockResolvedValue({
      recoverable: [],
      provisioning: [
        {
          id: 1,
          resource_name: "bioaf-demo-abc123",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "abc123",
          gke_status: "PROVISIONING",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
      dead: [],
    });

    render(<DeployRecoveryModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Deployment still in progress/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Google Cloud is still setting up/)).toBeInTheDocument();
    expect(screen.getByText(/Google Cloud Status/)).toBeInTheDocument();
    expect(screen.getByText(/Got It/)).toBeInTheDocument();
  });

  test("calls onRecovered after successful adopt", async () => {
    mockGet.mockResolvedValue({
      recoverable: [
        {
          id: 42,
          resource_name: "bioaf-demo-abc123",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "abc123",
          gke_status: "RUNNING",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
      provisioning: [],
      dead: [],
    });
    mockPost.mockResolvedValue({ status: "adopted" });

    render(<DeployRecoveryModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Resume Deployment/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Resume Deployment/));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/api/v1/infrastructure/orphaned-resources/42/adopt",
      );
      expect(defaultProps.onRecovered).toHaveBeenCalled();
    });
  });

  test("calls onStartFresh after cleanup", async () => {
    mockGet.mockResolvedValue({
      recoverable: [
        {
          id: 1,
          resource_name: "bioaf-demo-abc123",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "abc123",
          gke_status: "RUNNING",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
      provisioning: [],
      dead: [],
    });
    mockPost.mockResolvedValue({ cleaned: 1, skipped: 0, failed: 0 });

    render(<DeployRecoveryModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Start Fresh/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Start Fresh/));

    // Start Fresh deletes the previous deployment, and this modal's own copy says
    // that deployment "finished successfully". It confirms now.
    await screen.findByText(/Delete the previous deployment and start over\?/i);
    expect(screen.getByText(/permanently deleted/i)).toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalledWith(
      "/api/v1/infrastructure/orphaned-resources/cleanup-all",
    );

    fireEvent.click(screen.getByRole("button", { name: /Delete and start over/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/api/v1/infrastructure/orphaned-resources/cleanup-all",
      );
      expect(defaultProps.onStartFresh).toHaveBeenCalled();
    });
  });

  test("cancelling Start Fresh deletes nothing", async () => {
    mockGet.mockResolvedValue({
      recoverable: [
        {
          id: 1,
          resource_name: "bioaf-demo-abc123",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "abc123",
          gke_status: "RUNNING",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
      provisioning: [],
      dead: [],
    });

    render(<DeployRecoveryModal {...defaultProps} />);
    await waitFor(() => expect(screen.getByText(/Start Fresh/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Start Fresh/));

    await screen.findByText(/Delete the previous deployment and start over\?/i);
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/i }));

    await waitFor(() =>
      expect(mockPost).not.toHaveBeenCalledWith(
        "/api/v1/infrastructure/orphaned-resources/cleanup-all",
      ),
    );
    expect(defaultProps.onStartFresh).not.toHaveBeenCalled();
  });

  /**
   * This test used to assert the opposite: that opening the modal fired
   * `cleanup-all` "in background". That was the defect, not the feature. A
   * destructive cloud call with no user action, no disclosure and a swallowed
   * failure is not something a render gets to do, however dead the resources
   * look. Nothing is deleted until the user chooses Start Fresh.
   */
  test("deletes nothing merely by opening", async () => {
    mockGet.mockResolvedValue({
      recoverable: [],
      provisioning: [],
      dead: [
        {
          id: 5,
          resource_name: "bioaf-demo-dead",
          gcp_project_id: "test-project",
          gcp_zone: "us-central1",
          stack_uid: "dead01",
          gke_status: "NOT_FOUND",
          detected_at: "2026-04-01T00:00:00Z",
        },
      ],
    });
    mockPost.mockResolvedValue({ cleaned: 1, skipped: 0, failed: 0 });

    render(<DeployRecoveryModal {...defaultProps} />);

    // Let the recovery check settle, then assert nothing was destroyed.
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(mockPost).not.toHaveBeenCalledWith(
      "/api/v1/infrastructure/orphaned-resources/cleanup-all",
    );
  });

  test("does not render when open is false", () => {
    const { container } = render(
      <DeployRecoveryModal {...defaultProps} open={false} />,
    );
    expect(container.innerHTML).toBe("");
  });

  test("shows error when recovery check fails", async () => {
    mockGet.mockRejectedValue(new Error("Network error"));

    render(<DeployRecoveryModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Could not check deployment status/)).toBeInTheDocument();
    });
  });
});
