/**
 * The keyed request/polling owner (plan 13). Only the current generation of a key may change what
 * the consumer sees: data, errors, loading, refresh state, or completion callbacks.
 */
import React from "react";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useResource, type ResourceLoader } from "./useResource";
import { endSession, startSession } from "@/lib/session";

type Call = {
  key: string;
  signal: AbortSignal;
  resolve: (value: string) => void;
  reject: (error: unknown) => void;
};

let calls: Call[];

/** A loader whose every read waits for the test to settle it. */
function controlledLoader(key: string): ResourceLoader<string> {
  return (signal) =>
    new Promise<string>((resolve, reject) => {
      calls.push({ key, signal, resolve, reject });
    });
}

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}

beforeEach(() => {
  calls = [];
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe("ownership by key", () => {
  test.each([
    ["the newer answer arrives first", [1, 0]],
    ["the older answer arrives first", [0, 1]],
  ])("only the current key's answer is shown when %s", async (_label, order) => {
    const { result, rerender } = renderHook(({ key }) => useResource(key, controlledLoader(key)), {
      initialProps: { key: "q=a" },
    });
    rerender({ key: "q=ab" });
    expect(calls.map((c) => c.key)).toEqual(["q=a", "q=ab"]);

    for (const i of order) {
      await settle(() => calls[i].resolve(`rows for ${calls[i].key}`));
    }

    expect(result.current.data).toBe("rows for q=ab");
    expect(result.current.loading).toBe(false);
  });

  test("a superseded key's failure and finalization change nothing", async () => {
    const { result, rerender } = renderHook(({ key }) => useResource(key, controlledLoader(key)), {
      initialProps: { key: "page=1" },
    });
    rerender({ key: "page=2" });

    await settle(() => calls[0].reject(new Error("old failure")));

    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(true);
    await settle(() => calls[1].resolve("page 2"));
    expect(result.current.data).toBe("page 2");
  });

  test("a new key does not present the previous key's data as current", async () => {
    const { result, rerender } = renderHook(({ key }) => useResource(key, controlledLoader(key)), {
      initialProps: { key: "status=active" },
    });
    await settle(() => calls[0].resolve("active rows"));
    expect(result.current.data).toBe("active rows");

    rerender({ key: "status=archived" });

    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(true);
  });

  test("changing the key aborts the previous read", () => {
    const { rerender } = renderHook(({ key }) => useResource(key, controlledLoader(key)), {
      initialProps: { key: "a" },
    });
    rerender({ key: "b" });
    expect(calls[0].signal.aborted).toBe(true);
    expect(calls[1].signal.aborted).toBe(false);
  });

  test("an initial failure is an error, not an empty result", async () => {
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].reject(new Error("500")));

    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toEqual(new Error("500"));
    expect(result.current.stale).toBe(false);
  });

  test("a deliberate cancellation is not reported as a failure", async () => {
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].reject(new DOMException("aborted", "AbortError")));
    expect(result.current.error).toBeNull();
  });
});

describe("refreshing the same key", () => {
  test("keeps the last data visible while refreshing, then replaces it", async () => {
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].resolve("v1"));

    let refreshed: Promise<void>;
    act(() => {
      refreshed = result.current.refresh();
    });

    expect(result.current.data).toBe("v1");
    expect(result.current.refreshing).toBe(true);
    expect(result.current.loading).toBe(false);

    await settle(() => calls[1].resolve("v2"));
    await act(async () => {
      await refreshed;
    });
    expect(result.current.data).toBe("v2");
    expect(result.current.refreshing).toBe(false);
  });

  test("a failed refresh keeps the data, marked stale, until a refresh recovers", async () => {
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].resolve("v1"));

    act(() => {
      void result.current.refresh();
    });
    await settle(() => calls[1].reject(new Error("502")));

    expect(result.current.data).toBe("v1");
    expect(result.current.stale).toBe(true);
    expect(result.current.error).toEqual(new Error("502"));

    act(() => {
      void result.current.refresh();
    });
    await settle(() => calls[2].resolve("v3"));
    expect(result.current.stale).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.data).toBe("v3");
  });

  test("a superseding refresh wins even when the earlier read answers last", async () => {
    const onSuccess = jest.fn();
    const { result } = renderHook(() => useResource("k", controlledLoader("k"), { onSuccess }));
    await settle(() => calls[0].resolve("v1"));

    act(() => {
      void result.current.refresh();
    });
    act(() => {
      void result.current.refresh();
    });
    expect(calls[1].signal.aborted).toBe(true);

    await settle(() => calls[2].resolve("after the mutation"));
    await settle(() => calls[1].resolve("before the mutation"));

    expect(result.current.data).toBe("after the mutation");
    expect(onSuccess.mock.calls.map((c) => c[0])).toEqual(["v1", "after the mutation"]);
  });

  test("a local update supersedes the read in flight", async () => {
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].resolve("3 unread"));
    act(() => {
      void result.current.refresh();
    });

    act(() => result.current.mutate("2 unread"));
    await settle(() => calls[1].resolve("3 unread"));

    expect(result.current.data).toBe("2 unread");
  });
});

