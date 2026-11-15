/**
 * Starting a restore opens its review at once: the banner with Accept and Reject appears as soon as the
 * restore answers, without reloading the page (the review status is otherwise re-read only while a review is
 * already active).
 */
import { render, screen, act, within } from "@/testing/renderWithProviders";
import { fakeServer } from "@/testing/fakeServer";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => mockRouter }));
const mockPermissions = { canAccess: () => true, loading: false };
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => mockPermissions }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import InfraBackupPage from "./page";
import { startSession } from "@/lib/session";

const realFetch = global.fetch;
const BACKUP = "pgdump-20261114-030000.dump";

function serve() {
  let reviewing = false;
  return fakeServer([
    ["/api/backups/status", { json: { tiers: [], overall_status: "healthy" } }],
    ["/api/backups/config-snapshots", { json: { snapshots: [] } }],
    ["/api/backups/postgres-snapshots", { json: { snapshots: [
      { filename: BACKUP, date: "2026-11-14T03:00:00Z", size_bytes: 1000, integrity: "checksummed" },
    ], total: 1 } }],
    ["/api/backups/tfstate-files", { json: { files: [] } }],
    ["/api/backups/settings", { json: {
      postgres_retention_days: 7, postgres_schedule_hours: 24, postgres_schedule_enabled: true,
      postgres_next_run: null, config_retention_days: 7, config_schedule_hours: 24,
      config_schedule_enabled: true, config_next_run: null,
    } }],
    ["/api/backups/restore/status", () => ({ json: reviewing
      ? { active: true, backup_filename: BACKUP, seconds_remaining: 3600 }
      : { active: false } })],
    ["/api/backups/restore/postgres", () => {
      reviewing = true;
      return { json: { status: "reviewing", message: `Restore from ${BACKUP} active. You have 1 hour to review.` } };
    }],
  ]);
}

async function flush() {
  await act(async () => {
    for (let n = 0; n < 50; n++) await Promise.resolve();
  });
}

beforeEach(() => {
  localStorage.clear();
  startSession("live.jwt");
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  jest.restoreAllMocks();
});

test("the review banner appears as soon as a restore starts, without a reload", async () => {
  serve();
  render(<InfraBackupPage />);
  await flush();
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  const row = screen.getByText(BACKUP).closest("tr") as HTMLElement;
  await act(async () => {
    within(row).getByRole("button", { name: "Restore" }).click();
  });
  await act(async () => {
    within(screen.getByRole("dialog")).getByRole("button", { name: "Restore" }).click();
  });
  await flush();
  expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
  expect(screen.getByText(/Restored from/)).toBeInTheDocument();
});
