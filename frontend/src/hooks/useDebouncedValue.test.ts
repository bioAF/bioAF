import { renderHook, act } from "@testing-library/react";
import { useDebouncedValue } from "./useDebouncedValue";

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe("useDebouncedValue", () => {
  it("returns the initial value immediately", () => {
    const { result } = renderHook(() => useDebouncedValue("a", 1000));
    expect(result.current).toBe("a");
  });

  it("updates only after the delay elapses", () => {
    const { result, rerender } = renderHook(({ v }) => useDebouncedValue(v, 1000), {
      initialProps: { v: "a" },
    });
    rerender({ v: "ab" });
    expect(result.current).toBe("a");
    act(() => jest.advanceTimersByTime(999));
    expect(result.current).toBe("a");
    act(() => jest.advanceTimersByTime(1));
    expect(result.current).toBe("ab");
  });

  it("resets the timer on rapid changes so only the last value lands", () => {
    const { result, rerender } = renderHook(({ v }) => useDebouncedValue(v, 1000), {
      initialProps: { v: "a" },
    });
    rerender({ v: "ab" });
    act(() => jest.advanceTimersByTime(500));
    rerender({ v: "abc" });
    act(() => jest.advanceTimersByTime(500));
    expect(result.current).toBe("a");
    act(() => jest.advanceTimersByTime(500));
    expect(result.current).toBe("abc");
  });
});
