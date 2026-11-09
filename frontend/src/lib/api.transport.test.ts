/**
 * The application transport (plan 13): one auth, status, and error policy shared by JSON,
 * multipart, proxied XHR upload, blob/text/download, and stream requests.
 */
const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { api, ApiError, apiUrl, fileContentUrl, isAbortError } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { endSession, startSession } from "@/lib/session";
import { clearMaintenance, getMaintenance } from "@/lib/maintenance";
import { permissionsCache } from "@/hooks/permissionsCache";
import { uploadFileResumable } from "@/lib/resumableUpload";

const realFetch = global.fetch;
const realXhr = global.XMLHttpRequest;
const realEnv = process.env.NEXT_PUBLIC_API_URL;

const MAINTENANCE = {
  status: "maintenance",
  reason: "upgrade_in_progress",
  detail: "bioAF is finishing an upgrade and is not accepting requests yet.",
};
const MAINTENANCE_MESSAGE = "bioAF is finishing an upgrade. Try again in a minute.";
const FAILED_UPGRADE = { ...MAINTENANCE, reason: "upgrade_failed" };
const FAILED_UPGRADE_MESSAGE =
  "bioAF’s last upgrade failed. An administrator needs to review the failure and retry.";

interface FakeBody {
  json?: unknown;
  /** Present when the body is not JSON (HTML from a proxy, an empty body). */
  raw?: string;
  headers?: Record<string, string>;
  blob?: Blob;
}

const NOT_JSON = Symbol("not json");

function fakeResponse(status: number, body: FakeBody = {}): Response {
  const headers = Object.fromEntries(
    Object.entries(body.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]),
  );
  const raw = body.raw ?? (body.json === undefined ? "" : JSON.stringify(body.json));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => headers[name.toLowerCase()] ?? null },
    json: jest.fn(async () => {
      if (body.raw !== undefined || body.json === NOT_JSON) {
        throw new SyntaxError("Unexpected token < in JSON at position 0");
      }
      if (body.json === undefined) throw new SyntaxError("Unexpected end of JSON input");
      return body.json;
    }),
    text: jest.fn(async () => raw),
    blob: jest.fn(async () => body.blob ?? new Blob([raw])),
    body: { getReader: () => ({}) },
  } as unknown as Response;
}

function respondWith(status: number, body: FakeBody = {}) {
  const response = fakeResponse(status, body);
  global.fetch = jest.fn(async () => response) as unknown as typeof fetch;
  return response;
}

function lastFetch(): { url: string; init: RequestInit } {
  const calls = (global.fetch as jest.Mock).mock.calls;
  const [url, init] = calls[calls.length - 1];
  return { url: String(url), init: init ?? {} };
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

/** A controllable XMLHttpRequest stand-in for proxied and signed uploads. */
class FakeXhr {
  static instances: FakeXhr[] = [];
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = "";
  aborted = false;
  upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private responseHeaders: Record<string, string> = {};
  constructor() {
    FakeXhr.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader(name: string) {
    return this.responseHeaders[name.toLowerCase()] ?? null;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  respond(status: number, text: string, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = text;
    this.responseHeaders = Object.fromEntries(
      Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]),
    );
    this.onload?.();
  }
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
  }
}

function lastXhr(): FakeXhr {
  return FakeXhr.instances[FakeXhr.instances.length - 1];
}

const file = () => new File(["abc"], "reads.fastq", { type: "text/plain" });

beforeEach(() => {
  localStorage.clear();
  endSession();
  startSession("live.jwt");
  permissionsCache.roleName = "Admin";
  mockNavigateToLogin.mockReset();
  FakeXhr.instances = [];
  global.XMLHttpRequest = FakeXhr as unknown as typeof XMLHttpRequest;
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  global.XMLHttpRequest = realXhr;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  clearMaintenance();
  jest.restoreAllMocks();
  jest.useRealTimers();
});

