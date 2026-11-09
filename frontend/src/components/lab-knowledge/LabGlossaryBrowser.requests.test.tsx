/**
 * The glossary list and AI scan watch. A late answer for an earlier query/source must
 * not replace the current list; the scan poll never overlaps, stops at a terminal status, and a
 * completed scan refreshes the pending-review count.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn(), upload: jest.fn() },
}));

import { LabGlossaryBrowser } from "./LabGlossaryBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];
let pendingCount: number;

const term = (id: number, name: string) => ({
  id, term: name, definition: "d", aliases: [], category: null, context: null, source: "manual",
  created_by: null, created_at: "2026-01-15T10:00:00Z", updated_at: "2026-01-15T10:00:00Z",
});

async function answer(fragment: string, value: unknown) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending request for ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p.resolve(value);
  });
}

function scanReads() {
  return mockGet.mock.calls.filter(([u]) => String(u).includes("/glossary/scan/7")).length;
}

beforeEach(() => {
  pending = [];
  pendingCount = 0;
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending"))
      return Promise.resolve({ pending_review_count: pendingCount, job_ids: pendingCount ? [7] : [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [], total: 0 });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
});

afterEach(() => jest.useRealTimers());

test("an earlier source filter's late answer does not replace the current list", async () => {
  render(<LabGlossaryBrowser />);
  fireEvent.change(screen.getByLabelText(/filter by source/i), { target: { value: "import" } });

  await answer("source=import", { terms: [term(2, "Imported term")], total: 1 });
  await answer("/glossary?", { terms: [term(1, "Manual term")], total: 1 });

  expect(screen.getByText("Imported term")).toBeInTheDocument();
  expect(screen.queryByText("Manual term")).not.toBeInTheDocument();
});

test("the scan watch never overlaps, stops when complete, and refreshes pending review", async () => {
  jest.useFakeTimers();
  mockPost.mockResolvedValue({ id: 7, scan_type: "platform_wide", status: "pending" });
  render(<LabGlossaryBrowser />);
  await answer("/glossary?", { terms: [], total: 0 });

  fireEvent.click(screen.getByRole("button", { name: /ai scan/i }));
  fireEvent.change(screen.getByLabelText(/scan type/i), { target: { value: "platform_wide" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /start ai scan/i }));
  });
  expect(screen.getByTestId("scan-running-banner")).toBeInTheDocument();
  expect(scanReads()).toBe(1);

  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(scanReads()).toBe(1);

  pendingCount = 3;
  await answer("/glossary/scan/7", { id: 7, scan_type: "platform_wide", status: "complete" });

  expect(screen.queryByTestId("scan-running-banner")).not.toBeInTheDocument();
  expect(screen.getByText(/3 proposed terms awaiting review/)).toBeInTheDocument();
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(scanReads()).toBe(1);
});

test("a failed scan is reported and the watch stops", async () => {
  jest.useFakeTimers();
  mockPost.mockResolvedValue({ id: 7, scan_type: "platform_wide", status: "pending" });
  render(<LabGlossaryBrowser />);
  await answer("/glossary?", { terms: [], total: 0 });
  fireEvent.click(screen.getByRole("button", { name: /ai scan/i }));
  fireEvent.change(screen.getByLabelText(/scan type/i), { target: { value: "platform_wide" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /start ai scan/i }));
  });

  await answer("/glossary/scan/7", { id: 7, scan_type: "platform_wide", status: "failed" });

  expect(screen.getByText("The AI glossary scan could not be completed.")).toBeInTheDocument();
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(scanReads()).toBe(1);
});
