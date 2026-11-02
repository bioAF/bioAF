/**
 * Adding reference data goes through the upload and URL import routes, both of
 * which need references:upload, so the Add action follows that grant.
 */
import { render, screen, waitFor } from "@testing-library/react";
import DataReferencesPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  usePathname: () => "/data/references",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

function pageData(url: string) {
  return url.includes("filter-options")
    ? Promise.resolve({ categories: [], scopes: [] })
    : Promise.resolve({ references: [], total: 0 });
}

function givenMe(me: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => (url === "/api/auth/me" ? me() : pageData(url)));
}

const grant = (role_name: string, resource: string, action: string) => () =>
  Promise.resolve({ role_name, permissions: [{ resource, action }] });

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
});

const addActions = () => screen.queryAllByRole("button", { name: /add reference data/i });

test("a custom role holding references:upload can add reference data", async () => {
  givenMe(grant("core", "references", "upload"));
  render(<DataReferencesPage />);
  await waitFor(() => expect(addActions().length).toBeGreaterThan(0));
});

test("a role named admin without the grant cannot", async () => {
  givenMe(grant("admin", "references", "view"));
  render(<DataReferencesPage />);
  await screen.findByText(/no reference datasets are available yet/i);
  expect(addActions()).toHaveLength(0);
});

test("the action is withheld while permissions load", async () => {
  givenMe(() => new Promise(() => {}));
  render(<DataReferencesPage />);
  await screen.findByText(/no reference datasets are available yet/i);
  expect(addActions()).toHaveLength(0);
});

test("the action is withheld when permissions fail to load", async () => {
  givenMe(() => Promise.reject(new Error("boom")));
  render(<DataReferencesPage />);
  await screen.findByText(/no reference datasets are available yet/i);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  expect(addActions()).toHaveLength(0);
});
