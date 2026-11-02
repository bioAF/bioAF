/**
 * The New Project action follows the projects:create grant, not the role's name,
 * so a custom role holding the grant sees it and one without does not. While
 * permissions load, or when they fail to load, an action stays withheld.
 */
import { render, screen, waitFor } from "@testing-library/react";
import ProjectsPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
const mockSearchParams = { value: new URLSearchParams() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => mockSearchParams.value,
  usePathname: () => "/projects",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

function givenMe(me: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) =>
    url === "/api/auth/me" ? me() : Promise.resolve({ projects: [], total: 0 }),
  );
}

const grant = (role_name: string, resource: string, action: string) => () =>
  Promise.resolve({ role_name, permissions: [{ resource, action }] });

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
  mockSearchParams.value = new URLSearchParams();
});

const newProjectActions = () => screen.queryAllByRole("button", { name: /new project|create project/i });

test("a custom role holding projects:create can create projects", async () => {
  givenMe(grant("lab_manager", "projects", "create"));
  render(<ProjectsPage />);
  await waitFor(() => expect(newProjectActions().length).toBeGreaterThan(0));
});

test("a role named admin without the grant cannot", async () => {
  givenMe(grant("admin", "projects", "view"));
  render(<ProjectsPage />);
  await screen.findByText(/no projects found/i);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  expect(newProjectActions()).toHaveLength(0);
});

test("the action is withheld while permissions load", async () => {
  givenMe(() => new Promise(() => {}));
  render(<ProjectsPage />);
  await screen.findByText(/no projects found/i);
  expect(newProjectActions()).toHaveLength(0);
});

test("the action is withheld when permissions fail to load", async () => {
  givenMe(() => Promise.reject(new Error("boom")));
  render(<ProjectsPage />);
  await screen.findByText(/no projects found/i);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  expect(newProjectActions()).toHaveLength(0);
});

test("?new=1 opens the form once the grant is known", async () => {
  mockSearchParams.value = new URLSearchParams("new=1");
  givenMe(grant("lab_manager", "projects", "create"));
  render(<ProjectsPage />);
  await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
});
