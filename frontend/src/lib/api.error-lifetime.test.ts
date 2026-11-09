/**
 * An error response's body is read after the response arrives, so the session can end while it
 * is being read. Ownership is checked again once the body is read: a cancelled read settles as a
 * cancellation, and a late body cannot apply the status policy (maintenance, expiry) to the
 * next session.
 */
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import { api, isAbortError } from "@/lib/api";
import { endSession, startSession } from "@/lib/session";
import { clearMaintenance, getMaintenance } from "@/lib/maintenance";

const realFetch = global.fetch;

/** A 503 whose body is read only when the test releases it, or rejects if the request aborts. */
function slowErrorBody() {
  let started!: () => void;
  const reading = new Promise<void>((resolve) => (started = resolve));
  let release!: (text: string) => void;
  global.fetch = jest.fn(async (_url: unknown, init: RequestInit = {}) => ({
    ok: false,
    status: 503,
    headers: { get: () => null },
    text: () =>
      new Promise<string>((resolve, reject) => {
        release = resolve;
        init.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("The operation was aborted.", "AbortError")),
          { once: true },
        );
        started();
      }),
  })) as unknown as typeof fetch;
  return { reading, release: (text: string) => release(text) };
}

beforeEach(() => {
  localStorage.clear();
  clearMaintenance();
  startSession("a.jwt");
});
afterEach(() => {
  global.fetch = realFetch;
  clearMaintenance();
  endSession();
});

test("logout during the error-body read settles as a cancellation", async () => {
  const body = slowErrorBody();
  const result = api.get("/api/example").catch((e) => e);
  await body.reading;
  endSession();
  startSession("b.jwt");
  expect(isAbortError(await result)).toBe(true);
});

test("the caller cancelling during the error-body read settles as a cancellation", async () => {
  const body = slowErrorBody();
  const controller = new AbortController();
  const result = api.get("/api/example", { signal: controller.signal }).catch((e) => e);
  await body.reading;
  controller.abort();
  expect(isAbortError(await result)).toBe(true);
});

test("an error body that finishes after another login does not report maintenance", async () => {
  // A body reader that ignores the abort and finishes after the next session has started.
  let started!: () => void;
  const reading = new Promise<void>((resolve) => (started = resolve));
  let release!: (text: string) => void;
  global.fetch = jest.fn(async () => ({
    ok: false,
    status: 503,
    headers: { get: () => null },
    text: () =>
      new Promise<string>((resolve) => {
        release = resolve;
        started();
      }),
  })) as unknown as typeof fetch;
  const result = api.get("/api/example").catch((e) => e);
  await reading;
  endSession();
  startSession("b.jwt");
  release(JSON.stringify({ status: "maintenance", reason: "upgrade_failed" }));

  expect(isAbortError(await result)).toBe(true);
  expect(getMaintenance()).toBeNull();
});
