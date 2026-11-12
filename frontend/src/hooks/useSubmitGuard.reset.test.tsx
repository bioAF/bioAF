import { act, renderHook } from "@testing-library/react";
import { useSubmitGuard } from "./useSubmitGuard";

function pending() {
  let resolve: () => void = () => {};
  let reject: (e: Error) => void = () => {};
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("reset releases the guard while the old work is still pending", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  const old = pending();
  act(() => {
    void result.current.run(() => old.promise);
  });
  expect(result.current.busy).toBe(true);

  act(() => result.current.reset());
  expect(result.current.busy).toBe(false);

  const again = jest.fn(() => new Promise<void>(() => {}));
  act(() => {
    void result.current.run(again);
  });
  expect(again).toHaveBeenCalledTimes(1);
  expect(result.current.busy).toBe(true);
});

test("an old attempt that succeeds after a reset cannot release the newer attempt", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  const old = pending();
  let oldRun: Promise<void> = Promise.resolve();
  act(() => {
    oldRun = result.current.run(() => old.promise);
  });
  act(() => result.current.reset());
  act(() => {
    void result.current.run(() => new Promise<void>(() => {}));
  });

  await act(async () => {
    old.resolve();
    await oldRun;
  });
  expect(result.current.busy).toBe(true);

  const third = jest.fn(() => Promise.resolve());
  act(() => {
    void result.current.run(third);
  });
  expect(third).not.toHaveBeenCalled();
});

test("an old attempt that fails after a reset still rejects but cannot release the newer attempt", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  const old = pending();
  let oldRun: Promise<void> = Promise.resolve();
  act(() => {
    oldRun = result.current.run(() => old.promise);
  });
  act(() => result.current.reset());
  act(() => {
    void result.current.run(() => new Promise<void>(() => {}));
  });

  await act(async () => {
    old.reject(new Error("late"));
    await expect(oldRun).rejects.toThrow("late");
  });
  expect(result.current.busy).toBe(true);
  const third = jest.fn(() => Promise.resolve());
  act(() => {
    void result.current.run(third);
  });
  expect(third).not.toHaveBeenCalled();
});

test("several resets leave only the latest attempt able to release", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  const first = pending();
  const second = pending();
  const latest = pending();
  const runs: Promise<void>[] = [];
  act(() => {
    runs.push(result.current.run(() => first.promise));
  });
  act(() => result.current.reset());
  act(() => {
    runs.push(result.current.run(() => second.promise));
  });
  act(() => result.current.reset());
  act(() => result.current.reset());
  act(() => {
    runs.push(result.current.run(() => latest.promise));
  });

  await act(async () => {
    first.resolve();
    second.resolve();
    await Promise.all(runs.slice(0, 2));
  });
  expect(result.current.busy).toBe(true);

  await act(async () => {
    latest.resolve();
    await runs[2];
  });
  expect(result.current.busy).toBe(false);
});

test("reset with nothing in flight is harmless", () => {
  const { result } = renderHook(() => useSubmitGuard());
  act(() => result.current.reset());
  expect(result.current.busy).toBe(false);
});
