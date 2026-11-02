/**
 * An admin can activate an invited user directly. It is a separate, confirmed
 * action: a password reset alone never changes a user's status.
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

test("Activate asks first, then activates", async () => {
  givenUsers([user(9, "invited@test.com", "invited")]);
  render(<UsersPage />);
  await openUser("invited@test.com");

  fireEvent.click(await screen.findByRole("button", { name: "Activate" }));
  expect(mockPost).not.toHaveBeenCalled();
  expect(await screen.findByText(/without accepting their invitation/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Activate" }).at(-1)!);

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/users/9/activate"));
});

test("only invited users are offered Activate", async () => {
  givenUsers([user(9, "active@test.com", "active")]);
  render(<UsersPage />);
  await openUser("active@test.com");

  await screen.findByRole("button", { name: "Edit" });
  expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
});

test("Activate needs users:deactivate, the grant its route enforces", async () => {
  canAccess.mockImplementation((resource: string, action: string) => !(resource === "users" && action === "deactivate"));
  givenUsers([user(9, "invited@test.com", "invited")]);
  render(<UsersPage />);
  await openUser("invited@test.com");

  await screen.findByRole("button", { name: "Edit" });
  expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
});

