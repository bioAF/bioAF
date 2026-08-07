import { renderHook, waitFor } from "@testing-library/react";

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mockApiGet(...args) },
}));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));

import { usePermissions, clearPermissionsCache } from "./usePermissions";

const me = {
  id: 1,
  email: "a@b.co",
  name: "A",
  role_id: 1,
  role_name: "admin",
  organization_id: 1,
  status: "active",
  permissions: [{ resource: "projects", action: "view" }],
};

beforeEach(() => {
  mockApiGet.mockReset();
  clearPermissionsCache();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe("usePermissions", () => {
  it("grants what the role holds", async () => {
    mockApiGet.mockResolvedValue(me);
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.canAccess("projects", "view")).toBe(true);
    expect(result.current.canAccess("users", "view")).toBe(false);
    expect(result.current.roleName).toBe("admin");
    expect(result.current.failed).toBe(false);
  });

  /**
   * The defect this locks: a failed /api/auth/me resolved to an empty permission
   * set, so the shell rendered a real, navigable app in which the user could do
   * nothing. Measured live: the sidebar collapsed to one item and the dashboard
   * read "Your dashboard has no widgets" -- a failed load presented as the
   * user's own preference.
   *
   * Permissions must still deny while unknown (granting what we cannot verify
   * would be a security defect). The fix is that callers can now tell the
   * difference and say so, instead of rendering an empty account.
   */
  it("says the load failed rather than presenting an empty account", async () => {
    mockApiGet.mockRejectedValue(new Error("auth/me down"));
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.failed).toBe(true);
    expect(result.current.canAccess("projects", "view")).toBe(false);
  });

  it("puts the real error in the logs", async () => {
    mockApiGet.mockRejectedValue(new Error("auth/me down"));
    const { result } = renderHook(() => usePermissions());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("[bioAF]"),
      expect.any(Error),
    );
  });

  it("does not cache a failure, so the next mount tries again", async () => {
    mockApiGet.mockRejectedValue(new Error("auth/me down"));
    const first = renderHook(() => usePermissions());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    expect(first.result.current.failed).toBe(true);
    first.unmount();

    mockApiGet.mockResolvedValue(me);
    const second = renderHook(() => usePermissions());
    await waitFor(() => expect(second.result.current.loading).toBe(false));

    expect(second.result.current.failed).toBe(false);
    expect(second.result.current.canAccess("projects", "view")).toBe(true);
  });

  it("still caches a success, so navigation does not refetch", async () => {
    mockApiGet.mockResolvedValue(me);
    const first = renderHook(() => usePermissions());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    first.unmount();

    const callsAfterFirst = mockApiGet.mock.calls.length;
    const second = renderHook(() => usePermissions());
    await waitFor(() => expect(second.result.current.loading).toBe(false));

    expect(mockApiGet.mock.calls.length).toBe(callsAfterFirst);
    expect(second.result.current.canAccess("projects", "view")).toBe(true);
  });
});
