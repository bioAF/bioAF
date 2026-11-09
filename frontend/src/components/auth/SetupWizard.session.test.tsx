/**
 * Setup's code check and first-admin request use their own credentials (none, then the setup
 * token), never the session's, and a refusal there is not a session expiry (plan 13). A created
 * admin starts a new session.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { fakeServer } from "@/testing/fakeServer";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
const mockNavigateToLogin = jest.fn();
jest.mock("@/lib/navigation", () => ({ navigateToLogin: () => mockNavigateToLogin() }));

import { SetupWizard } from "./SetupWizard";
import { getToken } from "@/lib/auth";
import { endSession, sessionGeneration } from "@/lib/session";

const realFetch = global.fetch;

beforeEach(() => {
  localStorage.clear();
  endSession();
  mockNavigateToLogin.mockReset();
});
afterEach(() => {
  global.fetch = realFetch;
});

async function toCreateAdmin(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
  await user.click(screen.getByRole("button", { name: /verify/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());
  await user.type(screen.getByLabelText(/name/i), "Admin");
  await user.type(screen.getByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/^password$/i), "password123");
  await user.type(screen.getByLabelText(/confirm password/i), "password123");
  await user.click(screen.getByRole("button", { name: /create admin/i }));
}

test("an invalid setup token stays in setup and does not redirect to login", async () => {
  const user = userEvent.setup();
  const { requests } = fakeServer([
    ["/verify-setup-code", { json: { setup_token: "setup.tok" } }],
    ["/create-admin", { status: 401, json: { detail: "Invalid or expired setup code" } }],
  ]);
  render(<SetupWizard onComplete={() => {}} />);

  await toCreateAdmin(user);

  expect(await screen.findByText(/invalid or expired setup code/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument();
  expect(mockNavigateToLogin).not.toHaveBeenCalled();
  expect(getToken()).toBeNull();
  const verify = requests.find((r) => r.url.includes("/verify-setup-code"))!;
  const create = requests.find((r) => r.url.includes("/create-admin"))!;
  expect(verify.headers.Authorization).toBeUndefined();
  expect(create.headers.Authorization).toBe("Bearer setup.tok");
});

test("the created admin starts a new session", async () => {
  const user = userEvent.setup();
  fakeServer([
    ["/verify-setup-code", { json: { setup_token: "setup.tok" } }],
    ["/create-admin", { json: { access_token: "admin.jwt", token_type: "bearer" } }],
  ]);
  const before = sessionGeneration();
  render(<SetupWizard onComplete={() => {}} />);

  await toCreateAdmin(user);

  await waitFor(() => expect(getToken()).toBe("admin.jwt"));
  expect(sessionGeneration()).not.toBe(before);
});
