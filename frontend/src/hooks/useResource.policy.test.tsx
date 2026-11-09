/**
 * Polling policy owned by the hook. A feature declares when its resource is active (from the
 * current data and error) and which failures are expected; the hook decides whether to schedule
 * the next read. No caller mirrors the hook's data or errors into its own flags.
 */
import { renderHook, act } from "@testing-library/react";
import { useResource, type ResourceLoader } from "./useResource";

type Job = { status: "running" | "done" };
type Call = { signal: AbortSignal; resolve: (value: Job) => void; reject: (error: unknown) => void };
let calls: Call[];

const loader: ResourceLoader<Job> = (signal) =>
  new Promise<Job>((resolve, reject) => {
    calls.push({ signal, resolve, reject });
  });

const forbidden = Object.assign(new Error("Forbidden"), { status: 403 });
const upstream = Object.assign(new Error("Bad gateway"), { status: 502 });
const isForbidden = (e: unknown) => (e as { status?: number })?.status === 403;

/** Poll every second while the job runs or the last read failed; stop when it is done. */
const whileRunning = ({ data, error }: { data: Job | undefined; error: unknown }) =>
  error !== null || data?.status === "running" ? 1000 : false;

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}
async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  calls = [];
});
afterEach(() => jest.useRealTimers());

function render(key: string | null = "job:1", options = {}) {
  return renderHook(
    ({ k }) => useResource(k, loader, { poll: whileRunning, expected: isForbidden, ...options }),
    { initialProps: { k: key } },
  );
}

test("an active answer keeps polling; a terminal answer stops it", async () => {
  const { result } = render();
  await settle(() => calls[0].resolve({ status: "running" }));
  await tick(1000);
  expect(calls).toHaveLength(2);

  await settle(() => calls[1].resolve({ status: "done" }));
  await tick(10_000);
  expect(calls).toHaveLength(2);
  expect(result.current.data).toEqual({ status: "done" });
});

test("a failed first read is an error and is re-checked until it recovers", async () => {
  const { result } = render();
  await settle(() => calls[0].reject(upstream));
  expect(result.current.error).toBe(upstream);
  expect(result.current.stale).toBe(false);

  await tick(1000);
  expect(calls).toHaveLength(2);
  await settle(() => calls[1].resolve({ status: "done" }));
  expect(result.current.error).toBeNull();
  await tick(10_000);
  expect(calls).toHaveLength(2);
});

test("a failed refresh keeps the last answer, marked stale, and keeps checking", async () => {
  const { result } = render();
  await settle(() => calls[0].resolve({ status: "running" }));
  await tick(1000);
  await settle(() => calls[1].reject(upstream));
  expect(result.current.stale).toBe(true);
  expect(result.current.data).toEqual({ status: "running" });

  await tick(1000);
  expect(calls).toHaveLength(3);
});

test("an expected failure is a denial, not an error, and stops polling", async () => {
  const onError = jest.fn();
  const { result } = render("job:1", { onError });
  await settle(() => calls[0].reject(forbidden));
  expect(result.current.denied).toBe(true);
  expect(result.current.error).toBeNull();
  expect(result.current.loading).toBe(false);
  expect(onError).not.toHaveBeenCalled();

  await tick(10_000);
  expect(calls).toHaveLength(1);
});

test("a denial after a transient failure clears the failure and stops polling", async () => {
  const { result } = render();
  await settle(() => calls[0].reject(upstream));
  await tick(1000);
  await settle(() => calls[1].reject(forbidden));

  expect(result.current.error).toBeNull();
  expect(result.current.denied).toBe(true);
  await tick(10_000);
  expect(calls).toHaveLength(2);
});

test("a manual refresh after a denial reads again and can recover", async () => {
  const { result } = render();
  await settle(() => calls[0].reject(forbidden));
  await act(async () => {
    void result.current.refresh();
  });
  await settle(() => calls[1].resolve({ status: "running" }));
  expect(result.current.denied).toBe(false);
  expect(result.current.data).toEqual({ status: "running" });
});

test("a key change starts over: the previous key's denial and data do not carry", async () => {
  const { result, rerender } = render("job:1");
  await settle(() => calls[0].reject(forbidden));
  rerender({ k: "job:2" });
  expect(result.current.denied).toBe(false);
  expect(result.current.loading).toBe(true);
  await settle(() => calls[1].resolve({ status: "running" }));
  await tick(1000);
  expect(calls).toHaveLength(3);
});

test("the policy sees only the current key's answer", async () => {
  const { rerender } = render("job:1");
  await settle(() => calls[0].resolve({ status: "running" }));
  rerender({ k: "job:2" });
  // job:2 has no answer yet; job:1's running answer must not schedule a poll for it.
  await tick(10_000);
  expect(calls).toHaveLength(2);
});

test("every read gets a signal that aborts when the read is superseded", async () => {
  const { rerender } = render("job:1");
  const first = calls[0].signal;
  expect(first.aborted).toBe(false);
  rerender({ k: "job:2" });
  expect(first.aborted).toBe(true);
});
