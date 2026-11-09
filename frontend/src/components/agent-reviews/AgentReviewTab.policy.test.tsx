/**
 * The review list's polling is decided from the list the resource holds, per filter: a filter
 * change cancels the previous filter's read, and a pending review under one filter does not keep
 * another filter polling.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...a: unknown[]) => mockGet(...a) } }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));

import { AgentReviewTab } from "./AgentReviewTab";

type Pending = { url: string; signal?: AbortSignal; resolve: (v: unknown) => void; reject: (e: unknown) => void };
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
const reads = (fragment: string) =>
  mockGet.mock.calls.filter(([url]) => String(url).includes(fragment)).length;

beforeEach(() => {
  jest.useFakeTimers();
  localStorage.clear();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (url: string, options?: { signal?: AbortSignal }) =>
      new Promise((resolve, reject) => pending.push({ url, signal: options?.signal, resolve, reject })),
  );
});
afterEach(() => jest.useRealTimers());

test("a filter change aborts the previous filter's read", () => {
  render(<AgentReviewTab entityType="experiment" entityId={5} />);
  const first = pending[0].signal!;
  expect(first).toBeInstanceOf(AbortSignal);
  fireEvent.click(screen.getByRole("button", { name: "Dismissed" }));
  expect(first.aborted).toBe(true);
});

test("a pending review under one filter does not keep the next filter polling", async () => {
  render(<AgentReviewTab entityType="experiment" entityId={5} />);
  await answer("filter=active", [review(1, "Working", "pending")]);
  fireEvent.click(screen.getByRole("button", { name: "Dismissed" }));
  await answer("filter=dismissed", [review(2, "Done")]);

  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(reads("filter=dismissed")).toBe(1);
});