describe("application URL resolution", () => {
  test("an unset API URL falls back to the local backend", () => {
    delete process.env.NEXT_PUBLIC_API_URL;
    expect(apiUrl("/api/projects")).toBe("http://localhost:8000/api/projects");
  });

  test("an explicitly empty API URL means the same origin", () => {
    process.env.NEXT_PUBLIC_API_URL = "";
    expect(apiUrl("/api/projects")).toBe("/api/projects");
  });

  test("a configured API URL is used as the base", () => {
    process.env.NEXT_PUBLIC_API_URL = "https://bioaf.lab.org";
    expect(apiUrl("/api/projects")).toBe("https://bioaf.lab.org/api/projects");
  });

  test.each(["https://storage.example.com/x", "//storage.example.com/x", "api/projects", ""])(
    "the authenticated client refuses %p, which is not an application path",
    async (path) => {
      respondWith(200, { json: {} });
      await expect(api.get(path)).rejects.toThrow();
      expect(global.fetch).not.toHaveBeenCalled();
    },
  );

  test("content-token URLs carry the content token, never the session JWT", async () => {
    process.env.NEXT_PUBLIC_API_URL = "";
    respondWith(200, { json: { token: "content.tok", expires_in: 60 } });
    const url = await fileContentUrl(7);
    expect(url).toBe("/api/files/7/content?token=content.tok");
    expect(url).not.toContain("live.jwt");
  });
});

describe("JSON responses", () => {
  test("a 204 resolves with no value", async () => {
    respondWith(204);
    await expect(api.delete("/api/projects/1")).resolves.toBeUndefined();
  });

  test("a malformed successful body is a failure, not a success", async () => {
    respondWith(200, { raw: "<html>proxy</html>" });
    const error = (await api.get("/api/projects").catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("invalid_response");
  });

  test("a request can be cancelled through its signal", async () => {
    const controller = new AbortController();
    global.fetch = jest.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }),
    ) as unknown as typeof fetch;

    const call = api.get("/api/projects", { signal: controller.signal }).catch((e) => e);
    controller.abort();

    expect(isAbortError(await call)).toBe(true);
  });
});

describe("error bodies", () => {
  test.each([
    ["HTML from a proxy", { raw: "<html><body>502 Bad Gateway</body></html>" }],
    ["an empty body", { raw: "" }],
    ["JSON null", { json: null }],
    ["a bare string", { json: "boom" }],
    ["an array", { json: [1, 2] }],
    ["an unrecognized object", { json: { error: "x" } }],
  ])("%s still gives a status-bearing, readable error", async (_label, body) => {
    respondWith(502, body as FakeBody);
    const error = (await api.get("/api/projects").catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.message).toBe("Request failed (HTTP 502).");
  });

  test("validation arrays are joined", async () => {
    respondWith(422, { json: { detail: [{ msg: "name is required" }, { msg: "bad date" }] } });
    const error = (await api.post("/api/projects", {}).catch((e) => e)) as ApiError;
    expect(error.message).toBe("name is required; bad date");
  });

  test("the parsed body stays available for feature-specific conflicts", async () => {
    const body = {
      detail: {
        detail: "Sample ID “DUP” previously identified multiple samples.",
        error: "ambiguous_external_id",
        candidates: ["DUP~6", "DUP~7"],
      },
    };
    respondWith(409, { json: body });
    const error = (await api.post("/api/samples", {}).catch((e) => e)) as ApiError;
    expect(error.message).toBe(body.detail.detail);
    expect(error.body).toEqual(body);
  });
});

