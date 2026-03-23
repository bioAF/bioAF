import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConnectButton } from "@/components/shared/ConnectButton";

const mockGetCurrentUser = jest.fn();
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => mockGetCurrentUser(),
}));

const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { post: (...args: unknown[]) => mockApiPost(...args) },
}));

const mockConnection = {
  command: "kubectl exec -it -n bioaf-pipelines job/pipeline-run-1 -- /bin/bash",
  setup_guide: "1. Install gcloud CLI\n2. Authenticate\n3. Get credentials",
  warning: "Actions performed inside this container are NOT tracked",
  target_type: "pipeline_job",
  target_id: "pipeline-run-1",
  namespace: "bioaf-pipelines",
};

describe("ConnectButton", () => {
  beforeEach(() => {
    mockGetCurrentUser.mockReturnValue({ email: "test@bioaf.org", role_name: "comp_bio", sub: "1" });
    mockApiPost.mockReset();
    mockApiPost.mockResolvedValue(mockConnection);
  });

  it("renders when user has comp_bio role", () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    expect(screen.getByTestId("connect-button")).toBeInTheDocument();
  });

  it("is hidden when user has bench role", () => {
    mockGetCurrentUser.mockReturnValue({ email: "bench@bioaf.org", role_name: "bench", sub: "2" });
    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    expect(screen.queryByTestId("connect-button")).not.toBeInTheDocument();
  });

  it("calls API and expands to show command on click", async () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    fireEvent.click(screen.getByTestId("connect-button"));
    await waitFor(() => {
      expect(screen.getByTestId("connect-expanded")).toBeInTheDocument();
      expect(screen.getByTestId("connect-command")).toHaveTextContent("kubectl exec");
    });
    expect(mockApiPost).toHaveBeenCalledWith("/api/pipeline-runs/1/connect");
  });

  it("copy button copies command to clipboard", async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    fireEvent.click(screen.getByTestId("connect-button"));
    await waitFor(() => {
      expect(screen.getByTestId("copy-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("copy-button"));
    expect(writeText).toHaveBeenCalledWith(mockConnection.command);
  });

  it("setup guide is collapsible", async () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    fireEvent.click(screen.getByTestId("connect-button"));
    await waitFor(() => {
      expect(screen.getByTestId("setup-guide-toggle")).toBeInTheDocument();
    });
    // Initially hidden
    expect(screen.queryByTestId("setup-guide-content")).not.toBeInTheDocument();
    // Click to show
    fireEvent.click(screen.getByTestId("setup-guide-toggle"));
    expect(screen.getByTestId("setup-guide-content")).toBeInTheDocument();
    // Click to hide
    fireEvent.click(screen.getByTestId("setup-guide-toggle"));
    expect(screen.queryByTestId("setup-guide-content")).not.toBeInTheDocument();
  });

  it("shows tooltip when disabled", () => {
    render(<ConnectButton targetType="pipeline_run" targetId={1} disabled />);
    const btn = screen.getByTestId("connect-button");
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toBe("Target is not running");
  });

  it("shows error on API failure", async () => {
    mockApiPost.mockRejectedValueOnce(new Error("Connection failed"));
    render(<ConnectButton targetType="pipeline_run" targetId={1} />);
    fireEvent.click(screen.getByTestId("connect-button"));
    await waitFor(() => {
      expect(screen.getByTestId("connect-error")).toHaveTextContent("Connection failed");
    });
  });
});
