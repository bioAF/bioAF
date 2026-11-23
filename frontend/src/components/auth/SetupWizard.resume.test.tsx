/**
 * Reviewing an unfinished installation's saved setup (project 12A, approved D3).
 *
 * An authorized administrator sees the summary card, then reviews saved settings from the
 * organization step. Unchanged values are never written again (no PUT, validation, batch or
 * completion replay); infrastructure work already accepted is shown, not repeated; enabled
 * components stay selected; and a failed completion stays on its step with a retry.
 */
import { render, screen, waitFor, within } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import { fakeServer, type FakeReply, type RecordedRequest } from "@/testing/fakeServer";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import { SetupWizard } from "./SetupWizard";
import { endSession, startSession } from "@/lib/session";

type Route = [string, FakeReply | ((req: RecordedRequest) => FakeReply)];

const OP = {
  id: 42,
  action: "compute_deploy",
  label: "Compute deployment",
  steps: [],
  accepted_at: "2026-11-22T10:00:00Z",
  started_at: "2026-11-22T10:01:00Z",
};

const PERMISSIONS = ["configure", "deploy", "view"].map((action) => ({ resource: "infrastructure", action }));

function server(overrides: Route[] = [], { operation = null as null | Record<string, unknown>, busy = false } = {}) {
  const routes: Route[] = [
    ...overrides,
    ["/api/bootstrap/status", { json: { cloud_provider: "gcp", has_admin: true, setup_complete: false } }],
    ["/api/bootstrap/org-settings", { json: { org_name: "Lab North" } }],
    [
      "/api/system-settings",
      { json: { install_metrics: { preference: "enabled", revision: 1, notice_version: "2026-11-15" } } },
    ],
    [
      "/api/v1/settings/gcp/validate",
      { json: { passed: true, checks: [], permission_details: [], app_probe: null, bootstrap_probe: null } },
    ],
    [
      "/api/v1/settings/gcp",
      {
        json: {
          gcp_project_id: "lab-north",
          gcp_region: "us-central1",
          gcp_zone: "us-central1-a",
          org_slug: "north",
          gcp_credential_source: "vm_default",
          gcp_service_account_email: null,
          gcp_bootstrap_sa_email: "bootstrap@lab-north.iam",
          gcp_credentials_configured: true,
          gcp_validation_status: '[{"name":"auth","passed":true,"message":"ok"}]',
        },
      },
    ],
    [
      "/api/bootstrap/smtp-settings",
      {
        json: {
          host: "smtp.north",
          port: 465,
          username: "mailer",
          password: "ab***yz",
          from_address: "lab@north.org",
          encryption: "ssl",
          configured: true,
        },
      },
    ],
    ["/api/components/select-batch", { json: { queued: [] } }],
    [
      "/api/components",
      {
        json: {
          components: [
            { key: "nextflow", name: "Nextflow", status: "enabled", enabled: true },
            { key: "jupyterhub", name: "JupyterHub", status: "disabled", enabled: false },
          ],
        },
      },
    ],
    [
      "/api/v1/infrastructure/stack/components",
      {
        json: {
          compute_stack: "kubernetes",
          components: ["nextflow", "jupyterhub", "rstudio"].map((key) => ({
            key,
            name: key,
            description: "",
            category: "analysis",
            dependencies: [],
            cost_estimate: "",
            status: "disabled",
          })),
        },
      },
    ],
    [
      "/api/v1/infrastructure/operations/active",
      { json: { busy, state: busy ? "running" : null, operation: busy ? operation : null, interrupted: [] } },
    ],
    ["/api/v1/infrastructure/operations/42", { json: operation ?? {} }],
    ["/api/v1/infrastructure/operations", { json: { operations: operation ? [operation] : [] } }],
    ["/api/auth/me", { json: { role_name: "admin", permissions: PERMISSIONS } }],
    ["/api/bootstrap/complete", { json: { message: "Setup complete" } }],
    ["/api/bootstrap/configure-smtp", { json: { message: "SMTP configured" } }],
  ];
  return fakeServer(routes);
}

function writes(requests: RecordedRequest[]) {
  return requests.filter((r) => r.method !== "GET").map((r) => `${r.method} ${new URL(r.url, "http://x").pathname}`);
}

async function reviewSavedSetup(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Review saved setup" }));
  await screen.findByRole("heading", { name: "Organization Name" });
}

