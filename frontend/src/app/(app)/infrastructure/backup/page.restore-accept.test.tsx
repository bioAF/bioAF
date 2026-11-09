/**
 * The backend decides whether a restore review may still be accepted. The page keeps its Accept
 * control even when the countdown reads zero, and shows the backend's refusal as a plain message.
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
const REFUSAL = "The restore review has expired. The original database was kept.";

function serve() {
  return fakeServer([
    ["/api/backups/status", { json: { tiers: [], overall_status: "healthy" } }],
    ["/api/backups/config-snapshots", { json: { snapshots: [] } }],
    ["/api/backups/postgres-snapshots", { json: { snapshots: [] } }],
    ["/api/backups/tfstate-files", { json: { files: [] } }],
    ["/api/backups/settings", { json: {
      postgres_retention_days: 7, postgres_schedule_hours: 24, postgres_schedule_enabled: true,
      postgres_next_run: null, config_retention_days: 7, config_schedule_hours: 24,
      config_schedule_enabled: true, config_next_run: null,
    } }],
    ["/api/backups/restore/status", { json: { active: true, backup_filename: "pgdump-20261109-030000.dump", seconds_remaining: 0 } }],
    ["/api/backups/restore/accept", { status: 400, json: { detail: REFUSAL } }],
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

test("Accept at zero seconds left reaches the backend, and its refusal is shown plainly", async () => {
  const { requests } = serve();
  render(<InfraBackupPage />);
  await flush();

  const accept = screen.getByRole("button", { name: "Accept" });
  await act(async () => {
    accept.click();
  });
  const dialog = screen.getByRole("dialog");
  await act(async () => {
    within(dialog).getByRole("button", { name: "Accept" }).click();
  });
  await flush();

  expect(requests.some((r) => r.url.includes("/api/backups/restore/accept") && r.method === "POST")).toBe(true);
  expect(screen.getByText(REFUSAL)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Accept" })).toBeInTheDocument();
});
