/**
 * A failed load must not render as "No experiments found". The experiment list
 * is the entry point to the science workspace, so an outage here reading as
 * "you have no experiments" is the worst version of this defect in the app.
 */
import { render, screen, waitFor } from "@testing-library/react";
import ExperimentsPage from "./page";

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

function respond({ list }: { list: "ok" | "fail" }) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.resolve({ projects: [] });
    if (list === "fail") return Promise.reject(new Error("Backend unavailable"));
    return Promise.resolve({ experiments: [], total: 0 });
  });
}

test("a failed load shows an error with retry, not an empty state", async () => {
  respond({ list: "fail" });
  render(<ExperimentsPage />);

  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(
    /experiments could not be loaded/i,
  );
  expect(screen.getByTestId("error-message")).toHaveTextContent(/logs/i);
  expect(screen.getByTestId("error-message")).not.toHaveTextContent(/backend unavailable/i);
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  expect(screen.queryByText(/no experiments found/i)).not.toBeInTheDocument();
});

test("an account with no experiments still says so", async () => {
  respond({ list: "ok" });
  render(<ExperimentsPage />);

  await waitFor(() => expect(screen.getByText(/no experiments found/i)).toBeInTheDocument());
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
