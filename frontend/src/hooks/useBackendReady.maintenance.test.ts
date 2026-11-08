/**
 * An upgrade in progress is not "the server cannot be reached" (plan 9). At boot the splash says
 * the upgrade is being finished; in a tab that was already working, maintenance is reported by the
 * API client and the hook polls until the backend is ready again, then clears it WITHOUT reloading,
 * so whatever the user typed is still there.
 */
import { renderHook, act } from "@testing-library/react";

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import { useBackendReady } from "./useBackendReady";
import { reportMaintenance, clearMaintenance } from "@/lib/maintenance";

const up = () => ({ ok: true, status: 200, json: async () => ({ status: "ok" }) });
const upgrading = () => ({
  ok: false,
  status: 503,
  json: async () => ({ status: "maintenance", reason: "upgrade_in_progress", upgrade: { step: "migrate" } }),
});

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function nextPoll() {
  await act(async () => {
    jest.advanceTimersByTime(2000);
  });
  await settle();
}

beforeEach(() => {
  mockFetch.mockReset();
  sessionStorage.clear();
  clearMaintenance();
  jest.spyOn(console, "error").mockImplementation(() => {});
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

it("at boot, reports maintenance rather than unreachable, however long it lasts", async () => {
  mockFetch.mockResolvedValue(upgrading());
  const { result } = renderHook(() => useBackendReady());
  await settle();
  for (let i = 0; i < 5; i++) await nextPoll();

  expect(result.current.ready).toBe(false);
  expect(result.current.unreachable).toBe(false);
  expect(result.current.maintenance?.reason).toBe("upgrade_in_progress");
});

it("in an open tab, shows maintenance and clears it when the backend is back, without a reload", async () => {
  sessionStorage.setItem("bioaf_backend_ready", "true");
  mockFetch.mockResolvedValue(upgrading());
  const { result } = renderHook(() => useBackendReady());
  await settle();
  expect(result.current.ready).toBe(true);
  expect(mockFetch).not.toHaveBeenCalled();

  await act(async () => {
    reportMaintenance({ status: "maintenance", reason: "upgrade_in_progress" });
  });
  await settle();
  expect(result.current.ready).toBe(true); // the page stays mounted
  expect(result.current.maintenance?.reason).toBe("upgrade_in_progress");
  expect(mockFetch).toHaveBeenCalled(); // it now polls readiness

  mockFetch.mockResolvedValue(up());
  await nextPoll();
  expect(result.current.maintenance).toBeNull();
  expect(result.current.ready).toBe(true);
  expect(sessionStorage.getItem("bioaf_backend_ready")).toBe("true");

  const calls = mockFetch.mock.calls.length;
  await nextPoll();
  expect(mockFetch.mock.calls.length).toBe(calls); // polling stopped
});
