/**
 * Deployment progress polled with setInterval: reads overlapped, a slow older read could land
 * after a newer one, a read outstanding when polling was disabled still updated the view, and
 * failures were swallowed so they looked like "no deployment".
 */
import { renderHook, act } from "@testing-library/react";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
import { useDeploymentProgress, type DeploymentProgress } from "./useDeploymentProgress";

const mockGet = api.get as jest.Mock;
type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void; signal?: AbortSignal };
let pending: Pending[];

function progress(overrides: Partial<DeploymentProgress>): DeploymentProgress {
  return {
    active: true, status: "applying", phase: "compute", resources_completed: 1, resources_total: 4,
    completed_resources: [], planned_resources: [], error_message: null, run_id: 7, ...overrides,
  };
}

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (_url: string, controls?: { signal?: AbortSignal }) =>
      new Promise((resolve, reject) => pending.push({ resolve, reject, signal: controls?.signal })),
  );
});

afterEach(() => {
  jest.useRealTimers();
});

test("a slow read is never overlapped by the next poll", async () => {
  renderHook(() => useDeploymentProgress(true, 1000));
  await act(async () => {
    jest.advanceTimersByTime(5000);
  });
  expect(mockGet).toHaveBeenCalledTimes(1);

  await settle(() => pending[0].resolve(progress({})));
  await act(async () => {
    jest.advanceTimersByTime(1000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("a refetch's answer is not replaced by the older poll read answering later", async () => {
  const { result } = renderHook(() => useDeploymentProgress(true, 1000));
  await settle(() => pending[0].resolve(progress({ resources_completed: 1 })));
  await act(async () => {
    jest.advanceTimersByTime(1000);
  });

  act(() => {
    void result.current.refetch();
  });
  await settle(() => pending[2].resolve(progress({ active: false, status: "completed", resources_completed: 4 })));
  await settle(() => pending[1].resolve(progress({ resources_completed: 2 })));

  expect(result.current.status).toBe("completed");
  expect(result.current.active).toBe(false);
});

test("a read outstanding when polling is disabled does not update the view", async () => {
  const { result, rerender } = renderHook(({ enabled }) => useDeploymentProgress(enabled, 1000), {
    initialProps: { enabled: true },
  });
  rerender({ enabled: false });
  expect(pending[0].signal?.aborted).toBe(true);

  await settle(() => pending[0].resolve(progress({ status: "applying" })));

  expect(result.current.active).toBe(false);
  expect(result.current.status).toBeNull();
  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(1);
});

test("a failed read is reported, distinct from no deployment, and keeps the last known progress", async () => {
  const { result } = renderHook(() => useDeploymentProgress(true, 1000));
  await settle(() => pending[0].resolve(progress({ resources_completed: 2 })));
  await act(async () => {
    jest.advanceTimersByTime(1000);
  });
  await settle(() => pending[1].reject(new Error("502")));

  expect(result.current.failed).toBe(true);
  expect(result.current.active).toBe(true);
  expect(result.current.resources_completed).toBe(2);

  await act(async () => {
    jest.advanceTimersByTime(1000);
  });
  await settle(() => pending[2].resolve(progress({ resources_completed: 3 })));
  expect(result.current.failed).toBe(false);
});

test("a failure before any answer is reported, not read as an idle stack", async () => {
  const { result } = renderHook(() => useDeploymentProgress(true, 1000));
  await settle(() => pending[0].reject(new Error("502")));
  expect(result.current.failed).toBe(true);
  expect(result.current.loading).toBe(false);
});

test("a new interval takes effect for the next poll", async () => {
  const { rerender } = renderHook(({ ms }) => useDeploymentProgress(true, ms), { initialProps: { ms: 1000 } });
  await settle(() => pending[0].resolve(progress({})));
  rerender({ ms: 5000 });
  await act(async () => {
    jest.advanceTimersByTime(4999);
  });
  expect(mockGet).toHaveBeenCalledTimes(1);
  await act(async () => {
    jest.advanceTimersByTime(1);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
});
