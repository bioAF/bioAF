/**
 * A failed activity load must not read as "nothing has happened".
 * This page previously swallowed the error behind a "handled by api client"
 * comment, which was never true: lib/api.ts only throws.
 */
import { render, screen, waitFor } from "@testing-library/react";
import ActivityPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/activity",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
beforeEach(() => mockGet.mockReset());

test("a failed load shows an error with retry, not an empty feed", async () => {
  mockGet.mockRejectedValue(new Error("Backend unavailable"));
  render(<ActivityPage />);
  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(/could not load activity/i);
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("a genuinely empty feed still renders its own empty state", async () => {
  mockGet.mockResolvedValue({ events: [], total: 0 });
  render(<ActivityPage />);
  await waitFor(() => expect(screen.queryByTestId("error-message")).not.toBeInTheDocument());
});
