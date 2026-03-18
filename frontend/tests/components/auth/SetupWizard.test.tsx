import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SetupWizard } from "@/components/auth/SetupWizard";

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

// InviteForm uses api internally; its mock is covered above.

/** Advance the wizard from step 0 to the GCP Configuration step (step 4). */
async function advanceToGcpStep() {
  // Step 0 -> 1
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@b.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
  fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "pw" } });
  fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));
  await screen.findByRole("heading", { name: "Verify Email" });

  // Step 1 -> 2 (skip)
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await screen.findByRole("heading", { name: "Organization Name" });

  // Step 2 -> 3
  mockApiPost.mockResolvedValueOnce({});
  fireEvent.change(screen.getByLabelText("Organization Name"), { target: { value: "Acme Bio" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Organization Name" }));
  await screen.findByRole("heading", { name: "SMTP Configuration" });

  // Step 3 -> 4 (skip)
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await screen.findByRole("heading", { name: "GCP Configuration" });
}

/** Advance from GCP Configuration step to Compute Stack step (skip). */
async function advanceToComputeStep() {
  await advanceToGcpStep();
  // Step 4 -> 5 (skip)
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await screen.findByRole("heading", { name: "Compute Stack" });
}

describe("SetupWizard", () => {
  beforeEach(() => {
    mockApiPost.mockReset();
    mockApiPut.mockReset();
    mockApiPost.mockResolvedValue({ access_token: "tok", email_sent: true });
    mockApiPut.mockResolvedValue({});
  });

  it("renders the 8-step indicator on mount", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    // 8 step circles labeled 1-8
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  it("step 0: shows Create Admin Account form", () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm Password")).toBeInTheDocument();
  });

  it("step 0: shows error when passwords do not match", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "admin@bioaf.org" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "abc" } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "xyz" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));

    expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("step 0: advances to step 1 after successful admin creation", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "admin@bioaf.org" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pass123" } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "pass123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Verify Email" })).toBeInTheDocument();
    });
  });

  it("step 4: GCP Configuration appears after SMTP", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep();

    expect(screen.getByRole("heading", { name: "GCP Configuration" })).toBeInTheDocument();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
    expect(screen.getByText("Organization Slug")).toBeInTheDocument();
  });

  it("step 4: Save & Validate saves GCP config then validates", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToGcpStep();

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [] });

    fireEvent.change(screen.getByLabelText("GCP Project ID"), { target: { value: "my-project" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & Validate" }));

    await screen.findByRole("heading", { name: "Compute Stack" });

    expect(mockApiPut).toHaveBeenCalledWith("/api/v1/settings/gcp", expect.objectContaining({
      gcp_project_id: "my-project",
    }));
    expect(mockApiPost).toHaveBeenCalledWith("/api/v1/settings/gcp/validate");
  });

  it("step 5: renders Kubernetes + GCS (recommended) and SLURM + NFS (coming soon) cards", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToComputeStep();

    // Kubernetes card is present and labeled Recommended
    expect(screen.getByTestId("compute-stack-kubernetes")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes + GCS")).toBeInTheDocument();
    expect(screen.getByText("Recommended")).toBeInTheDocument();

    // SLURM card is present and labeled Coming Soon
    expect(screen.getByTestId("compute-stack-slurm")).toBeInTheDocument();
    expect(screen.getByText("SLURM + NFS")).toBeInTheDocument();
    expect(screen.getByText("Coming Soon")).toBeInTheDocument();
  });

  it("step 5: Kubernetes is selected by default and continues with K8s label", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToComputeStep();

    // Continue button shows Kubernetes label
    expect(screen.getByRole("button", { name: "Continue with Kubernetes + GCS" })).toBeInTheDocument();
  });

  it("step 5: clicking Kubernetes card keeps it selected", async () => {
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToComputeStep();

    fireEvent.click(screen.getByTestId("compute-stack-kubernetes"));
    expect(screen.getByRole("button", { name: "Continue with Kubernetes + GCS" })).toBeInTheDocument();
  });
});
