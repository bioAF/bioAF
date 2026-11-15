/**
 * Each PostgreSQL snapshot shows whether its checksum was recorded when it was made. A backup made before
 * checksums can still be restored, but only after a confirmation that says it cannot be verified; that
 * confirmation is what sends the explicit opt-in. Other backups are requested without it.
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
const CHECKSUMMED = "pgdump-20261114-030000.dump";
const LEGACY = "pgdump-20261101-030000.dump";
const INVALID = "pgdump-20261113-030000.dump";
const UNKNOWN = "pgdump-20261112-030000.dump";

function serve() {
  return fakeServer([
    ["/api/backups/status", { json: { tiers: [], overall_status: "healthy" } }],
    ["/api/backups/config-snapshots", { json: { snapshots: [] } }],
    ["/api/backups/postgres-snapshots", { json: { snapshots: [
      { filename: CHECKSUMMED, date: "2026-11-14T03:00:00Z", size_bytes: 1000, integrity: "checksummed" },
      { filename: INVALID, date: "2026-11-13T03:00:00Z", size_bytes: 1000, integrity: "invalid" },
      { filename: UNKNOWN, date: "2026-11-12T03:00:00Z", size_bytes: 1000, integrity: "unknown" },
      { filename: LEGACY, date: "2026-11-01T03:00:00Z", size_bytes: 1000, integrity: "unverified" },
    ], total: 4 } }],
    ["/api/backups/tfstate-files", { json: { files: [] } }],
    ["/api/backups/settings", { json: {
      postgres_retention_days: 7, postgres_schedule_hours: 24, postgres_schedule_enabled: true,
      postgres_next_run: null, config_retention_days: 7, config_schedule_hours: 24,
      config_schedule_enabled: true, config_next_run: null,
    } }],
    ["/api/backups/restore/status", { json: { active: false } }],
    ["/api/backups/restore/postgres", { json: { status: "reviewing", message: "Restore active." } }],
  ]);
}

async function flush() {
  await act(async () => {
    for (let n = 0; n < 50; n++) await Promise.resolve();
  });
}

function rowFor(filename: string) {
  return screen.getByText(filename).closest("tr") as HTMLElement;
}

async function restore(filename: string) {
  await act(async () => {
    within(rowFor(filename)).getByRole("button", { name: "Restore" }).click();
  });
  return screen.getByRole("dialog");
}

function restoreBodies(requests: { url: string; method: string; body: unknown }[]) {
  return requests
    .filter((r) => r.url.includes("/api/backups/restore/postgres") && r.method === "POST")
    .map((r) => JSON.parse(String(r.body)));
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

test("each snapshot shows its checksum state", async () => {
  serve();
  render(<InfraBackupPage />);
  await flush();
  expect(within(rowFor(CHECKSUMMED)).getByText("Checksum recorded")).toBeInTheDocument();
  expect(within(rowFor(LEGACY)).getByText("Unverified (made before checksums)")).toBeInTheDocument();
  expect(within(rowFor(INVALID)).getByText("Checksum missing or damaged")).toBeInTheDocument();
  expect(within(rowFor(UNKNOWN)).getByText("Not checked")).toBeInTheDocument();
});

test("a checksummed backup is restored without the unverified opt-in", async () => {
  const { requests } = serve();
  render(<InfraBackupPage />);
  await flush();
  const dialog = await restore(CHECKSUMMED);
  expect(within(dialog).queryByText(/cannot be verified/)).toBeNull();
  await act(async () => {
    within(dialog).getByRole("button", { name: "Restore" }).click();
  });
  await flush();
  expect(restoreBodies(requests)).toEqual([{ filename: CHECKSUMMED }]);
});

test("an unverified backup is restored only through a confirmation that says so", async () => {
  const { requests } = serve();
  render(<InfraBackupPage />);
  await flush();
  const dialog = await restore(LEGACY);
  expect(within(dialog).getByText(/cannot be verified/)).toBeInTheDocument();
  expect(restoreBodies(requests)).toEqual([]);
  await act(async () => {
    within(dialog).getByRole("button", { name: "Restore unverified backup" }).click();
  });
  await flush();
  expect(restoreBodies(requests)).toEqual([{ filename: LEGACY, allow_unverified: true }]);
});
