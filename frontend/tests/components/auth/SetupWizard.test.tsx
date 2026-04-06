import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupWizard } from "@/components/auth/SetupWizard";

// Mock fetch globally
const mockFetch = jest.fn();
global.fetch = mockFetch;

const mockApiPost = jest.fn();
const mockApiPut = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    put: (...args: unknown[]) => mockApiPut(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  setToken: jest.fn(),
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
    mockApiPost.mockResolvedValue({});
    mockApiPut.mockResolvedValue({});
    localStorage.clear();
  });

  it("renders the 9-step indicator on mount", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
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

  it("step 6: Kubernetes is selected by default", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({});
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
