/**
 * The QC AI review surface (plan 13): an availability check that failed is not a confirmed
 * "AI review is off", and polling for pending reviews never overlaps and stops when they finish.
 */
import { render, screen, act } from "@testing-library/react";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...a: unknown[]) => mockGet(...a), post: jest.fn() } }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => false }) }));

import { QCAiReviewSection } from "./QCAiReviewSection";

function review(status: string) {
  return {
    id: 1, entity_type: "pipeline_run", entity_id: 42, included_run_ids: null, review_type: "x",
    provider: "openai", model: "m", status, severity: "green", headline: `Review ${status}`,
    stale: false, dismissed: false, prompt_source: null, created_at: "2026-01-01T00:00:00Z",
    completed_at: null,
  };
}

type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

async function answerList(items: unknown[]) {
  const i = pending.findIndex((p) => p.url.startsWith("/api/agent_reviews?"));
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p.resolve({ items });
  });
}

function listCalls() {
  return mockGet.mock.calls.filter((c) => String(c[0]).startsWith("/api/agent_reviews?")).length;
}

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  pending = [];
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a failed availability check says so instead of hiding the surface as if AI were off", async () => {
  mockGet.mockImplementation((url: string) =>
    url === "/api/agent_reviews/availability"
      ? Promise.reject(new Error("502"))
      : Promise.resolve({ items: [review("succeeded")] }),
  );
  render(<QCAiReviewSection pipelineRunId={42} />);
  await act(async () => {
    await Promise.resolve();
  });

  expect(screen.getByTestId("qc-ai-review-unavailable")).toHaveTextContent(/could not be checked/i);
});

test("polling for a pending review never overlaps and stops when it finishes", async () => {
  mockGet.mockImplementation((url: string) =>
    url === "/api/agent_reviews/availability"
      ? Promise.resolve({ enabled: true })
      : new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  render(<QCAiReviewSection pipelineRunId={42} />);
  await answerList([review("pending")]);

  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  expect(listCalls()).toBe(2);
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(listCalls()).toBe(2);

  await answerList([review("succeeded")]);
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(listCalls()).toBe(2);
  expect(screen.getByText("Review succeeded")).toBeInTheDocument();
});