describe("rate limiting", () => {
  test("the rate-limit middleware's message, with its limit, reaches the user", async () => {
    respondWith(429, { json: { detail: "Too many requests. Limit: 5 per minute." } });
    const error = (await api.post("/api/auth/login", {}).catch((e) => e)) as ApiError;
    expect(error.status).toBe(429);
    expect(error.message).toBe("Too many requests. Limit: 5 per minute.");
  });

  test("a provider's explanation and structured fields are kept", async () => {
    respondWith(429, {
      json: {
        detail: "Google Sheets is receiving too many requests. Wait a minute and try again.",
        code: "sheets_rate_limited",
        details: { provider: "google" },
      },
    });
    const error = (await api.get("/api/sheets/preview").catch((e) => e)) as ApiError;
    expect(error.message).toBe(
      "Google Sheets is receiving too many requests. Wait a minute and try again.",
    );
    expect(error.code).toBe("sheets_rate_limited");
    expect(error.details).toEqual({ provider: "google" });
  });

  test("an unusable body falls back to the generic sentence", async () => {
    respondWith(429, { raw: "" });
    const error = (await api.get("/api/projects").catch((e) => e)) as ApiError;
    expect(error.message).toBe("Too many requests. Please wait and try again.");
  });

  test("Retry-After is exposed when the server sends it", async () => {
    respondWith(429, { json: { detail: "Slow down." }, headers: { "Retry-After": "30" } });
    const error = (await api.get("/api/projects").catch((e) => e)) as ApiError;
    expect(error.retryAfter).toBe(30);
    respondWith(429, { json: { detail: "Slow down." } });
    const without = (await api.get("/api/projects").catch((e) => e)) as ApiError;
    expect(without.retryAfter).toBeNull();
  });
});

/**
 * Every application transport path, driven the same way, so the shared policy is proven for each
 * rather than for the JSON client alone.
 */
type Mode = {
  name: string;
  run: (status: number, body: FakeBody) => Promise<unknown>;
};

const MODES: Mode[] = [
  {
    name: "JSON",
    run: (status, body) => {
      respondWith(status, body);
      return api.get("/api/projects");
    },
  },
  {
    name: "multipart upload",
    run: (status, body) => {
      respondWith(status, body);
      return api.upload("/api/files/upload", file());
    },
  },
  {
    name: "proxied XHR upload",
    run: (status, body) => {
      const call = api.uploadProxied(file(), { projectId: 3 });
      lastXhr().respond(
        status,
        body.raw ?? (body.json === undefined ? "" : JSON.stringify(body.json)),
        body.headers,
      );
      return call;
    },
  },
  {
    name: "download",
    run: (status, body) => {
      respondWith(status, body);
      return api.download("/api/exports/1");
    },
  },
  {
    name: "blob",
    run: (status, body) => {
      respondWith(status, body);
      return api.blob("/api/files/1/content");
    },
  },
  {
    name: "text",
    run: (status, body) => {
      respondWith(status, body);
      return api.text("/api/pipeline-runs/1/report");
    },
  },
  {
    name: "stream (before it opens)",
    run: (status, body) => {
      respondWith(status, body);
      return api.stream("/api/v1/infrastructure/terraform/apply", { method: "POST" });
    },
  },
];

