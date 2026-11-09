/**
 * One session-ending path serves explicit logout and an expired session. Ending a
 * session drops the token, empties every session-bound holder, and makes any request still in
 * flight from that session unable to write into the next one.
 */
import { renderHook, waitFor, act } from "@testing-library/react";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { api, ApiError, isAbortError } from "@/lib/api";
import { getToken } from "@/lib/auth";
import {
  endSession,
  replaceSessionToken,
  sessionGeneration,
  sessionSignal,
  startSession,
} from "@/lib/session";
import { permissionsCache } from "@/hooks/permissionsCache";
import { capabilitiesCache, MINIMAL_CAPABILITIES } from "@/hooks/capabilitiesCache";
import { usePermissions } from "@/hooks/usePermissions";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useComponents } from "@/hooks/useComponents";
import { useStackOptions } from "@/hooks/useStackOptions";
import { useVocabulary } from "@/hooks/useVocabulary";

const realFetch = global.fetch;

type Reply = { status: number; body: unknown };
type Pending = { url: string; token: string | null; resolve: (reply: Reply) => void };

let pending: Pending[] = [];

/** Every request waits until the test answers it, so the test controls the order of arrival. */
function holdRequests() {
  pending = [];
  global.fetch = jest.fn(
    (url: RequestInfo | URL, init?: RequestInit) =>
      new Promise((resolve) => {
        const headers = (init?.headers ?? {}) as Record<string, string>;
        const auth = headers.Authorization ?? null;
        pending.push({
          url: String(url),
          token: auth ? auth.replace(/^Bearer /, "") : null,
          resolve: ({ status, body }) =>
            resolve({
              ok: status >= 200 && status < 300,
              status,
              json: async () => body,
            } as Response),
        });
      }),
  ) as unknown as typeof fetch;
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request for ${fragment}`);
  return pending.splice(index, 1)[0];
}

const ME_A = {
  id: 1,
  email: "a@lab.org",
  name: "A",
  role_id: 1,
  role_name: "Admin",
  organization_id: 1,
  status: "active",
  permissions: [{ resource: "users", action: "manage" }],
};
const ME_B = { ...ME_A, id: 2, email: "b@lab.org", role_name: "Viewer", permissions: [] };

beforeEach(() => {
  localStorage.clear();
  endSession();
  mockNavigateToLogin.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
  holdRequests();
});

afterEach(() => {
  global.fetch = realFetch;
  jest.restoreAllMocks();
});

describe("ending a session", () => {
  test("drops the token and every session-bound holder, and can run twice", () => {
    startSession("a.jwt");
    permissionsCache.permissions = new Set(["users:manage"]);
    permissionsCache.roleName = "Admin";
    capabilitiesCache.value = MINIMAL_CAPABILITIES;

    endSession();
    endSession();

    expect(getToken()).toBeNull();
    expect(permissionsCache.permissions).toBeNull();
    expect(permissionsCache.roleName).toBeNull();
    expect(capabilitiesCache.value).toBeNull();
  });

  test("aborts work that belongs to the ended session", () => {
    startSession("a.jwt");
    const signal = sessionSignal();
    expect(signal.aborted).toBe(false);

    endSession();

    expect(signal.aborted).toBe(true);
    expect(sessionSignal().aborted).toBe(false);
  });

  test("a permissions answer that arrives after logout does not repopulate the cache", async () => {
    startSession("a.jwt");
    renderHook(() => usePermissions());
    const request = take("/api/auth/me");

    endSession();
    await act(async () => request.resolve({ status: 200, body: ME_A }));

    expect(permissionsCache.permissions).toBeNull();
  });

  test("the previous user's late permissions do not reach the next user", async () => {
    startSession("a.jwt");
    renderHook(() => usePermissions());
    const requestA = take("/api/auth/me");

    endSession();
    startSession("b.jwt");
    const { result } = renderHook(() => usePermissions());
    const requestB = take("/api/auth/me");
    expect(requestB.token).toBe("b.jwt");

    await act(async () => requestB.resolve({ status: 200, body: ME_B }));
    await act(async () => requestA.resolve({ status: 200, body: ME_A }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.roleName).toBe("Viewer");
    expect(result.current.canAccess("users", "manage")).toBe(false);
    expect(permissionsCache.roleName).toBe("Viewer");
  });

  test("the previous user's late failure does not mark the next user's permissions failed", async () => {
    startSession("a.jwt");
    renderHook(() => usePermissions());
    const requestA = take("/api/auth/me");

    endSession();
    startSession("b.jwt");
    const { result } = renderHook(() => usePermissions());
    const requestB = take("/api/auth/me");

    await act(async () => requestA.resolve({ status: 500, body: { detail: "boom" } }));
    await act(async () => requestB.resolve({ status: 200, body: ME_B }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.failed).toBe(false);
    expect(permissionsCache.failed).toBe(false);
    expect(permissionsCache.roleName).toBe("Viewer");
  });

  test("a capabilities answer from the previous session does not fill the next one", async () => {
    startSession("a.jwt");
    renderHook(() => useCapabilities());
    const requestA = take("/api/bootstrap/status");

    endSession();
    await act(async () =>
      requestA.resolve({ status: 200, body: { capabilities: { notebooks: true } } }),
    );

    expect(capabilitiesCache.value).toBeNull();
  });

  test("component, stack-option, and vocabulary answers do not outlive their session", async () => {
    startSession("a.jwt");
    renderHook(() => useComponents());
    renderHook(() => useStackOptions());
    renderHook(() => useVocabulary("organism"));
    const components = take("/stack/components");
    const stack = take("/stack-options");
    const vocab = take("/api/vocabularies");

    endSession();
    startSession("b.jwt");
    await act(async () => {
      components.resolve({
        status: 200,
        body: {
          compute_stack: "kubernetes",
          compute_deployed: true,
          storage_deployed: true,
          components: [
            {
              key: "notebooks",
              name: "Notebooks",
              category: "compute",
              description: "",
              cost_estimate: "",
              dependencies: [],
              status: "enabled",
              configurable: false,
            },
          ],
        },
      });
      stack.resolve({ status: 200, body: { cloud_provider: "aws", options: [] } });
      vocab.resolve({
        status: 200,
        body: { field_name: "organism", values: [{ id: 1, value: "human", display_label: "Human" }] },
      });
    });

    // The next session reads its own answers rather than the previous session's.
    const next = renderHook(() => useComponents());
    const nextStack = renderHook(() => useStackOptions());
    const nextVocab = renderHook(() => useVocabulary("organism"));
    expect(next.result.current.loading).toBe(true);
    expect(nextStack.result.current.loading).toBe(true);
    expect(nextVocab.result.current.loading).toBe(true);
    expect(pending.some((p) => p.url.includes("/stack/components") && p.token === "b.jwt")).toBe(true);
    expect(pending.some((p) => p.url.includes("/stack-options") && p.token === "b.jwt")).toBe(true);
    expect(pending.some((p) => p.url.includes("/api/vocabularies") && p.token === "b.jwt")).toBe(true);
  });
});

describe("which 401s end the session", () => {
  test("a 401 on the active session's request ends it and sends the user to log in", async () => {
    startSession("a.jwt");
    const call = api.get("/api/experiments").catch((e) => e);
    take("/api/experiments").resolve({ status: 401, body: { detail: "Unauthorized" } });

    expect(await call).toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(mockNavigateToLogin).toHaveBeenCalledTimes(1);
  });

  test("a late 401 from the previous session does not end the new one", async () => {
    startSession("a.jwt");
    const call = api.get("/api/experiments").catch((e) => e);
    const old = take("/api/experiments");

    endSession();
    startSession("b.jwt");
    permissionsCache.roleName = "Viewer";
    old.resolve({ status: 401, body: { detail: "Unauthorized" } });

    // Ending session A cancelled its request, so the late 401 never reaches the policy.
    expect(isAbortError(await call)).toBe(true);
    expect(getToken()).toBe("b.jwt");
    expect(permissionsCache.roleName).toBe("Viewer");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });

  test("a 401 for a credential the session has since replaced does not end the session", async () => {
    startSession("a.jwt");
    permissionsCache.roleName = "Admin";
    const call = api.get("/api/experiments").catch((e) => e);
    const old = take("/api/experiments");

    // Profile changes re-issue the token inside the same session.
    replaceSessionToken("a2.jwt", sessionGeneration());
    old.resolve({ status: 401, body: { detail: "Unauthorized" } });

    expect(await call).toBeInstanceOf(ApiError);
    expect(getToken()).toBe("a2.jwt");
    expect(permissionsCache.roleName).toBe("Admin");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });

  test("replacing the token keeps the session's holders and its work", () => {
    startSession("a.jwt");
    permissionsCache.roleName = "Admin";
    const signal = sessionSignal();

    replaceSessionToken("a2.jwt", sessionGeneration());

    expect(permissionsCache.roleName).toBe("Admin");
    expect(signal.aborted).toBe(false);
  });

  test("a wrong password on the login form is shown, not answered with a reload", async () => {
    // No session exists yet: a 401 here is the login endpoint's answer, not an expiry.
    const call = api
      .post("/api/auth/login", { email: "a@lab.org", password: "nope" })
      .catch((e) => e);
    take("/api/auth/login").resolve({ status: 401, body: { detail: "Invalid email or password" } });

    const error = (await call) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
    expect(error.message).toBe("Invalid email or password");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });

  test.each([
    [403, { detail: "Permission denied" }],
    [429, { detail: "The AI provider is limiting requests right now. Wait a minute and try again." }],
    [502, { detail: "The AI provider did not accept bioAF's credentials." }],
    [504, { detail: "The AI provider did not respond in time. Try again." }],
  ])("a %s leaves the session intact", async (status, body) => {
    startSession("a.jwt");
    permissionsCache.roleName = "Admin";
    const call = api.get("/api/assistant/x").catch((e) => e);
    take("/api/assistant/x").resolve({ status, body });

    expect(await call).toBeInstanceOf(ApiError);
    expect(getToken()).toBe("a.jwt");
    expect(permissionsCache.roleName).toBe("Admin");
    expect(mockNavigateToLogin).not.toHaveBeenCalled();
  });
});
