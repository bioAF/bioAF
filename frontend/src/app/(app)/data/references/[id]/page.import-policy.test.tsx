/**
 * Reference import progress: polling is decided from the progress the resource holds. No
 * progress row yet (404) keeps checking; a terminal status stops the watch even while the
 * reloaded dataset still says "uploading" (a finished download awaiting Finalize), instead of
 * re-reading the same terminal answer; and the read is cancelled when the page closes.
 */
import { render, screen, act } from "@testing-library/react";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: "1" }),
}));
const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...a: unknown[]) => mockGet(...a), post: jest.fn() },
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
const PATH = "/api/references/1/import-status";

type Pending = { signal?: AbortSignal; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let statusReads: Pending[];

const statusCalls = () => mockGet.mock.calls.filter(([u]) => u === PATH).length;
async function flush() {
  await act(async () => {
    for (let n = 0; n < 5; n++) await Promise.resolve();
  });
}
async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  statusReads = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string, options?: { signal?: AbortSignal }) => {
    if (url === "/api/references/1") return Promise.resolve(REF);
    if (url === PATH) {
      return new Promise((resolve, reject) => statusReads.push({ signal: options?.signal, resolve, reject }));
    }
    return Promise.resolve({ references: [], total: 0 });
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("the progress read carries a signal that aborts when the page closes", async () => {
  const { unmount } = render(<DataReferenceDetailPage />);
  await flush();
  expect(statusReads[0].signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(statusReads[0].signal!.aborted).toBe(true);
});

test("no progress row yet (404) keeps checking", async () => {
  render(<DataReferenceDetailPage />);
  await flush();
  await act(async () => {
    statusReads[0].reject(Object.assign(new Error("Not found"), { status: 404 }));
  });
  await tick(30_000);
  expect(statusCalls()).toBe(2);
});

test("a terminal status stops the watch even while the dataset still says uploading", async () => {
  render(<DataReferenceDetailPage />);
  await flush();
  await act(async () => {
    statusReads[0].resolve({ ...STATUS, status: "active", progress_pct: 100 });
  });
  await flush();
  expect(screen.getByRole("button", { name: /finalize import/i })).toBeInTheDocument();

  await tick(60_000);
  expect(statusCalls()).toBe(1);
});
