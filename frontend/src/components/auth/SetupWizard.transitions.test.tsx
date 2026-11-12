/**
 * Setup's higher-impact transitions. Each step submit runs once however fast it is pressed (an
 * admin, an organization, cloud credentials, a stack deployment). A step view that unmounts and
 * comes back (Back, then Forward) keeps its values and sends nothing. A provider prefill that
 * arrives after the user typed never replaces what they typed. Secrets are never written to
 * browser storage.
 */
import { act, fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { SetupWizard } from "./SetupWizard";

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
const mockStartSession = jest.fn();
jest.mock("@/lib/session", () => ({
  ...jest.requireActual("@/lib/session"),
  startSession: (token: string) => mockStartSession(token),
}));

const realFetch = global.fetch;
let gcpPrefill: (cfg: unknown) => void;

function respond(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

beforeEach(() => {
  mockFetch.mockReset();
  mockGet.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  mockStartSession.mockReset();
  global.fetch = mockFetch as unknown as typeof fetch;
  localStorage.clear();
  sessionStorage.clear();
  mockPost.mockResolvedValue({});
  mockPut.mockResolvedValue({});
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/v1/settings/gcp")) return new Promise((resolve) => (gcpPrefill = resolve));
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

const calls = (fragment: string) => mockFetch.mock.calls.filter(([url]) => String(url).includes(fragment));

async function toAdminStep() {
  render(<SetupWizard onComplete={() => {}} />);
  fireEvent.change(screen.getByLabelText("Setup Code"), { target: { value: "ABC123" } });
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
  await screen.findByRole("heading", { name: "Create Admin Account" });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@lab.org" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "s3cret-pass" } });
  fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "s3cret-pass" } });
}

async function toGcpStep() {
  await toAdminStep();
  fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));
  await screen.findByRole("heading", { name: "Organization Name" });
  fireEvent.change(screen.getByLabelText("Organization Name"), { target: { value: "Acme" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Organization Name" }));
  await screen.findByRole("heading", { name: "GCP Credentials" });
}

test("the setup code is verified once however fast it is pressed", async () => {
  render(<SetupWizard onComplete={() => {}} />);
  fireEvent.change(screen.getByLabelText("Setup Code"), { target: { value: "ABC123" } });
  const verify = screen.getByRole("button", { name: "Verify" });
  act(() => {
    verify.click();
    verify.click();
  });
  await screen.findByRole("heading", { name: "Create Admin Account" });
  expect(calls("verify-setup-code")).toHaveLength(1);
});

test("the admin is created once however fast it is pressed, and starts one session", async () => {
  await toAdminStep();
  const create = screen.getByRole("button", { name: "Create Admin Account" });
  act(() => {
    create.click();
    create.click();
  });
  await screen.findByRole("heading", { name: "Organization Name" });
  expect(calls("create-admin")).toHaveLength(1);
  expect(mockStartSession).toHaveBeenCalledTimes(1);
  expect(mockStartSession).toHaveBeenCalledWith("admin-jwt");
});

test("Back then Forward remounts the admin step without creating another admin", async () => {
  await toAdminStep();
  fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));
  await screen.findByRole("heading", { name: "Organization Name" });

  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByLabelText("Email")).toHaveValue("ada@lab.org");
  expect(screen.getByLabelText("Password")).toHaveValue("s3cret-pass");
  fireEvent.click(screen.getByRole("button", { name: "Forward" }));
  await screen.findByRole("heading", { name: "Organization Name" });
  expect(calls("create-admin")).toHaveLength(1);
});

test("the GCP prefill never replaces a project ID the user already typed", async () => {
  await toGcpStep();
  fireEvent.change(screen.getByLabelText("GCP Project ID"), { target: { value: "typed-project" } });
  await act(async () =>
    gcpPrefill({
      gcp_project_id: "installer-project",
      gcp_region: "us-east1",
      gcp_zone: "us-east1-b",
      org_slug: "installer-slug",
      gcp_credential_source: "vm_default",
      gcp_service_account_email: null,
      gcp_bootstrap_sa_email: "bootstrap@x.iam.gserviceaccount.com",
    }),
  );
  expect(screen.getByLabelText("GCP Project ID")).toHaveValue("typed-project");
  expect(screen.getByLabelText(/Organization Slug/)).toHaveValue("installer-slug");
});

test("Save & Validate sends the GCP settings once however fast it is pressed", async () => {
  mockPost.mockImplementation((url: string) =>
    Promise.resolve(url.includes("/gcp/validate") ? { passed: true, checks: [], permission_details: [] } : {}),
  );
  await toGcpStep();
  const save = screen.getByRole("button", { name: "Save & Validate" });
  act(() => {
    save.click();
    save.click();
  });
  await screen.findByRole("heading", { name: "SMTP Settings" });
  expect(mockPut).toHaveBeenCalledTimes(1);
});

test("no password, SMTP secret or setup token is written to browser storage", async () => {
  await toGcpStep();
  fireEvent.click(screen.getByRole("button", { name: "Do this later" }));
  await screen.findByRole("heading", { name: "SMTP Settings" });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "smtp-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save SMTP Configuration" }));
  await screen.findByRole("heading", { name: "Infrastructure" });

  const stored = [
    ...Object.keys(localStorage).map((k) => localStorage.getItem(k)),
    ...Object.keys(sessionStorage).map((k) => sessionStorage.getItem(k)),
  ].join("\n");
  for (const secret of ["s3cret-pass", "smtp-secret", "setup-jwt", "ABC123"]) {
    expect(stored).not.toContain(secret);
  }
});

test("starting the stack deployment sends it once, however fast Continue and the confirmation are pressed", async () => {
  mockPost.mockImplementation((url: string) =>
    Promise.resolve(url.includes("/gcp/validate") ? { passed: true, checks: [], permission_details: [] } : {}),
  );
  await toGcpStep();
  fireEvent.click(screen.getByRole("button", { name: "Save & Validate" }));
  await screen.findByRole("heading", { name: "SMTP Settings" });
  fireEvent.click(screen.getByRole("button", { name: "Do this later" }));
  fireEvent.click(await screen.findByRole("button", { name: "Set up infrastructure" }));
  const proceed = await screen.findByRole("button", { name: /Continue with/ });
  act(() => {
    proceed.click();
    proceed.click();
  });
  const create = await screen.findByRole("button", { name: "Create infrastructure" });
  act(() => {
    create.click();
    create.click();
  });
  await screen.findByRole("heading", { name: "Select Components" });
  expect(mockPost.mock.calls.filter(([url]) => url === "/api/v1/infrastructure/stack/deploy-background")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/Create cloud infrastructure now/)).not.toBeInTheDocument());
});
