/**
 * Owner review: the scan poll discarded its HTTP failures (a job reporting status "failed" was
 * covered; a read that failed was not), and a failed pending-review count read looked like
 * "nothing awaiting review". Both are now said.
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
let scanReplies: Array<() => Promise<unknown>>;
let pendingReply: () => Promise<unknown>;

async function flush() {
  await act(async () => {
    for (let n = 0; n < 10; n++) await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  scanReplies = [];
  pendingReply = () => Promise.resolve({ pending_review_count: 0, job_ids: [] });
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending")) return pendingReply();
    if (url.includes("/glossary/scan/7")) {
      const next = scanReplies.shift();
      return next ? next() : new Promise(() => {});
    }
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [], total: 0 });
    return Promise.resolve({ terms: [], total: 0 });
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

async function startScan() {
  mockPost.mockResolvedValue({ id: 7, scan_type: "platform_wide", status: "pending" });
  render(<LabGlossaryBrowser />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: /ai scan/i }));
  fireEvent.change(screen.getByLabelText(/scan type/i), { target: { value: "platform_wide" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /start ai scan/i }));
  });
  await flush();
}

test("a failed scan status read is said while the watch continues, and clears on the next read", async () => {
  scanReplies = [() => Promise.reject(new Error("502")), () => Promise.resolve({ id: 7, status: "running" })];
  await startScan();

  expect(screen.getByTestId("scan-running-banner")).toBeInTheDocument();
  expect(screen.getByTestId("scan-status-unavailable")).toHaveTextContent(/could not be read/i);

  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  await flush();
  expect(screen.queryByTestId("scan-status-unavailable")).not.toBeInTheDocument();
  expect(screen.getByTestId("scan-running-banner")).toBeInTheDocument();
});

test("a failed pending-review count is said, not shown as nothing awaiting review", async () => {
  pendingReply = () => Promise.reject(new Error("502"));
  render(<LabGlossaryBrowser />);
  await flush();
  expect(screen.getByTestId("pending-count-unavailable")).toHaveTextContent(/could not be checked/i);
});
