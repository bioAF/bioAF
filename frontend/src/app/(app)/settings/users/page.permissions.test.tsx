/**
 * Lock, Deactivate, Reactivate and Delete appear only with the grant each route enforces
 * (users:deactivate; users:delete). Unknown permissions (loading, failed) deny, and the grant is
 * checked again when the action is confirmed, so a revoked grant sends no request.
 */
import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import UsersPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, sub: "1", email: "admin@test.com", role_name: "admin" }),
  getToken: () => "t",
}));
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/settings/users",
  useSearchParams: () => new URLSearchParams(),
}));
const canAccess = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess, hasPermission: canAccess, loading: false, roleName: "admin" }),
}));
jest.mock("@/lib/api", () => {
  class ApiError extends Error {}
  return { api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() }, ApiError };
});

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const user = (id: number, email: string, status: string) => ({
  id, email, name: null, role_id: 2, role_name: "viewer", status, organization_id: 1, last_login: null,
  session_credentials_configured: false, created_at: "2026-01-01T00:00:00Z",
});

function givenUsers(users: unknown[]) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/access-logs/never-logged-in")) return Promise.resolve({ users: [] });
    if (url.startsWith("/api/users")) return Promise.resolve({ users, total: users.length });
    if (url.startsWith("/api/roles")) return Promise.resolve({ roles: [], total: 0 });
    return Promise.resolve({});
  });
}

const openUser = async (email: string) => fireEvent.click(await screen.findByText(email));

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  canAccess.mockReturnValue(true);
});

const without = (grant: string) => (resource: string, action: string) => `${resource}:${action}` !== grant;

test.each([
  ["active", ["Lock", "Deactivate"]],
  ["deactivated", ["Reactivate", "Delete"]],
])("a %s user's protected actions show with every grant", async (status, actions) => {
  givenUsers([user(9, "u@test.com", status)]);
  render(<UsersPage />);
  await openUser("u@test.com");
  for (const name of actions) expect(await screen.findByRole("button", { name })).toBeInTheDocument();
});

test.each([
  ["active", "users:deactivate", ["Lock", "Deactivate"]],
  ["deactivated", "users:deactivate", ["Reactivate"]],
  ["deactivated", "users:delete", ["Delete"]],
])("a %s user's actions hide without %s", async (status, grant, hidden) => {
  canAccess.mockImplementation(without(grant));
  givenUsers([user(9, "u@test.com", status)]);
  render(<UsersPage />);
  await openUser("u@test.com");
  await screen.findByRole("button", { name: "Edit" });
  for (const name of hidden) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
});

test("unknown permissions (loading or failed) hide Lock and Deactivate", async () => {
  canAccess.mockImplementation((resource: string, action: string) => resource === "users" && action === "view");
  givenUsers([user(9, "u@test.com", "active")]);
  render(<UsersPage />);
  await openUser("u@test.com");
  await screen.findByRole("button", { name: "Edit" });
  expect(screen.queryByRole("button", { name: "Lock" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
});

test("a grant revoked before confirming sends no request", async () => {
  givenUsers([user(9, "u@test.com", "active")]);
  render(<UsersPage />);
  await openUser("u@test.com");
  fireEvent.click(await screen.findByRole("button", { name: "Lock" }));
  canAccess.mockImplementation(without("users:deactivate"));
  fireEvent.click(screen.getAllByRole("button", { name: "Lock" }).at(-1)!);
  await waitFor(() => expect(screen.getByText(/no longer have permission/)).toBeInTheDocument());
  expect(mockPost).not.toHaveBeenCalled();
});

test("with the grant, Lock asks first and then locks", async () => {
  givenUsers([user(9, "u@test.com", "active")]);
  render(<UsersPage />);
  await openUser("u@test.com");
  fireEvent.click(await screen.findByRole("button", { name: "Lock" }));
  expect(mockPost).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole("button", { name: "Lock" }).at(-1)!);
  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/users/9/lock"));
});
