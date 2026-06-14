import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupWizard } from "@/components/auth/SetupWizard";

// Mock fetch globally
const mockFetch = jest.fn();
global.fetch = mockFetch;

const mockApiPost = jest.fn();
const mockApiPut = jest.fn();
const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    put: (...args: unknown[]) => mockApiPut(...args),
    get: (...args: unknown[]) => mockApiGet(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  setToken: jest.fn(),
  // SetupWizard transitively imports isAuthenticated via useStackOptions; return
  // false so the hook uses GCP defaults without an extra fetch in these tests.
  isAuthenticated: jest.fn(() => false),
}));

function mockFetchResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** Advance from step 0 (Setup Code) through to the GCP step (step 3). */
async function advanceToGcpStep(user: ReturnType<typeof userEvent.setup>) {
  // Step 0 -> 1: verify setup code
  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { setup_token: "fake-jwt", message: "Setup code verified" })
  );
  await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
  await user.click(screen.getByRole("button", { name: /verify/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());

  // Step 1 -> 2: create admin
  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { access_token: "admin-jwt", token_type: "bearer", message: "ok" })
  );
  await user.type(screen.getByLabelText(/name/i), "Admin");
  await user.type(screen.getByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/^password$/i), "password123");
  await user.type(screen.getByLabelText(/confirm password/i), "password123");
  await user.click(screen.getByRole("button", { name: /create admin/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument());

  // Step 2 -> 3: org name
  mockApiPost.mockResolvedValueOnce({});
  await user.type(screen.getByLabelText(/organization name/i), "Acme Bio");
  await user.click(screen.getByRole("button", { name: /save organization/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument());
}

/** Advance from GCP step to Compute Stack step (step 6) via skip path. */
async function advanceToComputeStep(user: ReturnType<typeof userEvent.setup>) {
  await advanceToGcpStep(user);

  // Step 3 -> 4 (skip GCP)
  await user.click(screen.getByRole("button", { name: /do this later/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "SMTP Settings" })).toBeInTheDocument());

  // Step 4 -> 5 (skip SMTP)
  await user.click(screen.getByRole("button", { name: /do this later/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Infrastructure" })).toBeInTheDocument());
}

describe("SetupWizard", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockApiPost.mockReset();
    mockApiPut.mockReset();
    mockApiGet.mockReset();
    mockApiPost.mockResolvedValue({});
    mockApiPut.mockResolvedValue({});
    // Default api.get router: GCP prefill returns an empty config; everything
    // else returns an empty shape. Per-test setup uses mockImplementationOnce
    // to override specific URLs. This keeps the prefill effect (which can
    // fire on every step >= 3) from eating the once-mocks of other endpoints.
    mockApiGet.mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/v1/settings/gcp")) {
        return Promise.resolve({
          gcp_project_id: null,
          gcp_region: null,
          gcp_zone: null,
          org_slug: null,
          gcp_credential_source: "vm_default",
          gcp_service_account_email: null,
          gcp_bootstrap_sa_email: null,
        });
      }
      return Promise.resolve({});
    });
    localStorage.clear();
  });

  it("renders the 10-step indicator on mount", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByText("1")).toBeInTheDocument();
    // Step 8 was "Select Components" -- a new step inserted after Select Stack
    // and before Deploying. Indicator must reflect the new total.
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("step 0: shows Setup Code form", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByRole("heading", { name: "Setup Code" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter 6-character code")).toBeInTheDocument();
  });

  it("step 1: shows error when passwords do not match", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);

    // Advance to step 1
    mockFetch.mockImplementationOnce(() =>
      mockFetchResponse(200, { setup_token: "fake-jwt", message: "ok" })
    );
    await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
    await user.click(screen.getByRole("button", { name: /verify/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());

    await user.type(screen.getByLabelText(/email/i), "admin@bioaf.org");
    await user.type(screen.getByLabelText(/^password$/i), "abc");
    await user.type(screen.getByLabelText(/confirm password/i), "xyz");
    await user.click(screen.getByRole("button", { name: /create admin/i }));

    expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
  });

  it("step 3: GCP Credentials appears after Organization Name", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
  });

  it("step 3: Save & Validate saves GCP config then validates", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [] });

    await user.type(screen.getByLabelText("GCP Project ID"), "my-project");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));

    await screen.findByRole("heading", { name: "SMTP Settings" });

    expect(mockApiPut).toHaveBeenCalledWith("/api/v1/settings/gcp", expect.objectContaining({
      gcp_project_id: "my-project",
    }));
    expect(mockApiPost).toHaveBeenCalledWith("/api/v1/settings/gcp/validate");
  });

  it("Back button: step 3 (GCP) returns to step 2 (Org Name) with state preserved", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Back$/i }));

    expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument();
    // Org name input still holds the previously-entered value
    expect(screen.getByLabelText(/organization name/i)).toHaveValue("Acme Bio");
  });

  it("Forward button appears on a step that has already been completed", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);
    // Step 3 (GCP) is now active, but step 1 (Admin) and step 2 (Org) have
    // been completed during advanceToGcpStep. Going back to step 1...
    await user.click(screen.getByRole("button", { name: /^Back$/i }));
    await user.click(screen.getByRole("button", { name: /^Back$/i }));
    expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument();
    // ...should now show a Forward affordance the user can use to advance
    // without re-running create-admin.
    expect(screen.getByRole("button", { name: /^Forward$/i })).toBeInTheDocument();
  });

  it("Forward button is absent on a step that has NOT been completed", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByRole("heading", { name: "Setup Code" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Forward$/i })).not.toBeInTheDocument();
  });

  it("Re-submitting Create Admin with unchanged values skips the backend call", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    // Go back to step 1, then click Create Admin Account again without edits.
    // The setup-token fetch should NOT be re-invoked.
    await user.click(screen.getByRole("button", { name: /^Back$/i }));
    await user.click(screen.getByRole("button", { name: /^Back$/i }));

    const callsBeforeResubmit = mockFetch.mock.calls.length;
    await user.click(screen.getByRole("button", { name: /create admin/i }));

    // No new fetch call, but we did advance to the next step.
    expect(mockFetch.mock.calls.length).toBe(callsBeforeResubmit);
    expect(await screen.findByRole("heading", { name: "Organization Name" })).toBeInTheDocument();
  });

  it("Forward button advances to the next step without any backend call", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);
    await user.click(screen.getByRole("button", { name: /^Back$/i }));
    await user.click(screen.getByRole("button", { name: /^Back$/i }));

    const fetchBefore = mockFetch.mock.calls.length;
    const postBefore = mockApiPost.mock.calls.length;

    await user.click(screen.getByRole("button", { name: /^Forward$/i }));

    expect(await screen.findByRole("heading", { name: "Organization Name" })).toBeInTheDocument();
    expect(mockFetch.mock.calls.length).toBe(fetchBefore);
    expect(mockApiPost.mock.calls.length).toBe(postBefore);
  });

  it("Back button is absent on step 0 (nothing to go back to)", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByRole("heading", { name: "Setup Code" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Back$/i })).not.toBeInTheDocument();
  });

  it("Back button is absent on Select Components (step 7) because TF deploy is in flight", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], permission_details: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));

    mockApiPost.mockResolvedValueOnce({}); // terraform/init
    mockApiPost.mockResolvedValueOnce({}); // stack/deploy-background
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/infrastructure/stack/components")) {
        return Promise.resolve({
          compute_stack: "kubernetes",
          compute_deployed: false,
          storage_deployed: false,
          components: [
            { key: "nextflow", name: "Nextflow", description: ".", category: "pipeline_orchestration", dependencies: [], cost_estimate: "$0", status: "disabled", configurable: false },
          ],
        });
      }
      return Promise.resolve({});
    });
    await user.click(screen.getByRole("button", { name: /Continue with Kubernetes/i }));
    await screen.findByRole("heading", { name: "Select Components" });

    // Once TF deploy has fired, no going back.
    expect(screen.queryByRole("button", { name: /^Back$/i })).not.toBeInTheDocument();
  });

  it("step 6: renders Kubernetes (recommended) and SLURM (coming soon) cards", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    // Configure GCP so infra button is enabled
    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    // Skip SMTP
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await screen.findByRole("heading", { name: "Infrastructure" });

    // Set up infra
    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));
    await screen.findByRole("heading", { name: "Select Stack" });

    expect(screen.getByTestId("compute-stack-kubernetes")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes + GCS")).toBeInTheDocument();
    expect(screen.getByText("Recommended")).toBeInTheDocument();
    expect(screen.getByTestId("compute-stack-slurm")).toBeInTheDocument();
  });

  it("step 7: shows Select Components after clicking Continue from stack pick", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], permission_details: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await screen.findByRole("heading", { name: "Infrastructure" });

    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));
    await screen.findByRole("heading", { name: "Select Stack" });

    // After kicking off deploy, wizard advances to component selection so the
    // user can queue components while the cluster is still being built.
    mockApiPost.mockResolvedValueOnce({}); // terraform/init
    mockApiPost.mockResolvedValueOnce({}); // stack/deploy-background
    mockApiPost.mockResolvedValueOnce({}); // bootstrap/complete (now deferred)
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/infrastructure/stack/components")) {
        return Promise.resolve({
          compute_stack: "kubernetes",
          compute_deployed: false,
          storage_deployed: false,
          components: [
            { key: "nextflow", name: "Nextflow", description: "Pipeline orchestration.", category: "pipeline_orchestration", dependencies: ["kubernetes_cluster"], cost_estimate: "$0", status: "disabled", configurable: false },
            { key: "jupyterhub", name: "JupyterHub", description: "Notebooks.", category: "analysis", dependencies: ["kubernetes_cluster"], cost_estimate: "$50", status: "disabled", configurable: true },
            { key: "rstudio", name: "RStudio", description: "RStudio.", category: "analysis", dependencies: ["kubernetes_cluster"], cost_estimate: "$50", status: "disabled", configurable: true },
            { key: "cellxgene", name: "cellxgene", description: "Viz.", category: "visualization", dependencies: [], cost_estimate: "$20", status: "disabled", configurable: false },
            { key: "snakemake", name: "Snakemake", description: "snakemake.", category: "pipeline_orchestration", dependencies: ["kubernetes_cluster"], cost_estimate: "$0", status: "coming_soon", configurable: false },
            { key: "qc_dashboard", name: "QC Dashboard", description: "qc.", category: "visualization", dependencies: ["nextflow"], cost_estimate: "$10", status: "disabled", configurable: false },
            { key: "meilisearch", name: "Meilisearch", description: "search.", category: "search", dependencies: [], cost_estimate: "$20", status: "disabled", configurable: false },
          ],
        });
      }
      return Promise.resolve({});
    });

    await user.click(screen.getByRole("button", { name: /Continue with Kubernetes/i }));
    await screen.findByRole("heading", { name: "Select Components" });

    expect(screen.getByRole("checkbox", { name: /Nextflow/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /JupyterHub/ })).toBeChecked();
  });

  it("step 7: clicking Continue POSTs select-batch and advances to Deploying", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], permission_details: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await screen.findByRole("heading", { name: "Infrastructure" });

    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));
    await screen.findByRole("heading", { name: "Select Stack" });

    mockApiPost.mockResolvedValueOnce({}); // terraform/init
    mockApiPost.mockResolvedValueOnce({}); // stack/deploy-background
    mockApiPost.mockResolvedValueOnce({}); // bootstrap/complete
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/infrastructure/stack/components")) {
        return Promise.resolve({
          compute_stack: "kubernetes",
          compute_deployed: false,
          storage_deployed: false,
          components: [
            { key: "nextflow", name: "Nextflow", description: ".", category: "pipeline_orchestration", dependencies: [], cost_estimate: "$0", status: "disabled", configurable: false },
          ],
        });
      }
      return Promise.resolve({});
    });

    await user.click(screen.getByRole("button", { name: /Continue with Kubernetes/i }));
    await screen.findByRole("heading", { name: "Select Components" });

    mockApiPost.mockResolvedValueOnce({ queued: ["nextflow"] });

    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await screen.findByRole("heading", { name: "Deploying" });
    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/components/select-batch",
      expect.objectContaining({ keys: expect.arrayContaining(["nextflow"]) })
    );
  });

  it("step 8: Deploying step renders per-component status for the just-queued selection", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], permission_details: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));

    // Stack selection
    mockApiPost.mockResolvedValueOnce({}); // terraform/init
    mockApiPost.mockResolvedValueOnce({}); // stack/deploy-background
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/infrastructure/stack/components")) {
        return Promise.resolve({
          compute_stack: "kubernetes",
          compute_deployed: false,
          storage_deployed: false,
          components: [
            { key: "nextflow", name: "Nextflow", description: ".", category: "pipeline_orchestration", dependencies: [], cost_estimate: "$0", status: "disabled", configurable: false },
            { key: "jupyterhub", name: "JupyterHub", description: ".", category: "analysis", dependencies: [], cost_estimate: "$50", status: "disabled", configurable: true },
          ],
        });
      }
      if (url === "/api/components") {
        return Promise.resolve({
          components: [
            { key: "nextflow", name: "Nextflow", description: ".", category: "pipeline_orchestration", enabled: true, status: "queued_for_infra", config: {}, dependencies: [], estimated_monthly_cost: "$0", updated_at: null },
            { key: "jupyterhub", name: "JupyterHub", description: ".", category: "analysis", enabled: true, status: "provisioning", config: {}, dependencies: [], estimated_monthly_cost: "$50", updated_at: null },
          ],
        });
      }
      return Promise.resolve({});
    });
    await user.click(screen.getByRole("button", { name: /Continue with Kubernetes/i }));
    await screen.findByRole("heading", { name: "Select Components" });

    // Submit the components
    mockApiPost.mockResolvedValueOnce({ queued: ["nextflow", "jupyterhub"] });

    await user.click(screen.getByRole("button", { name: /Continue/i }));
    await screen.findByRole("heading", { name: "Deploying" });

    // Each selected component renders with its current status visible
    expect(await screen.findByText("Nextflow")).toBeInTheDocument();
    expect(screen.getByText("JupyterHub")).toBeInTheDocument();
    expect(screen.getByText(/Queued/i)).toBeInTheDocument();
    expect(screen.getByText(/Building/i)).toBeInTheDocument();
  });

  it("step 6: Kubernetes is selected by default", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], permission_details: [] });
    await user.type(screen.getByLabelText("GCP Project ID"), "proj");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));
    await screen.findByRole("heading", { name: "SMTP Settings" });

    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await screen.findByRole("heading", { name: "Infrastructure" });

    await user.click(screen.getByRole("button", { name: /set up infrastructure/i }));
    await screen.findByRole("heading", { name: "Select Stack" });

    expect(screen.getByRole("button", { name: "Continue with Kubernetes + GCS" })).toBeInTheDocument();
  });
});