describe("disable, unmount, and session end", () => {
  test("disabling aborts the read and drops its answer; re-enabling reads again", async () => {
    const { result, rerender } = renderHook(
      ({ key }: { key: string | null }) => useResource(key, controlledLoader(String(key))),
      { initialProps: { key: "k" as string | null } },
    );
    rerender({ key: null });
    expect(calls[0].signal.aborted).toBe(true);
    await settle(() => calls[0].resolve("late"));
    expect(result.current.data).toBeUndefined();
    expect(result.current.loading).toBe(false);

    rerender({ key: "k" });
    expect(calls).toHaveLength(2);
    await settle(() => calls[1].resolve("fresh"));
    expect(result.current.data).toBe("fresh");
  });

  test("unmounting aborts the read and runs no callback", async () => {
    const onSuccess = jest.fn();
    const { unmount } = renderHook(() => useResource("k", controlledLoader("k"), { onSuccess }));
    unmount();
    expect(calls[0].signal.aborted).toBe(true);
    await settle(() => calls[0].resolve("late"));
    expect(onSuccess).not.toHaveBeenCalled();
  });

  test("ending the session aborts the read and clears what was shown", async () => {
    startSession("a.jwt");
    const { result } = renderHook(() => useResource("k", controlledLoader("k")));
    await settle(() => calls[0].resolve("user A's rows"));
    act(() => {
      void result.current.refresh();
    });

    act(() => endSession());
    await settle(() => calls[1].resolve("user A's late rows"));

    expect(calls[1].signal.aborted).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  test("React's development remount commits only the final setup's answer", async () => {
    const onSuccess = jest.fn();
    const wrapper = ({ children }: { children: React.ReactNode }) => <React.StrictMode>{children}</React.StrictMode>;
    const { result } = renderHook(() => useResource("k", controlledLoader("k"), { onSuccess }), { wrapper });

    expect(calls.length).toBe(2);
    expect(calls[0].signal.aborted).toBe(true);
    await settle(() => calls[0].resolve("first setup"));
    await settle(() => calls[1].resolve("final setup"));

    expect(result.current.data).toBe("final setup");
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
});

describe("polling", () => {
  test("the next read waits for the previous one: slow reads never overlap", async () => {
    jest.useFakeTimers();
    renderHook(() => useResource("k", controlledLoader("k"), { poll: 1000 }));
    expect(calls).toHaveLength(1);

    await act(async () => {
      jest.advanceTimersByTime(5000);
    });
    expect(calls).toHaveLength(1);

    await settle(() => calls[0].resolve("v1"));
    await act(async () => {
      jest.advanceTimersByTime(999);
    });
    expect(calls).toHaveLength(1);
    await act(async () => {
      jest.advanceTimersByTime(1);
    });
    expect(calls).toHaveLength(2);
  });

  test("polling continues after a failed read and recovers on the next tick", async () => {
    jest.useFakeTimers();
    const { result } = renderHook(() => useResource("k", controlledLoader("k"), { poll: 1000 }));
    await settle(() => calls[0].resolve("v1"));
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    await settle(() => calls[1].reject(new Error("blip")));
    expect(result.current.stale).toBe(true);

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    await settle(() => calls[2].resolve("v3"));
    expect(result.current.stale).toBe(false);
    expect(result.current.data).toBe("v3");
  });

  test("turning polling off (a terminal state) clears the timer", async () => {
    jest.useFakeTimers();
    const { rerender } = renderHook(
      ({ poll }: { poll: number | false }) => useResource("k", controlledLoader("k"), { poll }),
      { initialProps: { poll: 1000 as number | false } },
    );
    await settle(() => calls[0].resolve("done"));
    rerender({ poll: false });

    await act(async () => {
      jest.advanceTimersByTime(10_000);
    });
    expect(calls).toHaveLength(1);
  });

  test("a manual refresh during a poll read supersedes it and the timer resumes after", async () => {
    jest.useFakeTimers();
    const { result } = renderHook(() => useResource("k", controlledLoader("k"), { poll: 1000 }));
    await settle(() => calls[0].resolve("v1"));
    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(calls).toHaveLength(2);

    act(() => {
      void result.current.refresh();
    });
    expect(calls[1].signal.aborted).toBe(true);
    await settle(() => calls[2].resolve("manual"));
    await settle(() => calls[1].resolve("poll"));
    expect(result.current.data).toBe("manual");

    await act(async () => {
      jest.advanceTimersByTime(1000);
    });
    expect(calls).toHaveLength(4);
  });

  test("unmounting stops polling", async () => {
    jest.useFakeTimers();
    const { unmount } = renderHook(() => useResource("k", controlledLoader("k"), { poll: 1000 }));
    await settle(() => calls[0].resolve("v1"));
    unmount();
    await act(async () => {
      jest.advanceTimersByTime(10_000);
    });
    expect(calls).toHaveLength(1);
  });

  test("the loader in use is the latest one, without a refetch when only its identity changes", async () => {
    let version = "one";
    const { result, rerender } = renderHook(() =>
      useResource("k", async () => `loaded by ${version}`),
    );
    await waitFor(() => expect(result.current.data).toBe("loaded by one"));
    version = "two";
    rerender();
    expect(result.current.data).toBe("loaded by one");
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.data).toBe("loaded by two");
  });
});