describe.each(MODES)("$name requests share the status policy", ({ run }) => {
  test("an active-session 401 ends the session", async () => {
    const error = (await run(401, { json: { detail: "Unauthorized" } }).catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
    expect(getToken()).toBeNull();
    expect(permissionsCache.roleName).toBeNull();
    expect(mockNavigateToLogin).toHaveBeenCalledTimes(1);
  });

  test("a 429 keeps the backend's explanation and the session", async () => {
    const error = (await run(429, { json: { detail: "Too many requests. Limit: 5 per minute." } }).catch(
      (e) => e,
    )) as ApiError;
    expect(error.status).toBe(429);
    expect(error.message).toBe("Too many requests. Limit: 5 per minute.");
    expect(getToken()).toBe("live.jwt");
  });

  test("a 429 without a usable body uses the generic sentence", async () => {
    const error = (await run(429, { raw: "" }).catch((e) => e)) as ApiError;
    expect(error.message).toBe("Too many requests. Please wait and try again.");
  });

  test("a maintenance 503 is reported, with the upgrade's own message", async () => {
    sessionStorage.setItem("bioaf_backend_ready", "true");
    const running = (await run(503, { json: MAINTENANCE }).catch((e) => e)) as ApiError;
    expect(running.status).toBe(503);
    expect(running.code).toBe("maintenance");
    expect(running.message).toBe(MAINTENANCE_MESSAGE);
    expect(getMaintenance()?.reason).toBe("upgrade_in_progress");
    expect(sessionStorage.getItem("bioaf_backend_ready")).toBeNull();

    const failed = (await run(503, { json: FAILED_UPGRADE }).catch((e) => e)) as ApiError;
    expect(failed.message).toBe(FAILED_UPGRADE_MESSAGE);
  });

  test("an ordinary 503 is an ordinary failure", async () => {
    const error = (await run(503, { json: { detail: "Service unavailable" } }).catch((e) => e)) as ApiError;
    expect(error.status).toBe(503);
    expect(error.message).toBe("Service unavailable");
    expect(getMaintenance()).toBeNull();
  });

  test("a 403 and a provider 502 leave the session intact", async () => {
    await run(403, { json: { detail: "Permission denied" } }).catch((e) => e);
    await run(502, { json: { detail: "The AI provider did not accept bioAF's credentials." } }).catch(
      (e) => e,
    );
    expect(getToken()).toBe("live.jwt");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });

  test("an unreadable error body still carries its status", async () => {
    const error = (await run(500, { raw: "<html>oops</html>" }).catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(500);
    expect(error.message).not.toContain("<html>");
  });
});

describe("multipart upload", () => {
  test("lets the browser set the multipart boundary and sends the session token", async () => {
    respondWith(200, { json: { id: 1 } });
    await api.upload("/api/files/upload", file(), { project_id: "3" });
    const { init } = lastFetch();
    expect(headersOf(init)["Content-Type"]).toBeUndefined();
    expect(headersOf(init).Authorization).toBe("Bearer live.jwt");
    expect((init.body as FormData).get("project_id")).toBe("3");
  });

  test("a 204 resolves with no value", async () => {
    respondWith(204);
    await expect(api.upload("/api/files/upload", file())).resolves.toBeUndefined();
  });
});

describe("proxied XHR upload", () => {
  test("sends the session token to the application and reports progress", async () => {
    process.env.NEXT_PUBLIC_API_URL = "";
    const progress: number[] = [];
    const call = api.uploadProxied(file(), { projectId: 3, onProgress: (p) => progress.push(p) });
    const xhr = lastXhr();
    expect(xhr.url).toBe("/api/files/upload/simple?project_id=3");
    expect(xhr.headers.Authorization).toBe("Bearer live.jwt");
    xhr.progress(1, 2);
    xhr.respond(201, JSON.stringify({ id: 9 }));
    await expect(call).resolves.toEqual({ id: 9 });
    expect(progress).toEqual([50]);
  });

  test("a malformed successful body is a failure, not a silent success", async () => {
    const call = api.uploadProxied(file()).catch((e) => e);
    lastXhr().respond(200, "<html>proxy</html>");
    const error = (await call) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("invalid_response");
  });

  test("a 204 resolves with no value", async () => {
    const call = api.uploadProxied(file());
    lastXhr().respond(204, "");
    await expect(call).resolves.toBeUndefined();
  });

  test("structured error fields reach the caller", async () => {
    const call = api.uploadProxied(file()).catch((e) => e);
    lastXhr().respond(
      400,
      JSON.stringify({ detail: "Quota exceeded", code: "quota", details: { limit: 5 } }),
    );
    const error = (await call) as ApiError;
    expect(error.message).toBe("Quota exceeded");
    expect(error.code).toBe("quota");
    expect(error.details).toEqual({ limit: 5 });
  });

  test("cancelling settles the upload as cancelled and detaches its handlers", async () => {
    const controller = new AbortController();
    const progress: number[] = [];
    const call = api
      .uploadProxied(file(), { signal: controller.signal, onProgress: (p) => progress.push(p) })
      .catch((e) => e);
    const xhr = lastXhr();
    controller.abort();

    expect(isAbortError(await call)).toBe(true);
    expect(xhr.aborted).toBe(true);
    xhr.progress(1, 2);
    expect(progress).toEqual([]);
  });
});

describe("downloads and blobs", () => {
  let click: jest.SpyInstance;
  beforeEach(() => {
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:x");
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
    click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  test.each([
    ['attachment; filename="report.csv"', "report.csv"],
    ["attachment; filename=report.csv", "report.csv"],
    ["attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf", "résumé.pdf"],
    [null, "download"],
  ])("Content-Disposition %p names the file %p", async (disposition, expected) => {
    respondWith(200, { raw: "a,b", headers: disposition ? { "Content-Disposition": disposition } : {} });
    let name = "";
    click.mockImplementation(function (this: HTMLAnchorElement) {
      name = this.download;
    });
    await api.download("/api/exports/1");
    expect(name).toBe(expected);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
  });

  test("a caller's fallback filename is used when the response names none", async () => {
    respondWith(200, { raw: "a,b" });
    const result = await api.blob("/api/exports/1", { filename: "audit-log.csv" });
    expect(result.filename).toBe("audit-log.csv");
  });

  test("POST downloads send a JSON body", async () => {
    respondWith(200, { raw: "zip" });
    await api.download("/api/projects/1/export/data", "POST", { format: "zip" });
    const { init } = lastFetch();
    expect(init.method).toBe("POST");
    expect(headersOf(init)["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ format: "zip" }));
  });

  test("a cancelled download saves nothing", async () => {
    const controller = new AbortController();
    const response = fakeResponse(200, { raw: "zip" });
    (response.blob as jest.Mock).mockImplementation(async () => {
      controller.abort();
      return new Blob(["zip"]);
    });
    global.fetch = jest.fn(async () => response) as unknown as typeof fetch;

    const error = await api.download("/api/exports/1", "GET", undefined, { signal: controller.signal }).catch(
      (e) => e,
    );
    expect(isAbortError(error)).toBe(true);
    expect(click).not.toHaveBeenCalled();
  });
});

describe("stream requests", () => {
  test("the stream is handed over unread", async () => {
    const response = respondWith(200, { raw: "data: {}\n\n" });
    const result = await api.stream("/api/v1/infrastructure/terraform/apply", {
      method: "POST",
      body: { component: "slurm" },
    });
    expect(result).toBe(response);
    expect(response.json).not.toHaveBeenCalled();
    expect(response.text).not.toHaveBeenCalled();
    expect(headersOf(lastFetch().init).Authorization).toBe("Bearer live.jwt");
  });
});

describe("read retries", () => {
  function sequence(...replies: Array<Response | Error>) {
    const fetchMock = jest.fn();
    for (const reply of replies) {
      if (reply instanceof Error) fetchMock.mockRejectedValueOnce(reply);
      else fetchMock.mockResolvedValueOnce(reply);
    }
    global.fetch = fetchMock as unknown as typeof fetch;
    return fetchMock;
  }

  test("a transient network failure and a 500 are retried", async () => {
    jest.useFakeTimers();
    const fetchMock = sequence(
      new TypeError("Failed to fetch"),
      fakeResponse(500, { json: { detail: "boom" } }),
      fakeResponse(200, { json: { ok: true } }),
    );
    const call = api.getWithRetry("/api/dashboard/x");
    await jest.runAllTimersAsync();
    await expect(call).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("the retry budget is three retries", async () => {
    jest.useFakeTimers();
    const fetchMock = jest.fn(async () => fakeResponse(500, { json: { detail: "boom" } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const call = api.getWithRetry("/api/dashboard/x").catch((e) => e);
    await jest.runAllTimersAsync();
    expect(((await call) as ApiError).status).toBe(500);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  test.each([
    ["a 400", fakeResponse(400, { json: { detail: "bad" } })],
    ["a 401", fakeResponse(401, { json: { detail: "Unauthorized" } })],
    ["a 403", fakeResponse(403, { json: { detail: "no" } })],
    ["a 404", fakeResponse(404, { json: { detail: "missing" } })],
    ["a 422", fakeResponse(422, { json: { detail: [{ msg: "bad" }] } })],
    ["a 429", fakeResponse(429, { json: { detail: "slow" } })],
    ["a maintenance 503", fakeResponse(503, { json: MAINTENANCE })],
    ["a malformed success", fakeResponse(200, { raw: "<html>" })],
  ])("%s is not retried", async (_label, reply) => {
    jest.useFakeTimers();
    const fetchMock = sequence(reply, fakeResponse(200, { json: {} }));
    const call = api.getWithRetry("/api/dashboard/x").catch((e) => e);
    await jest.runAllTimersAsync();
    await call;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("cancelling during the retry delay stops at once", async () => {
    jest.useFakeTimers();
    const controller = new AbortController();
    const fetchMock = sequence(new TypeError("Failed to fetch"), fakeResponse(200, { json: {} }));
    const call = api.getWithRetry("/api/dashboard/x", undefined, { signal: controller.signal }).catch((e) => e);
    await jest.advanceTimersByTimeAsync(10);
    controller.abort();
    expect(isAbortError(await call)).toBe(true);
    await jest.runAllTimersAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("a cancelled request is not retried", async () => {
    jest.useFakeTimers();
    const fetchMock = sequence(
      new DOMException("The operation was aborted.", "AbortError"),
      fakeResponse(200, { json: {} }),
    );
    const call = api.getWithRetry("/api/dashboard/x").catch((e) => e);
    await jest.runAllTimersAsync();
    expect(isAbortError(await call)).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("external uploads keep their own protocol", () => {
  test("the signed PUT carries no bioAF credential; initiate and complete do", async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        fakeResponse(200, { json: { upload_id: "u1", signed_url: "https://storage.example.com/put?sig=1" } }),
      )
      .mockResolvedValueOnce(fakeResponse(200, { json: { id: 4 } }));
    global.fetch = fetchMock as unknown as typeof fetch;

    const call = api.uploadSigned(file(), { projectId: 3 });
    await new Promise((r) => setTimeout(r, 0));
    const put = lastXhr();
    expect(put.url).toBe("https://storage.example.com/put?sig=1");
    expect(put.headers.Authorization).toBeUndefined();
    put.respond(200, "");
    await expect(call).resolves.toEqual({ id: 4 });

    for (const [, init] of fetchMock.mock.calls) {
      expect(headersOf(init).Authorization).toBe("Bearer live.jwt");
    }
  });

  test("a storage 401 on the signed PUT does not log the user out", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        fakeResponse(200, { json: { upload_id: "u1", signed_url: "https://storage.example.com/put" } }),
      ) as unknown as typeof fetch;
    const call = api.uploadSigned(file()).catch((e) => e);
    await new Promise((r) => setTimeout(r, 0));
    lastXhr().respond(401, "<Error>AccessDenied</Error>");
    expect(await call).toBeInstanceOf(Error);
    expect(getToken()).toBe("live.jwt");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });

  test("a resumable session PUT carries no bioAF credential and a storage 401 keeps the session", async () => {
    global.fetch = jest.fn(async () => ({
      status: 401,
      statusText: "Unauthorized",
      headers: { get: () => null },
    })) as unknown as typeof fetch;
    await expect(uploadFileResumable("https://storage.example.com/session", file())).rejects.toThrow();
    expect(headersOf(lastFetch().init).Authorization).toBeUndefined();
    expect(getToken()).toBe("live.jwt");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });
});
