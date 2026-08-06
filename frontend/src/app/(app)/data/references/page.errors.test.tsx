/**
 * A failed load must not render as "No reference datasets found".
 *
 * This is the same defect the projects page had, and it survived the round that
 * closed that class: the primary list load here still ended in `.catch(() => {})`,
 * so a 500 from /api/references was indistinguishable from an empty catalogue.
 */
import { render, screen, waitFor } from "@testing-library/react";
import DataReferencesPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
}));

const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

// The page makes two calls: filter options and the list itself. Only the list
// failing is what this is about.
function respond({ list }: { list: "ok" | "fail" }) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("filter-options")) {
      return Promise.resolve({ categories: ["genome"], scopes: ["public"] });
    }
    if (list === "fail") return Promise.reject(new Error("Backend unavailable"));
    return Promise.resolve({ references: [], total: 0 });
  });
}

test("a failed load shows an error with retry, not an empty state", async () => {
  respond({ list: "fail" });
  render(<DataReferencesPage />);

  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(
    /reference data could not be loaded/i,
  );
  expect(screen.getByTestId("error-message")).toHaveTextContent(/logs/i);
  expect(screen.getByTestId("error-message")).not.toHaveTextContent(/backend unavailable/i);
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  expect(screen.queryByText(/no reference datasets found/i)).not.toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("an genuinely empty catalogue still says so", async () => {
  respond({ list: "ok" });
  render(<DataReferencesPage />);

  await waitFor(() =>
    expect(screen.getByText(/no reference datasets found/i)).toBeInTheDocument(),
  );
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
