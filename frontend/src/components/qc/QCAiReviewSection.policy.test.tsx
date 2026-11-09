/**
 * The QC AI review surface: the availability check is read with the resource's signal, and the
 * review list's polling is decided from the list itself, including after a failed refresh.
 */
import { render, act } from "@testing-library/react";

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

type Pending = { url: string; signal?: AbortSignal; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function listReads() {
  return pending.filter((p) => p.url.startsWith("/api/agent_reviews?"));
}
async function settleList(fn: (p: Pending) => void) {
  const p = listReads()[0];
  pending.splice(pending.indexOf(p), 1);
  await act(async () => {
    fn(p);
  });
}
async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string, options?: { signal?: AbortSignal }) => {
    if (url.includes("/availability")) {
      return new Promise((resolve, reject) => pending.push({ url, signal: options?.signal, resolve, reject }));
    }
    return new Promise((resolve, reject) => pending.push({ url, signal: options?.signal, resolve, reject }));
  });
});
afterEach(() => jest.useRealTimers());

test("the availability check is read with a signal that aborts when the section closes", () => {
  const { unmount } = render(<QCAiReviewSection pipelineRunId={42} />);
  const availability = pending.find((p) => p.url.includes("/availability"))!;
  expect(availability.signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(availability.signal!.aborted).toBe(true);
});

test("a failed refresh while a review is pending keeps checking, and a finished review stops it", async () => {
  render(<QCAiReviewSection pipelineRunId={42} />);
  await settleList((p) => p.resolve({ items: [review("pending")] }));
  await tick(3000);
  await settleList((p) => p.reject(new Error("502")));
  await tick(3000);
  expect(listReads()).toHaveLength(1);

  await settleList((p) => p.resolve({ items: [review("complete")] }));
  await tick(30_000);
  expect(listReads()).toHaveLength(0);
});
