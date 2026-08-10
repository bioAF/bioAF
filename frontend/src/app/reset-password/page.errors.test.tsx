/**
 * A reset link that could not be CHECKED is not a reset link that has EXPIRED.
 *
 * `/api/auth/reset-password/validate` returns `{valid: false}` for a genuinely
 * dead token and never raises for one (backend/app/api/auth.py:206-210). So
 * every rejection reaching this page is an outage, and the page was rendering
 * all of them as:
 *
 *   "Link expired or invalid -- This password reset link has expired or is no
 *    longer valid. Reset links are valid for 60 minutes."
 *
 * That is a confident false statement in the one place a locked-out user has
 * left, and the recovery it offers ("Request a new link") goes back through the
 * same backend that just failed, so following it produces the same dead end.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import ResetPasswordPage from "./page";

const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams("token=a-token-nobody-could-judge"),
}));
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

test("an outage does not claim the link expired", async () => {
  mockGet.mockRejectedValue(new Error("Backend unavailable"));
  render(<ResetPasswordPage />);

  await waitFor(() =>
    expect(screen.getByTestId("reset-check-failed")).toBeInTheDocument(),
  );
  expect(screen.queryByText(/expired or is no longer valid/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/valid for 60 minutes/i)).not.toBeInTheDocument();
  // The real error belongs in the logs, never on screen.
  expect(screen.queryByText(/backend unavailable/i)).not.toBeInTheDocument();
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
});

test("the user can try the same link again rather than burning it", async () => {
  mockGet.mockRejectedValue(new Error("down"));
  render(<ResetPasswordPage />);

  await waitFor(() =>
    expect(screen.getByTestId("reset-check-retry")).toBeInTheDocument(),
  );

  // A retry that succeeds must leave the failure state, not strand the user in it.
  mockGet.mockResolvedValue({ valid: true });
  screen.getByTestId("reset-check-retry").click();

  await waitFor(() =>
    expect(screen.getByText(/choose a new password/i)).toBeInTheDocument(),
  );
  expect(screen.queryByTestId("reset-check-failed")).not.toBeInTheDocument();
});

test("a link that really is dead still reads as dead", async () => {
  // The backend answering `{valid: false}` is the genuine verdict and must keep
  // its own wording; only the *unanswered* case changed.
  mockGet.mockResolvedValue({ valid: false });
  render(<ResetPasswordPage />);

  await waitFor(() =>
    expect(screen.getByText(/link expired or invalid/i)).toBeInTheDocument(),
  );
  expect(screen.queryByTestId("reset-check-failed")).not.toBeInTheDocument();
});

test("a missing token is still treated as an invalid link, not an outage", async () => {
  mockGet.mockResolvedValue({ valid: true });
  jest.resetModules();
  // No token in the URL means there is nothing to check; the page must not
  // pretend the backend failed.
  render(<ResetPasswordPage />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
});
