import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AutoIngestControls } from "@/components/components/AutoIngestControls";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

const defaultStatus = {
  enabled: false,
  cleanup_policy: "delete_after_copy",
  listener_running: false,
  pubsub_topic: "bioaf-ingest-events-testorg",
  pubsub_subscription: "bioaf-ingest-worker-testorg",
  messages_processed_24h: 12,
  messages_failed_24h: 2,
};

describe("AutoIngestControls", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
  });

  // Test 25: Toggle renders
  it("renders auto-ingest toggle when storage is deployed", async () => {
    mockApiGet.mockResolvedValue(defaultStatus);
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={true} />);
    await waitFor(() => {
      expect(screen.getByText(/auto-ingest/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /toggle/i }) || screen.getByTestId("auto-ingest-toggle")).toBeTruthy();
    });
  });

  // Test 26: Toggle calls API
  it("calls API with correct enabled value when toggle is clicked", async () => {
    mockApiGet.mockResolvedValue(defaultStatus);
    mockApiPost.mockResolvedValue({ status: "ok", enabled: true });
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={true} />);
    await waitFor(() => {
      expect(screen.getByTestId("auto-ingest-toggle")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("auto-ingest-toggle"));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/v1/settings/auto-ingest",
        expect.objectContaining({ enabled: true }),
      );
    });
  });

  // Test 27: Status indicator
  it("shows green dot and Active when enabled and running", async () => {
    mockApiGet.mockResolvedValue({
      ...defaultStatus,
      enabled: true,
      listener_running: true,
    });
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={true} />);
    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
      expect(screen.getByTestId("status-dot")).toHaveClass("bg-green-500");
    });
  });

  // Test 28: Cleanup dropdown
  it("shows cleanup policy dropdown when auto-ingest is enabled", async () => {
    mockApiGet.mockResolvedValue({
      ...defaultStatus,
      enabled: true,
      listener_running: true,
    });
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={true} />);
    await waitFor(() => {
      expect(screen.getByTestId("cleanup-policy-select")).toBeInTheDocument();
    });
    const select = screen.getByTestId("cleanup-policy-select");
    expect(select).toBeInTheDocument();
  });

  // Test 29: Update notice when Pub/Sub not deployed
  it("shows update notice when pubsub not configured", async () => {
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={false} />);
    expect(screen.getByText(/Pub\/Sub notification infrastructure/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /update storage/i }),
    ).toBeInTheDocument();
  });

  // Test 30: Ingest stats display
  it("displays processed and failed counts", async () => {
    mockApiGet.mockResolvedValue({
      ...defaultStatus,
      enabled: true,
      listener_running: true,
      messages_processed_24h: 42,
      messages_failed_24h: 3,
    });
    render(<AutoIngestControls storageDeployed={true} pubsubConfigured={true} />);
    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument();
      expect(screen.getByText("3")).toBeInTheDocument();
    });
  });
});
