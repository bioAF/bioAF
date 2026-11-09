/**
 * Reference import progress: status reads never overlap, a terminal status reloads
 * the dataset and stops the watch, and cancelling stops the watch.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

const mockPush = jest.fn();
// A stable router, as Next provides: the page reloads its dataset when the router changes.
const mockRouter = { push: (...a: unknown[]) => mockPush(...a) };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: "1" }),
}));
const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: (...a: unknown[]) => mockPost(...a) },
}));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true, getCurrentUser: () => ({ role_name: "admin" }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false, failed: false }),
}));

import DataReferenceDetailPage from "./page";

const REF = {
  id: 1, organization_id: 1, name: "GRCh38", category: "genome", scope: "public", version: "v45",
  source_url: null, gcs_prefix: "g/", total_size_bytes: 100, file_count: 1, status: "uploading",
  deprecation_note: null, superseded_by_id: null, created_at: "2026-05-01T00:00:00Z", files: [],
  uploaded_by: null, approved_by: null,
};
const STATUS = {
  reference_id: 1, status: "downloading", progress_pct: 10, bytes_downloaded: 1, total_bytes: 10,
  error_message: null, import_job_id: null, updated_at: null,
};

type Pending = { resolve: (v: unknown) => void };
let statusReads: Pending[];
let referenceStatus: string;

function statusCalls() {
  return mockGet.mock.calls.filter(([u]) => u === "/api/references/1/import-status").length;
}

beforeEach(() => {
  jest.useFakeTimers();
  statusReads = [];
  referenceStatus = "uploading";
  mockGet.mockReset();
  mockPost.mockReset();
  mockPush.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/references/1") return Promise.resolve({ ...REF, status: referenceStatus });
    if (url === "/api/references/1/import-status")
      return new Promise((resolve) => statusReads.push({ resolve }));
    return Promise.resolve({ references: [], total: 0 });
  });
});
afterEach(() => jest.useRealTimers());

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

test("a slow status read is never overlapped, and a terminal status reloads and stops the watch", async () => {
  render(<DataReferenceDetailPage />);
  await flush();
  expect(statusCalls()).toBe(1);

  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(statusCalls()).toBe(1);

  referenceStatus = "active";
  await act(async () => {
    statusReads[0].resolve({ ...STATUS, status: "active", progress_pct: 100 });
  });
  await flush();

  const referenceReads = mockGet.mock.calls.filter(([u]) => u === "/api/references/1").length;
  expect(referenceReads).toBe(2);
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(statusCalls()).toBe(1);
});

test("cancelling the import stops watching it", async () => {
  mockPost.mockResolvedValue({});
  render(<DataReferenceDetailPage />);
  await flush();
  await act(async () => {
    statusReads[0].resolve(STATUS);
  });

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /cancel import/i }));
  });
  expect(mockPush).toHaveBeenCalledWith("/data/references");
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(statusCalls()).toBe(1);
});
