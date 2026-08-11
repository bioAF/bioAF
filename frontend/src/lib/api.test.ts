import { extractErrorMessage, api, ApiError } from "./api";
import { getToken, setToken } from "./auth";
import { permissionsCache } from "@/hooks/permissionsCache";
import { capabilitiesCache, MINIMAL_CAPABILITIES } from "@/hooks/capabilitiesCache";

describe("extractErrorMessage", () => {
  test("returns a plain string detail unchanged", () => {
    expect(extractErrorMessage({ detail: "boom" })).toBe("boom");
  });

  test("maps a known string code to a friendly message", () => {
    expect(extractErrorMessage({ detail: "review_in_progress" })).toMatch(
      /already in progress/i,
    );
  });

  test("maps a structured 409 conflict (nested code) to a friendly message", () => {
    // Shape raised by JobAlreadyRunning: detail is an object, not a string.
    const body = {
      detail: {
        detail: "review_in_progress",
        existing_job_id: 12,
        existing_agent_review_id: 34,
      },
    };
    expect(extractErrorMessage(body)).toMatch(/already in progress/i);
  });

  test("falls back to a nested string detail when the code is unknown", () => {
    expect(extractErrorMessage({ detail: { detail: "some other thing" } })).toBe(
      "some other thing",
    );
  });

  test("joins validation arrays", () => {
    expect(
      extractErrorMessage({ detail: [{ msg: "a" }, { msg: "b" }] }),
    ).toBe("a; b");
  });

  test("falls back to a generic message for an opaque object", () => {
    expect(extractErrorMessage({ detail: { foo: 1 } })).toBe("Request failed");
  });
});

describe("ApiError carries the structured domain-error envelope", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  test("a domain error surfaces code and details to the caller", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        detail: "Some selected samples have no linked input files",
        code: "samples_missing_files",
        details: { samples_without_files: [{ id: 9, external_id: "SAMPLE-102" }] },
      }),
    }) as unknown as typeof fetch;

    expect.assertions(3);
    try {
      await api.post("/api/pipeline-runs", {});
    } catch (err) {
      const e = err as ApiError;
      expect(e).toBeInstanceOf(ApiError);
      expect(e.code).toBe("samples_missing_files");
      expect(e.details?.samples_without_files).toEqual([{ id: 9, external_id: "SAMPLE-102" }]);
    }
  });
});

/**
 * Every request in the app goes through this function, and until now only the
 * pure `extractErrorMessage` helper above was covered. The branches below are
 * the ones a user actually meets: a session that expired while the tab was
 * open, and a burst of requests that trips the rate limiter.
 *
 * The 401 branch is the one worth guarding. It is the app's only session
 * teardown: it drops the bearer token and empties the permissions and
 * capabilities caches before sending the user to /login. If any of those stops
 * happening, a stale token or a previous user's cached permissions survive
 * into the next session, and nothing about the screen looks wrong.
 */
describe("the session-expiry and rate-limit branches of a request", () => {
  const realFetch = global.fetch;
  // The 401 branch also does `window.location.href = "/login"`. That is not
  // asserted here: jsdom 26 locks Location down (the object cannot be replaced
  // and `href` cannot be redefined or deleted), and assigning it is a no-op
  // that logs "Not implemented: navigation" rather than changing anything.
  // Asserting on that console message would test jsdom, not us. The teardown
  // below it is what actually matters, and that is covered.
  let consoleErr: jest.SpyInstance;

  beforeEach(() => {
    localStorage.clear();
    permissionsCache.permissions = null;
    permissionsCache.roleName = null;
    capabilitiesCache.value = null;
    // Swallow jsdom's navigation warning so a passing run stays readable.
    consoleErr = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    global.fetch = realFetch;
    consoleErr.mockRestore();
  });

  function respondWith(status: number, body: unknown = {}) {
    global.fetch = jest.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    }) as unknown as typeof fetch;
  }

  test("a 401 clears the stored token so a dead session cannot be replayed", async () => {
    setToken("stale.jwt.value");
    respondWith(401);

    await expect(api.get("/api/experiments")).rejects.toBeInstanceOf(ApiError);

    expect(getToken()).toBeNull();
  });

  test("a 401 empties the permissions and capabilities caches", async () => {
    setToken("stale.jwt.value");
    // Seed both holders, so asserting they are empty afterwards means they were
    // cleared rather than never populated.
    permissionsCache.permissions = new Set(["experiments.write"]);
    permissionsCache.roleName = "Lab Manager";
    capabilitiesCache.value = MINIMAL_CAPABILITIES;

    respondWith(401);
    await expect(api.get("/api/experiments")).rejects.toBeInstanceOf(ApiError);

    // A stale permission set outliving the session is how one user's rights
    // leak into the next login on a shared machine.
    expect(permissionsCache.permissions).toBeNull();
    expect(permissionsCache.roleName).toBeNull();
    expect(capabilitiesCache.value).toBeNull();
  });

  test("a 429 is reported as rate limiting and leaves the session alone", async () => {
    setToken("good.jwt.value");
    respondWith(429);

    expect.assertions(4);
    try {
      await api.get("/api/experiments");
    } catch (err) {
      const e = err as ApiError;
      expect(e).toBeInstanceOf(ApiError);
      expect(e.status).toBe(429);
      expect(e.message).toMatch(/too many requests/i);
    }
    // Being throttled is not being logged out. Clearing the token here would
    // turn a momentary burst into a forced re-login.
    expect(getToken()).toBe("good.jwt.value");
  });

  test("a failed request with an unreadable body still raises a usable error", async () => {
    setToken("good.jwt.value");
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON at position 0");
      },
    }) as unknown as typeof fetch;

    // A 500 from nginx is HTML, not JSON. The parse failure must not replace
    // the server's status with a TypeError from our own error handling.
    expect.assertions(2);
    try {
      await api.get("/api/experiments");
    } catch (err) {
      const e = err as ApiError;
      expect(e).toBeInstanceOf(ApiError);
      expect(e.status).toBe(500);
    }
  });

  test("the bearer token is attached when one is stored, and omitted when not", async () => {
    respondWith(200, { ok: true });
    await api.get("/api/experiments");
    let headers = (global.fetch as jest.Mock).mock.calls[0][1].headers;
    expect(headers.Authorization).toBeUndefined();

    setToken("live.jwt.value");
    respondWith(200, { ok: true });
    await api.get("/api/experiments");
    headers = (global.fetch as jest.Mock).mock.calls[0][1].headers;
    expect(headers.Authorization).toBe("Bearer live.jwt.value");
  });
});
