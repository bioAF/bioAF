/**
 * Owner review: Install enabled status polling while /execute was still pending, so the previous
 * upgrade's terminal status stopped the poll before the new upgrade started, and the eventual
 * execute success never restarted it. Observation now starts when /execute succeeds, and a
 * terminal status that is still the previous upgrade's is not taken as this one's outcome.
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

const CHECK = { current_version: "0.7.3", latest_version: "0.7.4", update_available: true, changelog: null, release_url: null };
const PREVIOUS = { status: "completed", step: "done", from_version: "0.7.2", to_version: "0.7.3", started_at: "2026-10-01T00:00:00Z" };

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let statusReads: Pending[];
let execute: Pending;

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
  await act(async () => fn());
  await flush();
}

beforeEach(() => {
  jest.useFakeTimers();
  statusReads = [];
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockImplementation(() => new Promise((resolve, reject) => (execute = { resolve, reject })));
  let initial = true;
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/upgrades/check")) return Promise.resolve(CHECK);
    if (url === "/api/upgrades/history") return Promise.resolve({ upgrades: [] });
    if (url === "/api/upgrades/status") {
      if (initial) {
        initial = false;
        return Promise.resolve(PREVIOUS);
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

test("status is not read while /execute is pending, and observation starts when it succeeds", async () => {
  render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  await tick(10_000);
  expect(statusReads).toHaveLength(0);
  expect(screen.getByText(/Updating to 0\.7\.4/)).toBeInTheDocument();

  await settle(() => execute.resolve({ upgrade_id: 2, status: "in_progress" }));
  expect(statusReads).toHaveLength(1);
});

test("the previous upgrade's terminal status does not end observation of the new one", async () => {
  render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  await settle(() => execute.resolve({ upgrade_id: 2, status: "in_progress" }));

  // The host agent has not picked up the new upgrade yet.
  await settle(() => statusReads[0].resolve(PREVIOUS));
  expect(screen.getByText(/Updating to 0\.7\.4/)).toBeInTheDocument();

  await tick(3_000);
  expect(statusReads).toHaveLength(2);
  await settle(() =>
    statusReads[1].resolve({ status: "in_progress", step: "build", to_version: "0.7.4", started_at: "2026-11-09T00:00:00Z" }),
  );
  await tick(3_000);
  await settle(() =>
    statusReads[2].resolve({ status: "failed", error: "build failed", to_version: "0.7.4", started_at: "2026-11-09T00:00:00Z" }),
  );
  expect(screen.getByText(/Update failed: build failed/)).toBeInTheDocument();
  await tick(30_000);
  expect(statusReads).toHaveLength(3);
});

test("a failed /execute stops without reading status", async () => {
  render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  await settle(() => execute.reject(new Error("Agent unavailable")));
  await tick(10_000);
  expect(statusReads).toHaveLength(0);
  expect(screen.getByText(/Agent unavailable/)).toBeInTheDocument();
});

test("installing again after a failed upgrade observes the new upgrade to its own outcome", async () => {
  const FAILED = { status: "failed", error: "build failed", to_version: "0.7.4", started_at: "2026-11-09T00:00:00Z" };
  render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  await settle(() => execute.resolve({ upgrade_id: 2, status: "in_progress" }));
  await settle(() => statusReads[0].resolve(FAILED));
  expect(screen.getByText(/Update failed: build failed/)).toBeInTheDocument();
  await tick(30_000);
  expect(statusReads).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  expect(screen.queryByText(/Update failed/)).not.toBeInTheDocument();
  await settle(() => execute.resolve({ upgrade_id: 3, status: "in_progress" }));
  expect(statusReads).toHaveLength(2);
  // Still the failed attempt's status: not this upgrade's outcome.
  await settle(() => statusReads[1].resolve(FAILED));
  expect(screen.getByText(/Updating to 0\.7\.4/)).toBeInTheDocument();
  expect(screen.queryByText(/Update failed/)).not.toBeInTheDocument();

  await tick(3_000);
  await settle(() =>
    statusReads[2].resolve({ status: "completed", step: "done", to_version: "0.7.4", started_at: "2026-11-09T01:00:00Z" }),
  );
  expect(screen.queryByText(/Updating to 0\.7\.4/)).not.toBeInTheDocument();
  await tick(30_000);
  expect(statusReads).toHaveLength(3);
});

test("a status read in flight carries a signal that aborts when the page closes", async () => {
  const { unmount } = render(<SettingsInfoPage />);
  await flush();
  fireEvent.click(screen.getByRole("button", { name: "Install Update" }));
  await flush();
  await settle(() => execute.resolve({ upgrade_id: 2, status: "in_progress" }));
  const read = mockGet.mock.calls.filter(([url]) => url === "/api/upgrades/status").at(-1)!;
  expect(read[1]?.signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(read[1].signal.aborted).toBe(true);
});
