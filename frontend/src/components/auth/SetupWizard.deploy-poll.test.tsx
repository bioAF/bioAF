/**
 * The Deploying step polls component status every 8 s while it is the current step (plan 13):
 * reads never overlap, leaving the step stops the poll and drops a read in flight, and a failed
 * read is said rather than swallowed.
 */
import { render, screen, waitFor, act } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";

const mockFetch = jest.fn();
const mockApiPost = jest.fn();
const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    put: jest.fn(),
    get: (...args: unknown[]) => mockApiGet(...args),
  },
}));
jest.mock("@/lib/auth", () => ({ setToken: jest.fn(), isAuthenticated: jest.fn(() => false) }));

import { SetupWizard } from "./SetupWizard";

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let componentReads: Pending[];
const realFetch = global.fetch;

function respond(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)) });
}

beforeEach(() => {
  componentReads = [];
  mockFetch.mockReset();
  mockApiPost.mockReset();
  mockApiGet.mockReset();
  global.fetch = mockFetch as unknown as typeof fetch;
  mockApiPost.mockResolvedValue({});
  mockApiGet.mockImplementation((url: string) => {
    if (url === "/api/components") {
      return new Promise((resolve, reject) => componentReads.push({ resolve, reject }));
    }
    return Promise.resolve({});
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

/** Setup code, admin, organization, then "Do this later" three times lands on Deploying. */
async function toDeployingStep() {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  render(<SetupWizard onComplete={jest.fn()} />);
  mockFetch.mockImplementationOnce(() => respond({ setup_token: "setup.tok" }));
  await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
  await user.click(screen.getByRole("button", { name: /verify/i }));
  await screen.findByRole("heading", { name: "Create Admin Account" });
  mockFetch.mockImplementationOnce(() => respond({ access_token: "admin.jwt", token_type: "bearer" }));
  await user.type(screen.getByLabelText(/name/i), "Admin");
  await user.type(screen.getByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/^password$/i), "password123");
  await user.type(screen.getByLabelText(/confirm password/i), "password123");
  await user.click(screen.getByRole("button", { name: /create admin/i }));
  await screen.findByRole("heading", { name: "Organization Name" });
  await user.type(screen.getByLabelText(/organization name/i), "Acme Bio");
  await user.click(screen.getByRole("button", { name: /save organization/i }));
  await screen.findByRole("heading", { name: "GCP Credentials" });
  await user.click(screen.getByRole("button", { name: /do this later/i }));
  await screen.findByRole("heading", { name: "SMTP Settings" });
  await user.click(screen.getByRole("button", { name: /do this later/i }));
  await screen.findByRole("heading", { name: "Infrastructure" });
  await user.click(screen.getByRole("button", { name: /do this later/i }));
  await screen.findByRole("heading", { name: "Deploying" });
  return user;
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}

test("component status reads never overlap and stop when the step is left", async () => {
  jest.useFakeTimers();
  const user = await toDeployingStep();
  await waitFor(() => expect(componentReads.length).toBe(1));

  await tick(60_000);
  expect(componentReads.length).toBe(1);

  await settle(() => componentReads[0].resolve({ components: [] }));
  await tick(8_000);
  expect(componentReads.length).toBe(2);

  await user.click(screen.getByRole("button", { name: /Continue to Getting Started/i }));
  await settle(() => componentReads[1].resolve({ components: [] }));
  await tick(60_000);
  expect(componentReads.length).toBe(2);
});

test("a failed component status read is said, and the next read recovers", async () => {
  jest.useFakeTimers();
  await toDeployingStep();
  await waitFor(() => expect(componentReads.length).toBe(1));

  await settle(() => componentReads[0].reject(new Error("502")));
  expect(screen.getByTestId("deploy-status-unavailable")).toBeInTheDocument();

  await tick(8_000);
  await settle(() => componentReads[1].resolve({ components: [] }));
  expect(screen.queryByTestId("deploy-status-unavailable")).not.toBeInTheDocument();
});
