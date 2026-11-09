/**
 * The tfstate download used `window.open` on a relative URL, which sends no session Bearer header
 * and so was refused by the backend's auth middleware (plan 13). It is now an authenticated blob
 * download through the shared client.
 */
import { render, screen, fireEvent, waitFor } from "@/testing/renderWithProviders";
import { fakeServer } from "@/testing/fakeServer";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import InfraBackupPage from "./page";
import { getToken } from "@/lib/auth";
import { startSession } from "@/lib/session";

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;
const STATE_FILE = "envs/prod/default.tfstate";

const BASE_ROUTES: Array<[string, { json: unknown }]> = [
  ["/api/backups/status", { json: { tiers: [], overall_status: "healthy" } }],
  ["/api/backups/config-snapshots", { json: { snapshots: [] } }],
  ["/api/backups/postgres-snapshots", { json: { snapshots: [] } }],
  ["/api/backups/tfstate-files", { json: { files: [{ name: STATE_FILE, size_bytes: 10, updated: null }] } }],
  ["/api/backups/settings", { json: {
    postgres_retention_days: 7, postgres_schedule_hours: 24, postgres_schedule_enabled: true,
    postgres_next_run: null, config_retention_days: 7, config_schedule_hours: 24,
    config_schedule_enabled: true, config_next_run: null,
  } }],
  ["/api/backups/restore/status", { json: { active: false } }],
];

let saved: string[];
let windowOpen: jest.SpyInstance;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.lab.org";
  localStorage.clear();
  startSession("live.jwt");
  saved = [];
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:tfstate");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
  windowOpen = jest.spyOn(window, "open").mockImplementation(() => null);
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockNavigateToLogin.mockReset();
});

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  jest.restoreAllMocks();
});

async function clickDownload() {
  render(<InfraBackupPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Download" }));
}

test("downloads the state file with the session credential from the configured API", async () => {
  const { requests } = fakeServer([
    ...BASE_ROUTES,
    ["/api/backups/tfstate-download/", {
      text: '{"version": 4}',
      headers: { "Content-Disposition": 'attachment; filename="default.tfstate"' },
    }],
  ]);

  await clickDownload();

  await waitFor(() => expect(saved).toEqual(["default.tfstate"]));
  const download = requests.find((r) => r.url.includes("tfstate-download"))!;
  expect(download.url).toBe(
    `https://api.lab.org/api/backups/tfstate-download/${encodeURIComponent(STATE_FILE)}`,
  );
  expect(download.headers.Authorization).toBe("Bearer live.jwt");
  expect(windowOpen).not.toHaveBeenCalled();
});

test("a refused download is shown on the page and saves nothing", async () => {
  fakeServer([
    ...BASE_ROUTES,
    ["/api/backups/tfstate-download/", { status: 403, json: { detail: "Permission denied" } }],
  ]);

  await clickDownload();

  expect(await screen.findByText(/Permission denied/)).toBeInTheDocument();
  expect(saved).toEqual([]);
  expect(getToken()).toBe("live.jwt");
});

test("an expired session on download ends the session", async () => {
  fakeServer([
    ...BASE_ROUTES,
    ["/api/backups/tfstate-download/", { status: 401, json: { detail: "Unauthorized" } }],
  ]);

  await clickDownload();

  await waitFor(() => expect(mockNavigateToLogin).toHaveBeenCalled());
  expect(getToken()).toBeNull();
  expect(saved).toEqual([]);
});
