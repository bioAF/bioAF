import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReviewPanel } from "@/components/experiments/ReviewPanel";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

const mockActiveReview = {
  id: 10,
  pipeline_run_id: 1,
  verdict: "approved" as const,
  notes: "Looks good",
  reviewed_at: "2026-03-10T12:00:00Z",
  reviewer: { id: 1, name: "Dr. Sarah", email: "sarah@bioaf.org" },
  is_active: true,
  sample_verdicts: null,
  recommended_exclusions: null,
  created_at: "2026-03-10T12:00:00Z",
};

const mockHistoricalReview = {
  ...mockActiveReview,
  id: 9,
  verdict: "revision_requested" as const,
  is_active: false,
};

describe("ReviewPanel", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    // Default: no reviews
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/reviews")) return Promise.resolve({ reviews: [], total: 0 });
      if (url.includes("/review")) return Promise.resolve(null);
      return Promise.resolve({});
    });
  });

  it("shows Submit Review button for comp_bio role", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Submit Review" })).toBeInTheDocument();
    });
  });

  it("shows Submit Review button for admin role", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="admin" />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Submit Review" })).toBeInTheDocument();
    });
  });

  it("hides Submit Review button for bench role", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="bench" />);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Submit Review" })).not.toBeInTheDocument();
    });
  });

  it("hides Submit Review button for viewer role", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="viewer" />);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Submit Review/i })).not.toBeInTheDocument();
    });
  });

  it("clicking Submit Review shows the review form", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => screen.getByRole("button", { name: "Submit Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit Review" }));
    expect(screen.getByLabelText("Verdict")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Review notes/i)).toBeInTheDocument();
  });

  it("review form contains all four verdict options", async () => {
    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => screen.getByRole("button", { name: "Submit Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit Review" }));

    const select = screen.getByRole("combobox");
    expect(select).toContainHTML("Approved");
    expect(select).toContainHTML("Approved with Caveats");
    expect(select).toContainHTML("Rejected");
    expect(select).toContainHTML("Revision Requested");
  });

  it("submitting a review posts to the API and closes the form", async () => {
    mockApiPost.mockResolvedValue({});
    // Load with no reviews so button says Submit Review (not Submit New Review)
    // beforeEach already sets this up, so just render directly

    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => screen.getByRole("button", { name: "Submit Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit Review" }));

    // After opening the form, there are two Submit Review buttons: toggle + submit
    // Override reload to return active review after post
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/reviews")) return Promise.resolve({ reviews: [mockActiveReview], total: 1 });
      if (url.includes("/review")) return Promise.resolve(mockActiveReview);
      return Promise.resolve({});
    });

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "rejected" } });
    fireEvent.change(screen.getByPlaceholderText(/Review notes/i), { target: { value: "Bad results" } });
    const submitButtons = screen.getAllByRole("button", { name: "Submit Review" });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/pipeline-runs/1/reviews",
        expect.objectContaining({ verdict: "rejected", notes: "Bad results" })
      );
    });
  });

  it("displays active review with verdict badge", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/reviews")) return Promise.resolve({ reviews: [mockActiveReview], total: 1 });
      if (url.includes("/review")) return Promise.resolve(mockActiveReview);
      return Promise.resolve({});
    });

    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => {
      expect(screen.getByText("Active Review")).toBeInTheDocument();
      expect(screen.getByText("Approved")).toBeInTheDocument();
      expect(screen.getByText("Looks good")).toBeInTheDocument();
    });
  });

  it("shows review history when multiple reviews exist", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/reviews"))
        return Promise.resolve({ reviews: [mockActiveReview, mockHistoricalReview], total: 2 });
      if (url.includes("/review")) return Promise.resolve(mockActiveReview);
      return Promise.resolve({});
    });

    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => {
      expect(screen.getByText("Review History")).toBeInTheDocument();
      expect(screen.getByText("Revision Requested")).toBeInTheDocument();
    });
  });

  it("shows Submit New Review when an active review already exists", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/reviews")) return Promise.resolve({ reviews: [mockActiveReview], total: 1 });
      if (url.includes("/review")) return Promise.resolve(mockActiveReview);
      return Promise.resolve({});
    });

    render(<ReviewPanel pipelineRunId={1} userRole="comp_bio" />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Submit New Review" })).toBeInTheDocument();
    });
  });
});