async function continueTo(user: ReturnType<typeof userEvent.setup>, heading: string, button: RegExp) {
  await user.click(screen.getByRole("button", { name: button }));
  await screen.findByRole("heading", { name: heading });
}

beforeEach(() => {
  localStorage.clear();
  endSession();
  startSession("admin.jwt");
});

test("the summary card is shown first and nothing is written by opening it", async () => {
  const { requests } = server();
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  expect(await screen.findByText("Finish setting up bioAF")).toBeInTheDocument();
  expect(screen.getByText("Review your saved settings to finish setup.")).toBeInTheDocument();
  expect(screen.queryByText(/Infrastructure deployment:/)).not.toBeInTheDocument();
  expect(screen.queryByPlaceholderText("Enter 6-character code")).not.toBeInTheDocument();
  await waitFor(() => expect(requests.some((r) => r.url.includes("/org-settings"))).toBe(true));
  expect(writes(requests)).toEqual([]);
});

test("review opens the organization step with the saved name, and unchanged settings write nothing", async () => {
  const user = userEvent.setup();
  const { requests } = server();
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await reviewSavedSetup(user);
  expect(screen.getByDisplayValue("Lab North")).toBeInTheDocument();
  // Setup code and admin creation can never be re-entered.
  expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();

  await continueTo(user, "GCP Credentials", /save|continue/i);
  await waitFor(() => expect(screen.getByDisplayValue("lab-north")).toBeInTheDocument());
  await continueTo(user, "SMTP Settings", /save & validate/i);
  expect(screen.getByDisplayValue("smtp.north")).toBeInTheDocument();
  expect(screen.getByLabelText(/password/i)).toHaveValue(""); // the masked secret never comes back
  await continueTo(user, "Infrastructure", /save/i);

  expect(writes(requests)).toEqual([]);
});

test("unchanged but unvalidated credentials are validated without being saved again", async () => {
  const user = userEvent.setup();
  const { requests } = server([
    [
      "/api/v1/settings/gcp/validate",
      { json: { passed: true, checks: [], permission_details: [], app_probe: null, bootstrap_probe: null } },
    ],
    [
      "/api/v1/settings/gcp",
      {
        json: {
          gcp_project_id: "lab-north",
          gcp_region: "us-central1",
          gcp_zone: "us-central1-a",
          org_slug: "north",
          gcp_credential_source: "vm_default",
          gcp_credentials_configured: false,
          gcp_validation_status: "not json",
        },
      },
    ],
  ]);
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await reviewSavedSetup(user);
  await continueTo(user, "GCP Credentials", /save|continue/i);
  await waitFor(() => expect(screen.getByDisplayValue("lab-north")).toBeInTheDocument());
  await continueTo(user, "SMTP Settings", /save & validate/i);
  expect(writes(requests)).toEqual(["POST /api/v1/settings/gcp/validate"]);
});

test("changed credentials are saved and validated", async () => {
  const user = userEvent.setup();
  const { requests } = server();
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await reviewSavedSetup(user);
  await continueTo(user, "GCP Credentials", /save|continue/i);
  const project = await screen.findByDisplayValue("lab-north");
  await user.clear(project);
  await user.type(project, "lab-south");
  await continueTo(user, "SMTP Settings", /save & validate/i);
  expect(writes(requests)).toEqual(["PUT /api/v1/settings/gcp", "POST /api/v1/settings/gcp/validate"]);
});

