/**
 * The review list: a filter change must not show the previous filter's late answer,
 * polling runs only while a review is pending and never overlaps, a failed load is not "no
 * reviews", and the parent's refresh signal re-reads the list.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...a: unknown[]) => mockGet(...a) } }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));

import { AgentReviewTab } from "./AgentReviewTab";

type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function review(id: number, headline: string, status = "completed") {
  return {
    id, entity_type: "experiment", entity_id: 5, included_run_ids: null, review_type: "x",
    provider: "openai", model: "m", status, severity: "info", headline, stale: false,
    dismissed: false, prompt_source: null, created_at: "2026-01-01T00:00:00Z", completed_at: null,
  };
}

async function answer(fragment: string, items: unknown[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending request for ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p.resolve({ items });
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (url: string) => new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
});
afterEach(() => jest.useRealTimers());

test("a previous filter's late answer is not shown under the new filter", async () => {
  render(<AgentReviewTab entityType="experiment" entityId={5} />);
  fireEvent.click(screen.getByRole("button", { name: "Dismissed" }));

  await answer("filter=dismissed", [review(2, "Dismissed one")]);
  await answer("filter=active", [review(1, "Active one")]);

  expect(screen.getByText("Dismissed one")).toBeInTheDocument();
  expect(screen.queryByText("Active one")).not.toBeInTheDocument();
});

test("polls only while a review is pending, one read at a time", async () => {
  render(<AgentReviewTab entityType="experiment" entityId={5} />);
  await answer("filter=active", [review(1, "Working", "pending")]);

  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);

  await answer("filter=active", [review(1, "Done")]);
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Done")).toBeInTheDocument();
});

test("a failed load is not reported as no matching reviews", async () => {
  render(<AgentReviewTab entityType="experiment" entityId={5} />);
  const [p] = pending.splice(0, 1);
  await act(async () => {
    p.reject(new Error("Reviews are unavailable."));
  });

  expect(screen.getByText("Reviews are unavailable.")).toBeInTheDocument();
  expect(screen.queryByText(/No reviews match/)).not.toBeInTheDocument();
});

test("the parent's refresh signal re-reads the list", async () => {
  const { rerender } = render(<AgentReviewTab entityType="experiment" entityId={5} refreshSignal={0} />);
  await answer("filter=active", []);
  rerender(<AgentReviewTab entityType="experiment" entityId={5} refreshSignal={1} />);
  expect(mockGet).toHaveBeenCalledTimes(2);
  await answer("filter=active", [review(3, "New review")]);
  expect(screen.getByText("New review")).toBeInTheDocument();
});
