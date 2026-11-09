/**
 * A refresh function kept past unmount (e.g. called when an awaited mutation finishes after
 * navigation) must not start work for a consumer that no longer exists (owner review).
 */
import { renderHook, act } from "@testing-library/react";
import { useResource } from "./useResource";

test("a saved refresh called after unmount starts no request and runs no callback", async () => {
  const load = jest.fn(async () => "rows");
  const onSuccess = jest.fn();
  const { result, unmount } = renderHook(() => useResource("k", load, { onSuccess }));
  await act(async () => {});
  const { refresh, mutate } = result.current;
  load.mockClear();
  onSuccess.mockClear();

  unmount();
  await refresh();
  mutate("local");

  expect(load).not.toHaveBeenCalled();
  expect(onSuccess).not.toHaveBeenCalled();
});
