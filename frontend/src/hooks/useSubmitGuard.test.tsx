import { act, renderHook } from "@testing-library/react";
import { useSubmitGuard } from "./useSubmitGuard";

test("a second run while the first is in flight does nothing", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  let finish: () => void = () => {};
  const work = jest.fn(() => new Promise<void>((r) => (finish = r)));
  let first: Promise<void> = Promise.resolve();
  act(() => {
    first = result.current.run(work);
    void result.current.run(work);
  });
  expect(work).toHaveBeenCalledTimes(1);
  expect(result.current.busy).toBe(true);
  await act(async () => {
    finish();
    await first;
  });
  expect(result.current.busy).toBe(false);
});

test("a failure releases the guard so the user can try again", async () => {
  const { result } = renderHook(() => useSubmitGuard());
  await act(async () => {
    await expect(result.current.run(() => Promise.reject(new Error("no")))).rejects.toThrow("no");
  });
  expect(result.current.busy).toBe(false);
  const again = jest.fn(() => Promise.resolve());
  await act(async () => {
    await result.current.run(again);
  });
  expect(again).toHaveBeenCalledTimes(1);
});
