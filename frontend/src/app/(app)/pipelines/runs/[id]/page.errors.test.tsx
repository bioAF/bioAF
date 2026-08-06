/**
 * A failed load must not claim the run does not exist.
 *
 * This page swallowed the detail load and then rendered "Run not found", which
 * is worse than rendering emptiness: it is a false statement about the user's
 * data. A 500, an expired session and a deleted run all read identically.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import PipelineRunDetailPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => ({ id: "42" }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
// ApiError must be the real class: the page distinguishes a 404 from an outage
// with `instanceof`, and a bare object mock would make that check throw.
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed load reports the failure instead of 'Run not found'", async () => {
  mockGet.mockRejectedValue(new Error("Backend unavailable"));
  render(<PipelineRunDetailPage />);

  await waitFor(() => expect(screen.getByTestId("error-message")).toBeInTheDocument());
  expect(screen.getByTestId("error-message")).toHaveTextContent(
    /this run could not be loaded/i,
  );
  expect(screen.getByTestId("error-message")).toHaveTextContent(/logs/i);
  // The technical text belongs in the logs, never on screen.
  expect(screen.getByTestId("error-message")).not.toHaveTextContent(/backend unavailable/i);
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  expect(screen.queryByText(/run not found/i)).not.toBeInTheDocument();
  expect(screen.getByTestId("error-retry")).toBeInTheDocument();
});

test("a run that really is missing still says so", async () => {
  // A 404 is a genuine "not found", and must keep reading that way.
  const { ApiError } = jest.requireActual("@/lib/api");
  mockGet.mockRejectedValue(new ApiError(404, "Not found"));
  render(<PipelineRunDetailPage />);

  await waitFor(() => expect(screen.getByText(/run not found/i)).toBeInTheDocument());
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
