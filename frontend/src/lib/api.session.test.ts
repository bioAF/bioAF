/**
 * Requests belong to the session that started them (plan 13, owner review). Ending the session
 * aborts them; an answer, a downloaded body, a retry, or a re-issued credential that arrives
 * afterwards must not reach the next session.
 */
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import { api, isAbortError } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { endSession, replaceSessionToken, sessionGeneration, startSession } from "@/lib/session";
import { fakeResponse } from "@/testing/fakeServer";

const realFetch = global.fetch;
type Pending = { url: string; init: RequestInit; resolve: (r: Response) => void; reject: (e: unknown) => void };
let pending: Pending[];

beforeEach(() => {
  localStorage.clear();
  endSession();
  pending = [];
  global.fetch = jest.fn(
    (url: RequestInfo | URL, init: RequestInit = {}) =>
      new Promise<Response>((resolve, reject) => {
        // Like the browser: an aborted signal rejects the request.
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
        pending.push({ url: String(url), init, resolve, reject });
      }),
  ) as unknown as typeof fetch;
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:x");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test("ending the session aborts its requests in flight", async () => {
  startSession("a.jwt");
  const call = api.get("/api/projects").catch((e) => e);
  endSession();
  expect(pending[0].init.signal?.aborted).toBe(true);
  expect(isAbortError(await call)).toBe(true);
});

test("the previous user's token refresh cannot replace the next user's token", async () => {
  startSession("a.jwt");
  const generation = sessionGeneration();
  const refresh = api
    .post<{ access_token: string }>("/api/auth/refresh")
    .then((res) => replaceSessionToken(res.access_token, generation))
    .catch((e) => e);
  const request = pending[0];

  endSession();
  startSession("b.jwt");
  request.resolve(fakeResponse({ json: { access_token: "a2.jwt" } }));
  await refresh;

  expect(getToken()).toBe("b.jwt");
});

test("a credential re-issued in an earlier session is refused", () => {
  startSession("a.jwt");
  const generation = sessionGeneration();
  endSession();
  startSession("b.jwt");

  expect(replaceSessionToken("a2.jwt", generation)).toBe(false);
  expect(getToken()).toBe("b.jwt");
});

test("a credential re-issued in the same session replaces the token", () => {
  startSession("a.jwt");
  expect(replaceSessionToken("a2.jwt", sessionGeneration())).toBe(true);
  expect(getToken()).toBe("a2.jwt");
});

test("a download whose body arrives after logout saves nothing", async () => {
  const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  startSession("a.jwt");
  let releaseBody: (b: Blob) => void = () => {};
  let bodyRequested: () => void = () => {};
  const reading = new Promise<void>((resolve) => (bodyRequested = resolve));
  const response = fakeResponse({ text: "secret" });
  (response as unknown as { blob: () => Promise<Blob> }).blob = () => {
    bodyRequested();
    return new Promise<Blob>((resolve) => (releaseBody = resolve));
  };
  const call = api.download("/api/exports/1").catch((e) => e);
  pending[0].resolve(response);
  await reading;

  endSession();
  releaseBody(new Blob(["secret"]));

  expect(isAbortError(await call)).toBe(true);
  expect(click).not.toHaveBeenCalled();
});

test("a retry started in one session never sends the next session's credential", async () => {
  jest.useFakeTimers();
  startSession("a.jwt");
  const call = api.getWithRetry("/api/dashboard/x").catch((e) => e);
  pending[0].reject(new TypeError("Failed to fetch"));
  await jest.advanceTimersByTimeAsync(10);

  endSession();
  startSession("b.jwt");
  await jest.advanceTimersByTimeAsync(5000);

  expect(isAbortError(await call)).toBe(true);
  expect(pending).toHaveLength(1);
});

test("a proxied upload is cancelled when its session ends", async () => {
  const aborted: boolean[] = [];
  class Xhr {
    upload = { onprogress: null };
    onload = null;
    onerror = null;
    onabort: (() => void) | null = null;
    open() {}
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    send() {}
    abort() {
      aborted.push(true);
      this.onabort?.();
    }
  }
  const realXhr = global.XMLHttpRequest;
  global.XMLHttpRequest = Xhr as unknown as typeof XMLHttpRequest;
  try {
    startSession("a.jwt");
    const call = api.uploadProxied(new File(["x"], "x.txt")).catch((e) => e);
    endSession();
    expect(isAbortError(await call)).toBe(true);
    expect(aborted).toEqual([true]);
  } finally {
    global.XMLHttpRequest = realXhr;
  }
});
