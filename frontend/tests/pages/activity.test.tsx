import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ActivityFeedPage from "@/app/activity/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  usePathname: () => "/activity",
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "test@bioaf.org", role: "admin", sub: "1" }),
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

const mockEvents = [
  {
    id: 1,
    user_id: 1,
    user_email: "maria@bioaf.org",
    event_type: "pipeline.completed",
    entity_type: "pipeline_run",
    entity_id: 42,
    summary: "Pipeline nf-core/rnaseq completed successfully",
    severity: "info",
    created_at: "2026-03-10T12:00:00Z",
  },
  {
    id: 2,
    user_id: 2,
    user_email: "sarah@bioaf.org",
    event_type: "data.uploaded",
    entity_type: "experiment",
    entity_id: 5,
    summary: "Uploaded 3 FASTQ files",
    severity: "info",
    created_at: "2026-03-10T11:00:00Z",
  },
];

describe("Activity Feed Page", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ events: mockEvents, total: 2 });
  });

  it("renders with mock events", async () => {
    render(<ActivityFeedPage />);
    await waitFor(() => {
      expect(screen.getByText("Pipeline nf-core/rnaseq completed successfully")).toBeInTheDocument();
      expect(screen.getByText("Uploaded 3 FASTQ files")).toBeInTheDocument();
    });
  });

  it("filters by event type", async () => {
    render(<ActivityFeedPage />);
    await waitFor(() => {
      expect(screen.getAllByTestId("activity-event")).toHaveLength(2);
    });
    const select = screen.getByTestId("filter-event-type");
    fireEvent.change(select, { target: { value: "pipeline.completed" } });
    // The API is called again with the filter
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining("event_type=pipeline.completed"),
    );
  });

  it("filters by user", async () => {
    render(<ActivityFeedPage />);
    await waitFor(() => {
      expect(screen.getAllByTestId("activity-event")).toHaveLength(2);
    });
    const input = screen.getByTestId("filter-user");
    fireEvent.change(input, { target: { value: "maria@bioaf.org" } });
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining("user_email=maria"),
    );
  });

  it("entity links navigate to correct detail page", async () => {
    render(<ActivityFeedPage />);
    await waitFor(() => {
      const links = screen.getAllByTestId("entity-link");
      expect(links[0]).toHaveAttribute("href", "/pipelines/runs/42");
      expect(links[1]).toHaveAttribute("href", "/experiments/5");
    });
  });

  it("pagination works", async () => {
    mockApiGet.mockResolvedValue({
      events: mockEvents,
      total: 100,
    });
    render(<ActivityFeedPage />);
    await waitFor(() => {
      expect(screen.getByTestId("activity-pagination")).toBeInTheDocument();
    });
    const nextBtn = screen.getByText("Next");
    fireEvent.click(nextBtn);
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining("page=2"),
    );
  });
});
