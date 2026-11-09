/**
 * Owner review: the import-status poll discarded its failures, so an HTTP failure left the last
 * progress on screen as if current. A failed read is now said (and polling continues); a 404,
 * which means the progress row does not exist yet, is not a failure.
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
  reference_id: 1, status: "downloading", progress_pct: 40, bytes_downloaded: 4, total_bytes: 10,
  error_message: null, import_job_id: null, updated_at: null,
};

let replies: Array<() => Promise<unknown>>;

async function flush() {
  await act(async () => {
    for (let n = 0; n < 10; n++) await Promise.resolve();
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
  replies = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/references/1") return Promise.resolve(REF);
    if (url === "/api/references/1/import-status") {
      const next = replies.shift();
      return next ? next() : Promise.resolve(STATUS);
    }
    return Promise.resolve({ references: [], total: 0 });
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a failed progress read is said, the last progress stays, and the next read clears it", async () => {
  replies = [() => Promise.resolve(STATUS), () => Promise.reject(new Error("502"))];
  render(<DataReferenceDetailPage />);
  await flush();
  expect(screen.getByText(/40%/)).toBeInTheDocument();

  await tick(5000);
  expect(screen.getByTestId("import-status-unavailable")).toHaveTextContent(/could not be read/i);
  expect(screen.getByText(/40%/)).toBeInTheDocument();

  await tick(5000);
  expect(screen.queryByTestId("import-status-unavailable")).not.toBeInTheDocument();
});

test("no progress row yet (404) is not reported as a failure", async () => {
  replies = [() => Promise.reject(Object.assign(new Error("Not found"), { status: 404 }))];
  render(<DataReferenceDetailPage />);
  await flush();
  expect(screen.queryByTestId("import-status-unavailable")).not.toBeInTheDocument();
});
