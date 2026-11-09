/**
 * While a restored database is under review the page polls its status every 30 s.
 * Reads do not overlap, an expired review reloads the page data with its message, and a failed
 * status read is not an expiry.
 */
import { render, screen, act } from "@/testing/renderWithProviders";
import { fakeServer, type FakeReply } from "@/testing/fakeServer";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => mockRouter }));
// A stable router and permissions object, as the real hooks return: the page reloads its data
// when either changes identity.
const mockPermissions = { canAccess: () => true, loading: false };
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import InfraBackupPage from "./page";
import { startSession } from "@/lib/session";

const realFetch = global.fetch;
const REVIEWING = { active: true, backup_filename: "pg-2026-11-01.sql.gz", seconds_remaining: 1200 };

const BASE: Array<[string, FakeReply]> = [
  ["/api/backups/status", { json: { tiers: [], overall_status: "healthy" } }],
  ["/api/backups/config-snapshots", { json: { snapshots: [] } }],
  ["/api/backups/postgres-snapshots", { json: { snapshots: [] } }],
  ["/api/backups/tfstate-files", { json: { files: [] } }],
  ["/api/backups/settings", { json: {
    postgres_retention_days: 7, postgres_schedule_hours: 24, postgres_schedule_enabled: true,
    postgres_next_run: null, config_retention_days: 7, config_schedule_hours: 24,
    config_schedule_enabled: true, config_next_run: null,
  } }],
];

/** Answers for /api/backups/restore/status, in order; the last one repeats. */
function serveRestoreStatus(replies: FakeReply[]) {
  let i = 0;
  return fakeServer([
    ...BASE,
    ["/api/backups/restore/status", () => replies[Math.min(i++, replies.length - 1)]],
  ]);
}

async function flush() {
  await act(async () => {
    for (let n = 0; n < 50; n++) await Promise.resolve();
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
  localStorage.clear();
  startSession("live.jwt");
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

function statusReads(requests: { url: string }[]) {
  return requests.filter((r) => r.url.includes("/api/backups/restore/status")).length;
}

test("an expired review is announced and the page data reloads", async () => {
  const { requests } = serveRestoreStatus([{ json: REVIEWING }, { json: REVIEWING }, { json: { active: false } }]);
  render(<InfraBackupPage />);
  await flush();
  expect(screen.getByText(/Reviewing restored database/)).toBeInTheDocument();
  const before = statusReads(requests);

  await tick(30_000);
  await tick(30_000);

  expect(screen.getByText("Restore review expired. Reverted to original database.")).toBeInTheDocument();
  expect(screen.queryByText(/Reviewing restored database/)).not.toBeInTheDocument();
  // The reload after expiry reads every section again, the status included.
  expect(requests.filter((r) => r.url.includes("/api/backups/settings")).length).toBe(2);

  const after = statusReads(requests);
  await tick(120_000);
  expect(statusReads(requests)).toBe(after);
  expect(after).toBeGreaterThan(before);
});

test("a failed status read keeps the review on screen and is not an expiry", async () => {
  serveRestoreStatus([
    { json: REVIEWING },
    { json: REVIEWING },
    { status: 502, json: { detail: "Bad gateway" } },
  ]);
  render(<InfraBackupPage />);
  await flush();
  expect(screen.getByText(/Reviewing restored database/)).toBeInTheDocument();

  await tick(30_000);
  await tick(30_000);

  expect(screen.getByText(/Reviewing restored database/)).toBeInTheDocument();
  expect(screen.getByText(/Restore status could not be refreshed/)).toBeInTheDocument();
  expect(screen.queryByText(/Restore review expired/)).not.toBeInTheDocument();
});

test("no review in progress: the status is read once and not polled", async () => {
  const { requests } = serveRestoreStatus([{ json: { active: false } }]);
  render(<InfraBackupPage />);
  await flush();
  expect(statusReads(requests)).toBe(1);
  await tick(120_000);
  expect(statusReads(requests)).toBe(1);
});

test("a failed first status read is said with the page's load error, and Retry recovers", async () => {
  serveRestoreStatus([{ status: 502, json: { detail: "Bad gateway" } }, { json: REVIEWING }]);
  render(<InfraBackupPage />);
  await flush();
  expect(screen.getByText(/Backup status could not be loaded/i)).toBeInTheDocument();

  await act(async () => {
    screen.getByRole("button", { name: "Retry" }).click();
  });
  await flush();
  expect(screen.queryByText(/Backup status could not be loaded/i)).not.toBeInTheDocument();
  expect(screen.getByText(/Reviewing restored database/)).toBeInTheDocument();
});

test("leaving the page cancels a restore status read in flight", async () => {
  const { fetchMock } = serveRestoreStatus([{ json: REVIEWING }]);
  const signals: AbortSignal[] = [];
  const answered = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (url: RequestInfo | URL, init: RequestInit = {}) => {
    if (String(url).includes("/api/backups/restore/status")) {
      signals.push(init.signal!);
      return new Promise<Response>(() => {});
    }
    return answered(url, init);
  });
  const { unmount } = render(<InfraBackupPage />);
  await flush();
  expect(signals.length).toBeGreaterThan(0);
  unmount();
  expect(signals.every((s) => s.aborted)).toBe(true);
});