test("a changed SMTP field keeps the saved encryption and the stored password", async () => {
  const user = userEvent.setup();
  const { requests } = server();
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await reviewSavedSetup(user);
  await continueTo(user, "GCP Credentials", /save|continue/i);
  await waitFor(() => expect(screen.getByDisplayValue("lab-north")).toBeInTheDocument());
  await continueTo(user, "SMTP Settings", /save & validate/i);
  const host = screen.getByDisplayValue("smtp.north");
  await user.clear(host);
  await user.type(host, "smtp.south");
  await user.click(screen.getByRole("button", { name: /save/i }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Overwrite" }));
  await screen.findByRole("heading", { name: "Infrastructure" });
  const post = requests.find((r) => r.url.includes("/configure-smtp"))!;
  expect(JSON.parse(String(post.body))).toMatchObject({ host: "smtp.south", encryption: "ssl", password: "" });
});

async function toInfrastructure(user: ReturnType<typeof userEvent.setup>) {
  await reviewSavedSetup(user);
  await continueTo(user, "GCP Credentials", /save|continue/i);
  await waitFor(() => expect(screen.getByDisplayValue("lab-north")).toBeInTheDocument());
  await continueTo(user, "SMTP Settings", /save & validate/i);
  await continueTo(user, "Infrastructure", /save/i);
}

test("an accepted deployment is shown instead of being created again", async () => {
  const user = userEvent.setup();
  const operation = { ...OP, status: "running" };
  const { requests } = server([], { operation, busy: true });
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  expect(await screen.findByText(/Infrastructure deployment: Running/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "View deployment" })).toBeInTheDocument();
  await toInfrastructure(user);
  expect(screen.getByText("Deployment already in progress")).toBeInTheDocument();
  expect(screen.getByText(/^Started .+\. This usually takes 10-15 minutes\.$/)).toBeInTheDocument();
  expect(screen.queryByText("Create cloud infrastructure now?")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Set up infrastructure" })).not.toBeInTheDocument();
  await continueTo(user, "Select Components", /continue to components/i);
  expect(writes(requests)).toEqual([]);
});

test("a failed deployment offers details and an explicit retry, never a silent replay", async () => {
  const user = userEvent.setup();
  const operation = { ...OP, status: "failed" };
  const { requests } = server([["/stack/deploy-background", { json: { operation_id: 43 } }]], { operation });
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await toInfrastructure(user);
  expect(screen.getByText("The last deployment didn't finish")).toBeInTheDocument();
  expect(screen.getByText("Your settings are saved. Review what happened before trying again.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "View details" })).toBeInTheDocument();
  const retry = screen.getByRole("button", { name: "Retry deployment" });
  await waitFor(() => expect(retry).toBeEnabled());
  expect(writes(requests)).toEqual([]);

  await user.click(retry);
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Retry deployment" }));
  await waitFor(() => expect(writes(requests)).toEqual(["POST /api/v1/infrastructure/stack/deploy-background"]));
  const deploy = requests.find((r) => r.url.includes("deploy-background"))!;
  expect(deploy.headers["Idempotency-Key"]).toBeTruthy();
});

test("a deployment needing recovery cannot be retried until it is recovered", async () => {
  const user = userEvent.setup();
  const operation = { ...OP, status: "recovery_required" };
  server([], { operation, busy: true });
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await toInfrastructure(user);
  expect(screen.getByText("The last deployment didn't finish")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry deployment" })).toBeDisabled();
});

async function toComponents(user: ReturnType<typeof userEvent.setup>) {
  await toInfrastructure(user);
  await continueTo(user, "Select Components", /continue to components/i);
}

test("saved components stay selected, only additions are sent, and a failed completion can be retried", async () => {
  const user = userEvent.setup();
  let completeFails = true;
  const operation = { ...OP, status: "running" };
  const { requests } = server(
    [["/api/bootstrap/complete", () => (completeFails ? { status: 500, json: { detail: "db" } } : { json: {} })]],
    { operation, busy: true },
  );
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  await toComponents(user);
  const nextflow = await screen.findByRole("checkbox", { name: "nextflow" });
  expect(nextflow).toBeChecked();
  expect(nextflow).toBeDisabled(); // already enabled: the additive endpoint cannot remove it
  await user.click(screen.getByRole("checkbox", { name: "rstudio" }));

  await user.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByText("Setup couldn't be marked complete. Your settings are saved.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Select Components" })).toBeInTheDocument();
  const batch = requests.filter((r) => r.url.includes("select-batch"));
  expect(batch.map((r) => JSON.parse(String(r.body)).keys)).toEqual([["rstudio"]]);

  completeFails = false;
  await user.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("heading", { name: "Deploying" });
  expect(requests.filter((r) => r.url.includes("select-batch"))).toHaveLength(1); // not replayed
});

test("an unreadable saved setup is a retryable error, never an empty form", async () => {
  server([["/api/bootstrap/org-settings", { status: 500, json: { detail: "boom" } }]]);
  render(<SetupWizard mode="resume" onComplete={() => {}} />);
  expect(await screen.findByText("Your saved setup couldn't be loaded.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review saved setup" })).not.toBeInTheDocument();
});
