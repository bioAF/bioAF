/**
 * Upgrade status polling: reads every 3 s that never overlap, keep going through the
 * restart (failed reads are expected then, and are said, not swallowed), and stop at the
 * terminal status with the version and history refreshed.
 */
import { render, screen, act, fireEvent } from "@testing-library/react";

jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/info",
  useRouter: () => ({ push: jest.fn() }),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import SettingsInfoPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const before = { current_version: "0.7.3", latest_version: "0.7.4", update_available: true, changelog: null, release_url: null };
const after = { current_version: "0.7.4", latest_version: "0.7.4", update_available: false, changelog: null, release_url: null };

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let statusReads: Pending[];
let upgraded: boolean;

async function flush() {
  await act(async () => {
    for (let n = 0; n < 20; n++) await Promise.resolve();
  });
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
  await flush();
}

async function settle(fn: () => void) {
  await act(async () => {
    fn();
  });
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  statusReads = [];
  upgraded = false;
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  let initialStatusRead = true;
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/upgrades/check") return Promise.resolve(upgraded ? after : before);
    if (url === "/api/upgrades/history") {
      return Promise.resolve({ upgrades: upgraded ? [{ id: 1, from_version: "0.7.3", to_version: "0.7.4", status: "completed", started_at: "2026-11-01T00:00:00Z", completed_at: "2026-11-01T00:10:00Z", error: null }] : [] });
    }
    if (url === "/api/upgrades/status") {
      if (initialStatusRead) {
        initialStatusRead = false;
        return Promise.resolve({ status: "idle" });
      }
      return new Promise((resolve, reject) => statusReads.push({ resolve, reject }));
    }
    return Promise.resolve({});
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

async function install() {
  render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
}

test("status reads never overlap and survive the restart until the upgrade completes", async () => {
  await install();
  const first = statusReads.length;
  expect(first).toBeGreaterThanOrEqual(1);

  // A slow read: no new read starts while it is outstanding.
  await tick(30_000);
  expect(statusReads.length).toBe(first);

  // The backend goes away while it restarts.
  await settle(() => statusReads[statusReads.length - 1].reject(new TypeError("Failed to fetch")));
  expect(screen.getByText(/Updating to 0\.7\.4/)).toBeInTheDocument();
  expect(screen.getByTestId("upgrade-status-unavailable")).toBeInTheDocument();

  await tick(3_000);
  expect(statusReads.length).toBe(first + 1);
  await settle(() => statusReads[statusReads.length - 1].resolve({ status: "in_progress", step: "migrate", to_version: "0.7.4" }));
  expect(screen.queryByTestId("upgrade-status-unavailable")).not.toBeInTheDocument();

  upgraded = true;
  await tick(3_000);
  await settle(() => statusReads[statusReads.length - 1].resolve({ status: "completed", step: "done", to_version: "0.7.4" }));

  expect(screen.queryByText(/Updating to/)).not.toBeInTheDocument();
  expect(screen.getAllByText("0.7.4").length).toBeGreaterThan(0);
  const count = statusReads.length;
  await tick(30_000);
  expect(statusReads.length).toBe(count);
});

test("a failed upgrade stops polling and shows the failure", async () => {
  await install();
  await settle(() => statusReads[statusReads.length - 1].resolve({ status: "failed", error: "migrate step failed" }));

  expect(screen.getByText(/Update failed: migrate step failed/)).toBeInTheDocument();
  const count = statusReads.length;
  await tick(30_000);
  expect(statusReads.length).toBe(count);
});
