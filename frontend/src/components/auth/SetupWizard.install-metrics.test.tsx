/**
 * The installation metrics choice on setup's Organization Name step (ADR-075). Neither choice is
 * preselected and the step cannot be saved until one is chosen. Either choice is saved, with the
 * notice version, before the organization name; then both continue the same setup flow. A failed
 * save is retryable and does not advance.
 */
import { fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { SetupWizard } from "./SetupWizard";
import { INSTALL_METRICS_NOTICE_VERSION } from "@/lib/installMetrics";

const mockFetch = jest.fn();
const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}));
jest.mock("@/lib/session", () => ({ ...jest.requireActual("@/lib/session"), startSession: jest.fn() }));

const realFetch = global.fetch;
const order: string[] = [];

function respond(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

function saved(preference: "enabled" | "disabled", revision = 1) {
  return {
    install_metrics: {
      preference,
      prompt_required: false,
      effective: preference === "enabled",
      operator_override: false,
      setup_completed: false,
      installation_id: preference === "enabled" ? "6d3f0a7e-0000-4000-8000-000000000001" : null,
      notice_version: INSTALL_METRICS_NOTICE_VERSION,
      decided_at: "2026-11-20T15:30:00Z",
      revision,
    },
  };
}

beforeEach(() => {
  order.length = 0;
  mockFetch.mockReset();
  mockGet.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  global.fetch = mockFetch as unknown as typeof fetch;
  localStorage.clear();
  sessionStorage.clear();
  mockPost.mockImplementation((url: string) => {
    order.push(`POST ${url}`);
    return Promise.resolve({});
  });
  mockPut.mockImplementation((url: string, body: { choice: "enabled" | "disabled" }) => {
    order.push(`PUT ${url}`);
    return Promise.resolve(saved(body.choice));
  });
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/bootstrap/status")) return Promise.resolve({ cloud_provider: "gcp" });
    return Promise.resolve({});
  });
  mockFetch.mockImplementation((url: string) => {
    if (url.includes("verify-setup-code")) return respond(200, { setup_token: "setup-jwt" });
    if (url.includes("create-admin")) return respond(200, { access_token: "admin-jwt" });
    return respond(404, {});
  });
});

afterEach(() => {
  global.fetch = realFetch;
});

async function toOrganizationStep() {
  render(<SetupWizard onComplete={() => {}} />);
  fireEvent.change(screen.getByLabelText("Setup Code"), { target: { value: "ABC123" } });
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
  await screen.findByRole("heading", { name: "Create Admin Account" });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@lab.org" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "s3cret-pass" } });
  fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "s3cret-pass" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));
  await screen.findByRole("heading", { name: "Organization Name" });
  fireEvent.change(screen.getByLabelText("Organization Name"), { target: { value: "Acme" } });
}

const save = () => screen.getByRole("button", { name: "Save Organization Name" });

test("the disclosure is shown with neither choice selected and the step cannot be saved", async () => {
  await toOrganizationStep();

  expect(screen.getByText(/Help improve bioAF by sharing optional installation metrics/)).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Share installation metrics" })).not.toBeChecked();
  expect(screen.getByRole("radio", { name: "Do not share" })).not.toBeChecked();
  expect(save()).toBeDisabled();
  expect(mockPut).not.toHaveBeenCalled();
});

test.each([
  ["Share installation metrics", "enabled"],
  ["Do not share", "disabled"],
] as const)("choosing %s saves the choice before the organization name, then continues", async (label, choice) => {
  await toOrganizationStep();

  fireEvent.click(screen.getByRole("radio", { name: label }));
  expect(save()).toBeEnabled();
  fireEvent.click(save());

  await screen.findByRole("heading", { name: "GCP Credentials" });
  expect(mockPut).toHaveBeenCalledWith("/api/system-settings/install-metrics", {
    choice,
    notice_version: INSTALL_METRICS_NOTICE_VERSION,
    revision: 0,
  });
  expect(order).toEqual(["PUT /api/system-settings/install-metrics", "POST /api/bootstrap/configure-org"]);
});

test("a failed save shows a retryable error, does not advance, and saves nothing else", async () => {
  mockPut.mockRejectedValueOnce(Object.assign(new Error("Server error"), { status: 500 }));
  await toOrganizationStep();

  fireEvent.click(screen.getByRole("radio", { name: "Do not share" }));
  fireEvent.click(save());

  await screen.findByText(/installation metrics choice was not saved/i);
  expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalledWith("/api/bootstrap/configure-org", expect.anything());

  fireEvent.click(save());
  await screen.findByRole("heading", { name: "GCP Credentials" });
  expect(mockPut).toHaveBeenCalledTimes(2);
});

test("a choice another admin already committed is respected and setup continues", async () => {
  mockPut.mockRejectedValueOnce(Object.assign(new Error("revision_conflict"), { status: 409 }));
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/bootstrap/status")) return Promise.resolve({ cloud_provider: "gcp" });
    if (url === "/api/system-settings") return Promise.resolve(saved("disabled"));
    return Promise.resolve({});
  });
  await toOrganizationStep();

  fireEvent.click(screen.getByRole("radio", { name: "Share installation metrics" }));
  fireEvent.click(save());

  await screen.findByRole("heading", { name: "GCP Credentials" });
  expect(mockPut).toHaveBeenCalledTimes(1);
});

test("returning to the step shows the saved choice and never asks again", async () => {
  await toOrganizationStep();
  fireEvent.click(screen.getByRole("radio", { name: "Share installation metrics" }));
  fireEvent.click(save());
  await screen.findByRole("heading", { name: "GCP Credentials" });

  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("heading", { name: "Organization Name" });

  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  expect(screen.getByText(/Installation metrics: Share installation metrics/)).toBeInTheDocument();
  fireEvent.click(save());
  await screen.findByRole("heading", { name: "GCP Credentials" });
  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
});
