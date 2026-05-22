import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QCAiReviewSection } from "./QCAiReviewSection";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));

const mockCanAccess = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: mockCanAccess,
    roleName: "admin",
    loading: false,
    permissions: new Set(),
  }),
}));

// Stub the heavy prompt-builder; we only assert it opens and that submit refetches.
jest.mock("@/components/agent-reviews/SectionBuilderModal", () => ({
  SectionBuilderModal: (props: { onSubmitted: () => void; onCancel: () => void }) => (
    <div data-testid="section-builder">
      <button onClick={props.onSubmitted}>builder-submit</button>
    </div>
  ),
}));

const mockGet = api.get as jest.Mock;

function review(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    entity_type: "pipeline_run",
    entity_id: 42,
    included_run_ids: null,
    review_type: "pipeline_run_review",
    provider: "openai",
    model: "gpt-5",
    status: "succeeded",
    severity: "green",
    headline: "Looks good overall here",
    stale: false,
    dismissed: false,
    prompt_source: null,
    created_at: "2026-05-22T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

function setupApi({ enabled = true, items = [] as Record<string, unknown>[] } = {}) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/agent_reviews/availability") return Promise.resolve({ enabled });
    if (url.startsWith("/api/agent_reviews?")) return Promise.resolve({ items });
    if (url === "/api/agent_reviews/section_catalog") return Promise.resolve({ sections: [] });
    if (/\/api\/agent_reviews\/\d+$/.test(url)) {
      return Promise.resolve({
        ...review(),
        flags: null,
        evidence: null,
        body: "Detailed notes",
        error_text: null,
        artifact_gcs_paths: [],
        dismissed_at: null,
        dismissed_by_user_id: null,
        prompt_text: null,
        prompt_sections: null,
        prompt_custom_id: null,
      });
    }
    return Promise.resolve({});
  });
}

function listCalls() {
  return mockGet.mock.calls.filter((c) => String(c[0]).startsWith("/api/agent_reviews?")).length;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

it("shows 'Run AI Review' when the run has never been reviewed", async () => {
  mockCanAccess.mockReturnValue(true);
  setupApi({ enabled: true, items: [] });
  render(<QCAiReviewSection pipelineRunId={42} />);
  expect(await screen.findByText("Run AI Review")).toBeInTheDocument();
  // No cards panel when there are no reviews.
  expect(screen.queryByText(/AI Reviews \(/)).not.toBeInTheDocument();
});

it("shows 'Re-run AI Review' when the run was ever reviewed, even if dismissed", async () => {
  mockCanAccess.mockReturnValue(true);
  setupApi({ enabled: true, items: [review({ dismissed: true })] });
  render(<QCAiReviewSection pipelineRunId={42} />);
  expect(await screen.findByText("Re-run AI Review")).toBeInTheDocument();
  // Dismissed-only means no active cards to show.
  expect(screen.queryByText(/AI Reviews \(/)).not.toBeInTheDocument();
});

it("renders active review cards and opens the modal on click (no trigger for viewers)", async () => {
  mockCanAccess.mockReturnValue(false); // a View-Results user without llm_integration:use
  setupApi({ enabled: true, items: [review()] });
  render(<QCAiReviewSection pipelineRunId={42} />);

  expect(await screen.findByText("AI Reviews (1)")).toBeInTheDocument();
  expect(screen.queryByText("Run AI Review")).not.toBeInTheDocument();
  expect(screen.queryByText("Re-run AI Review")).not.toBeInTheDocument();

  fireEvent.click(screen.getByText("Looks good overall here"));
  // The detail modal carries a Close button the card does not.
  expect(await screen.findByText("Close")).toBeInTheDocument();
});

it("hides the entire surface when AI is not enabled, even with reviews", async () => {
  mockCanAccess.mockReturnValue(true);
  setupApi({ enabled: false, items: [review()] });
  render(<QCAiReviewSection pipelineRunId={42} />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByTestId("qc-ai-review-section")).not.toBeInTheDocument();
  expect(screen.queryByText("Run AI Review")).not.toBeInTheDocument();
});

it("opens the prompt-builder from the trigger and refetches on submit", async () => {
  mockCanAccess.mockReturnValue(true);
  setupApi({ enabled: true, items: [] });
  render(<QCAiReviewSection pipelineRunId={42} />);

  fireEvent.click(await screen.findByText("Run AI Review"));
  expect(screen.getByTestId("section-builder")).toBeInTheDocument();

  const before = listCalls();
  fireEvent.click(screen.getByText("builder-submit"));
  await waitFor(() => expect(screen.queryByTestId("section-builder")).not.toBeInTheDocument());
  await waitFor(() => expect(listCalls()).toBeGreaterThan(before));
});

it("minimizes and expands the cards panel and persists the choice", async () => {
  mockCanAccess.mockReturnValue(false);
  setupApi({ enabled: true, items: [review()] });
  render(<QCAiReviewSection pipelineRunId={42} />);

  expect(await screen.findByText("Looks good overall here")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /AI Reviews \(1\)/ }));

  await waitFor(() =>
    expect(screen.queryByText("Looks good overall here")).not.toBeInTheDocument(),
  );
  expect(localStorage.getItem("qcAiReviewSection:42:collapsed")).toBe("1");
});

it("polls while a review is pending", async () => {
  jest.useFakeTimers();
  try {
    mockCanAccess.mockReturnValue(false);
    setupApi({ enabled: true, items: [review({ status: "pending" })] });
    render(<QCAiReviewSection pipelineRunId={42} />);
    await act(async () => {}); // flush the initial availability + list fetches
    const before = listCalls();
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });
    expect(listCalls()).toBeGreaterThan(before);
  } finally {
    jest.useRealTimers();
  }
});

it("renders an inline error when the review list fails to load", async () => {
  mockCanAccess.mockReturnValue(true);
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/agent_reviews/availability") return Promise.resolve({ enabled: true });
    if (url.startsWith("/api/agent_reviews?")) return Promise.reject(new Error("boom"));
    return Promise.resolve({});
  });
  render(<QCAiReviewSection pipelineRunId={42} />);
  expect(await screen.findByText("boom")).toBeInTheDocument();
});
