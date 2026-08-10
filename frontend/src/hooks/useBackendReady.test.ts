import { renderHook, act, waitFor } from "@testing-library/react";

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

import { useBackendReady } from "./useBackendReady";

const up = () => ({ ok: true, json: async () => ({ status: "ok" }) });
const down = () => ({ ok: false, status: 500, json: async () => ({ detail: "probe down" }) });

/** Let the promise chain inside one `check()` settle. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Advance one poll interval and let the resulting check settle. */
async function nextPoll() {
  await act(async () => {
    jest.advanceTimersByTime(2000);
  });
  await settle();
}

beforeEach(() => {
  mockFetch.mockReset();
  sessionStorage.clear();
  jest.spyOn(console, "error").mockImplementation(() => {});
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe("useBackendReady", () => {
  it("is ready immediately when the backend answers on the first check", async () => {
    mockFetch.mockResolvedValue(up());
    const { result } = renderHook(() => useBackendReady());
    await settle();

    expect(result.current.ready).toBe(true);
    expect(result.current.unreachable).toBe(false);
  });

  /**
   * The defect this locks: a failing probe produced a branded spinner forever.
   * There was no signal any caller could use to say so, which is why the splash
   * had no message, no retry and nothing to focus. A short grace period keeps a
   * normal slow start from flashing an alarming message.
   */
  it("reports the backend unreachable once the grace period is spent, and keeps polling", async () => {
    mockFetch.mockResolvedValue(down());
    const { result } = renderHook(() => useBackendReady());
    await settle();

    expect(result.current.ready).toBe(false);
    expect(result.current.unreachable).toBe(false); // still inside the grace period

    await nextPoll();
    await nextPoll();
    await nextPoll();

    expect(result.current.unreachable).toBe(true);
    expect(result.current.ready).toBe(false);

    // Still trying. Giving up entirely would be the opposite defect.
    const callsSoFar = mockFetch.mock.calls.length;
    await nextPoll();
    expect(mockFetch.mock.calls.length).toBeGreaterThan(callsSoFar);
  });

  it("checks straight away when the user asks it to, without waiting out the interval", async () => {
    mockFetch.mockResolvedValue(down());
    const { result } = renderHook(() => useBackendReady());
    await settle();
    const before = mockFetch.mock.calls.length;

    await act(async () => {
      result.current.retryNow();
    });
    await settle();

    expect(mockFetch.mock.calls.length).toBeGreaterThan(before);
  });

  /**
   * Recovering after a wait reloads the document rather than just flipping
   * `ready`. That is deliberate: the sibling loaders in the same layout
   * (permissions, components) already ran and failed while the backend was
   * down, and their effects will not re-run without a remount. It is safe
   * because the splash covers the entire app whenever this can fire, so there is
   * nothing on screen to discard.
   */
  it("stops polling and records readiness once the backend comes back", async () => {
    // Asserted through the observable contract rather than by spying on
    // `location.reload`: jsdom defines both `location` and `reload`
    // non-configurably, and reshaping production code to add a seam purely for
    // that would be the tail wagging the dog. Persisting readiness and stopping
    // the poll are what callers actually depend on.
    mockFetch.mockResolvedValue(down());
    renderHook(() => useBackendReady());
    await settle();
    expect(sessionStorage.getItem("bioaf_backend_ready")).toBeNull();

    mockFetch.mockResolvedValue(up());
    await nextPoll();

    expect(sessionStorage.getItem("bioaf_backend_ready")).toBe("true");

    const callsAtRecovery = mockFetch.mock.calls.length;
    await nextPoll();
    expect(mockFetch.mock.calls.length).toBe(callsAtRecovery);
  });

  /**
   * The probe runs every 2s indefinitely. Logging each failure would bury the
   * console in a repeating line and make the real first failure hard to find.
   */
  it("logs the failure once rather than on every poll", async () => {
    mockFetch.mockResolvedValue(down());
    renderHook(() => useBackendReady());
    await settle();
    await nextPoll();
    await nextPoll();
    await nextPoll();
    await nextPoll();

    const bioafLogs = (console.error as jest.Mock).mock.calls.filter((c) =>
      String(c[0]).includes("[bioAF]"),
    );
    expect(bioafLogs).toHaveLength(1);
  });

  it("does not re-check once this tab has already confirmed readiness", async () => {
    sessionStorage.setItem("bioaf_backend_ready", "true");
    mockFetch.mockResolvedValue(down());
    const { result } = renderHook(() => useBackendReady());
    await settle();

    expect(result.current.ready).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
