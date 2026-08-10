/**
 * A run whose review history could not be read is not a run that was never
 * reviewed.
 *
 * `loadReviews` swallowed every rejection, leaving `reviews` at `[]` and
 * `activeReview` at `null` -- exactly the state of a genuinely unreviewed run.
 * Proven on the deployed demo: with /api/pipeline-runs/28/reviews failing, the
 * panel rendered byte-identically to the healthy one.
 *
 * The consequence is not cosmetic. The panel then offers "Submit Review"
 * (rather than "Submit New Review"), so a reviewer who cannot see the existing
 * verdict is invited to file a second one over the top of it. A read that
 * failed must not arm a write.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import { ReviewPanel } from "./ReviewPanel";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));
// `userRole` is a prop; "admin" is what makes the submit affordance render at all.
const asAdmin = { pipelineRunId: 28, userRole: "admin" };

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed read says so instead of looking unreviewed", async () => {
  mockGet.mockRejectedValue(new Error("boom"));
  render(<ReviewPanel {...asAdmin} />);

  await waitFor(() =>
    expect(screen.getByTestId("reviews-load-failed")).toBeInTheDocument(),
  );
  expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  expect(errorLog).toHaveBeenCalled();
});

test("a failed read does not arm a second review over an unseen one", async () => {
  mockGet.mockRejectedValue(new Error("boom"));
  render(<ReviewPanel {...asAdmin} />);

  await waitFor(() =>
    expect(screen.getByTestId("reviews-load-failed")).toBeInTheDocument(),
  );
  const submit = screen.getByRole("button", { name: /submit review/i });
  expect(submit).toBeDisabled();
});

test("a run that really has no reviews still offers to submit one", async () => {
  mockGet.mockImplementation((url: string) =>
    url.endsWith("/reviews")
      ? Promise.resolve({ reviews: [] })
      : Promise.reject(new Error("no active review")),
  );
  render(<ReviewPanel {...asAdmin} />);

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /submit review/i })).toBeEnabled(),
  );
  expect(screen.queryByTestId("reviews-load-failed")).not.toBeInTheDocument();
});
