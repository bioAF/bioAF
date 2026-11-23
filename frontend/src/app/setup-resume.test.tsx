/**
 * One route decision for /login and /setup (project 12A, approved D3): from administrator existence,
 * setup completion, the session and effective permission. An unfinished installation with an
 * administrator shows the sign-in form; an authorized administrator then reviews saved setup on
 * /setup. Nothing bounces between the two pages, and nothing falls back to creating an admin.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { fakeServer, type FakeReply } from "@/testing/fakeServer";

type Route = [string, FakeReply];

const mockRouter = { push: jest.fn(), replace: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => mockRouter }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));
jest.mock("@/components/auth/SetupWizard", () => ({
  SetupWizard: ({ mode }: { mode?: string }) => <p>Setup wizard ({mode ?? "initial"})</p>,
}));

import LoginPage from "@/app/login/page";
import SetupPage from "@/app/setup/page";
import { getToken } from "@/lib/auth";
import { endSession, startSession } from "@/lib/session";

const CONFIGURE = { resource: "infrastructure", action: "configure" };

function status(has_admin: boolean, setup_complete: boolean): Route {
  return ["/api/bootstrap/status", { json: { has_admin, setup_complete } }];
}

function me(permissions: { resource: string; action: string }[]): Route {
  return ["/api/auth/me", { json: { role_name: "admin", permissions } }];
}

function paths() {
  return [...mockRouter.push.mock.calls, ...mockRouter.replace.mock.calls].map(([p]) => p);
}

beforeEach(() => {
  localStorage.clear();
  endSession();
  mockRouter.push.mockReset();
  mockRouter.replace.mockReset();
});

test("a fresh browser on an unfinished installation with an admin gets the sign-in form", async () => {
  fakeServer([status(true, false)]);
  render(<LoginPage />);
  expect(await screen.findByLabelText(/email/i)).toBeInTheDocument();
  expect(paths()).toEqual([]);
});

test("signing in to an unfinished installation continues to setup", async () => {
  const user = userEvent.setup();
  fakeServer([status(true, false), ["/api/auth/login", { json: { access_token: "admin.jwt" } }]]);
  render(<LoginPage />);
  await user.type(await screen.findByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/password/i), "pw");
  await user.click(screen.getByRole("button", { name: /sign in|log in/i }));
  await waitFor(() => expect(paths()).toEqual(["/setup"]));
  expect(getToken()).toBe("admin.jwt");
});

test("signing in to a completed installation opens the app", async () => {
  const user = userEvent.setup();
  fakeServer([status(true, true), ["/api/auth/login", { json: { access_token: "admin.jwt" } }]]);
  render(<LoginPage />);
  await user.type(await screen.findByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/password/i), "pw");
  await user.click(screen.getByRole("button", { name: /sign in|log in/i }));
  await waitFor(() => expect(paths()).toEqual(["/"]));
});

test("without an admin, login sends to setup and setup shows the first-run wizard", async () => {
  fakeServer([status(false, false)]);
  render(<LoginPage />);
  await waitFor(() => expect(paths()).toEqual(["/setup"]));
  mockRouter.push.mockReset();
  render(<SetupPage />);
  expect(await screen.findByText("Setup wizard (initial)")).toBeInTheDocument();
  expect(paths()).toEqual([]);
});

test("setup without a session on an unfinished installation sends to sign-in, which stays", async () => {
  fakeServer([status(true, false)]);
  const { unmount } = render(<SetupPage />);
  await waitFor(() => expect(paths()).toEqual(["/login"]));
  unmount();
  render(<LoginPage />);
  expect(await screen.findByLabelText(/email/i)).toBeInTheDocument();
  expect(paths()).toEqual(["/login"]); // no cycle
});

test("an authorized administrator on setup reviews saved setup", async () => {
  startSession("admin.jwt");
  fakeServer([status(true, false), me([CONFIGURE])]);
  render(<SetupPage />);
  expect(await screen.findByText("Setup wizard (resume)")).toBeInTheDocument();
  expect(paths()).toEqual([]);
});

test("a signed-in administrator on login with unfinished setup goes to setup", async () => {
  startSession("admin.jwt");
  fakeServer([status(true, false)]);
  render(<LoginPage />);
  await waitFor(() => expect(paths()).toEqual(["/setup"]));
});

test("a signed-in user without setup permission sees a plain notice and can sign out", async () => {
  const user = userEvent.setup();
  startSession("viewer.jwt");
  fakeServer([status(true, false), me([{ resource: "files", action: "view" }])]);
  render(<SetupPage />);
  expect(await screen.findByRole("heading", { name: "Setup isn't finished yet" })).toBeInTheDocument();
  expect(
    screen.getByText("An administrator needs to finish setting up bioAF before you can use it."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Setup wizard/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sign out" }));
  expect(getToken()).toBeNull();
  expect(paths()).toEqual(["/login"]);
});

test("a completed installation's setup page exits to the app or to sign-in", async () => {
  fakeServer([status(true, true)]);
  const { unmount } = render(<SetupPage />);
  await waitFor(() => expect(paths()).toEqual(["/login"]));
  unmount();
  mockRouter.replace.mockReset();
  startSession("admin.jwt");
  render(<SetupPage />);
  await waitFor(() => expect(paths()).toEqual(["/"]));
});

test("an unreadable status is a retryable error, never the first-run wizard", async () => {
  const user = userEvent.setup();
  let fail = true;
  fakeServer([
    ["/api/bootstrap/status", () => (fail ? { status: 500, json: { detail: "boom" } } : { json: { has_admin: false, setup_complete: false } })],
  ]);
  render(<SetupPage />);
  expect(await screen.findByText("Your saved setup couldn't be loaded.")).toBeInTheDocument();
  expect(screen.queryByText(/Setup wizard/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  fail = false;
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("Setup wizard (initial)")).toBeInTheDocument();
});

test("an expired session on setup returns to a usable sign-in", async () => {
  startSession("old.jwt");
  fakeServer([status(true, false), ["/api/auth/me", { status: 401, json: { detail: "expired" } }]]);
  render(<SetupPage />);
  await waitFor(() => expect(getToken()).toBeNull());
  expect(screen.queryByText(/Setup wizard/)).not.toBeInTheDocument();
});
