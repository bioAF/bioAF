/**
 * A failed load must not render as "No projects found".
 *
 * This page previously swallowed the error behind a comment claiming it was
 * "handled by api client". It was not: lib/api.ts only throws, and there was no
 * notification layer anywhere in the frontend to catch it.
 */
import { render, screen, waitFor } from "@testing-library/react";
import ProjectsPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
// The router object must be STABLE across renders: this page lists `router` in a
// useEffect dependency array, so a fresh object per render re-runs the load in a
// loop and the assertions never settle.
const routerMock = { push: jest.fn() };
const searchParamsMock = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => searchParamsMock,
  usePathname: () => "/projects",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("a failed load shows an error with retry, not an empty state", async () => {
  mockGet.mockRejectedValue(new Error("Backend unavailable"));
  render(<ProjectsPage />);

  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(/could not load projects/i);
  expect(screen.queryByText(/no projects found/i)).not.toBeInTheDocument();
});

test("a genuinely empty account still says so", async () => {
  mockGet.mockResolvedValue({ projects: [], total: 0 });
  render(<ProjectsPage />);

  await waitFor(() => expect(screen.getByText(/no projects found/i)).toBeInTheDocument());
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
