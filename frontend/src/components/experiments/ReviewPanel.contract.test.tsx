/**
 * The panel reads the review API's real response shape.
 *
 * Fixtures mirror `backend/app/api/pipeline_run_reviews.py:_review_response`: the stored
 * `sample_verdicts_json` dictionary, no list `total`, and a reviewer the declared schema
 * allows to be null. A stored review always has a reviewer today (the column is NOT NULL),
 * so the null case is defensive schema alignment, not a reproduced crash.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import { ReviewPanel } from "./ReviewPanel";
import { NOT_SET } from "@/lib/placeholders";
import type { PipelineRunReview, PipelineRunReviewListResponse } from "@/lib/types";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const active: PipelineRunReview = {
  id: 9,
  pipeline_run_id: 28,
  reviewer: { id: 2, name: null, email: "reviewer@lab.test" },
  verdict: "approved_with_caveats",
  notes: "one sample borderline",
  sample_verdicts_json: { "41": { verdict: "warning", notes: "low viability" } },
  recommended_exclusions: [41],
  reviewed_at: "2026-11-12T10:00:00Z",
  is_active: true,
  created_at: "2026-11-12T10:00:00Z",
};
const superseded: PipelineRunReview = {
  ...active,
  id: 8,
  reviewer: null,
  verdict: "a_future_verdict",
  notes: "earlier",
  sample_verdicts_json: null,
  recommended_exclusions: null,
  is_active: false,
};

function serve(list: PipelineRunReviewListResponse, current: PipelineRunReview | null) {
  mockGet.mockImplementation((url: string) =>
    Promise.resolve(url.endsWith("/reviews") ? list : current),
  );
}

beforeEach(() => mockGet.mockReset());

test("a backend-shaped active review renders its reviewer, verdict and exclusions", async () => {
  serve({ reviews: [active] }, active);
  render(<ReviewPanel pipelineRunId={28} userRole="viewer" />);

  await waitFor(() => expect(screen.getByText("Active Review")).toBeInTheDocument());
  expect(screen.getByText(/Reviewed by reviewer@lab\.test on/)).toBeInTheDocument();
  expect(screen.getByText("Approved w/ Caveats")).toBeInTheDocument();
  expect(screen.getByText(/Recommended exclusions: sample IDs 41/)).toBeInTheDocument();
});

test("a review whose reviewer is missing renders the null placeholder instead of failing", async () => {
  const orphan = { ...active, reviewer: null };
  serve({ reviews: [orphan, superseded] }, orphan);
  render(<ReviewPanel pipelineRunId={28} userRole="viewer" />);

  await waitFor(() => expect(screen.getByText("Active Review")).toBeInTheDocument());
  expect(screen.getByText(`Reviewed by ${NOT_SET} on`, { exact: false })).toBeInTheDocument();
  // The superseded history row has no reviewer either, and an unknown verdict keeps a readable label.
  expect(screen.getByText("Review History")).toBeInTheDocument();
  expect(screen.getByText(new RegExp(`^${NOT_SET}:`))).toBeInTheDocument();
  expect(screen.getByText("a future verdict")).toBeInTheDocument();
});
