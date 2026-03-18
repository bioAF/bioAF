import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SetupWizard } from "./SetupWizard";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  setToken: jest.fn(),
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role: "admin", email: "admin@test.com" }),
}));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;
const mockPut = api.put as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
  mockPut.mockReset();
  mockPost.mockResolvedValue({ access_token: "tok", email_sent: true });
  mockPut.mockResolvedValue({});
});

const onComplete = jest.fn();

/** Advance the wizard to the GCP Configuration step (step 4). */
async function advanceToGcpStep() {
  render(<SetupWizard onComplete={onComplete} />);

  // Step 0: Create Admin
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "a@b.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "pass1234" },
  });
  fireEvent.change(screen.getByLabelText("Confirm Password"), {
    target: { value: "pass1234" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create Admin Account" }));
  await screen.findByRole("heading", { name: "Verify Email" });

  // Step 1: Skip verification
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await screen.findByRole("heading", { name: "Organization Name" });

  // Step 2: Org name
  mockPost.mockResolvedValueOnce({ message: "ok" });
  fireEvent.change(screen.getByLabelText("Organization Name"), {
    target: { value: "Test Org" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save Organization Name" }));
  await screen.findByRole("heading", { name: "SMTP Configuration" });

  // Step 3: Skip SMTP
  fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
  await screen.findByRole("heading", { name: "GCP Configuration" });
}

describe("SetupWizard GCP step ordering", () => {
  test("has 8 steps total", () => {
    render(<SetupWizard onComplete={onComplete} />);
    expect(screen.getByText("8")).toBeInTheDocument();
  });

  test("GCP Configuration step appears after SMTP and before Compute Stack", async () => {
    await advanceToGcpStep();
    expect(screen.getByRole("heading", { name: "GCP Configuration" })).toBeInTheDocument();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
  });

  test("skipping GCP step advances to Compute Stack", async () => {
    await advanceToGcpStep();
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    await screen.findByRole("heading", { name: "Compute Stack" });
    expect(screen.getByText(/Choose the compute infrastructure/)).toBeInTheDocument();
  });
});

describe("GCP Configuration step fields", () => {
  test("shows project ID, region, zone, org slug, and auth fields", async () => {
    await advanceToGcpStep();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    expect(screen.getByLabelText("Zone")).toBeInTheDocument();
    expect(screen.getByText("Organization Slug")).toBeInTheDocument();
    expect(screen.getByText("VM default credentials")).toBeInTheDocument();
    expect(screen.getByText("Service account key (JSON)")).toBeInTheDocument();
  });

  test("Save & Validate calls PUT then POST validate, advances to Compute Stack", async () => {
    await advanceToGcpStep();

    mockPut.mockResolvedValueOnce({});
    mockPost.mockResolvedValueOnce({ passed: true, checks: [] });

    fireEvent.change(screen.getByLabelText("GCP Project ID"), {
      target: { value: "my-project" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save & Validate" }));

    await screen.findByRole("heading", { name: "Compute Stack" });

    expect(mockPut).toHaveBeenCalledWith(
      "/api/v1/settings/gcp",
      expect.objectContaining({ gcp_project_id: "my-project" }),
    );
    expect(mockPost).toHaveBeenCalledWith("/api/v1/settings/gcp/validate");
  });

  test("shows error when GCP save fails", async () => {
    await advanceToGcpStep();

    mockPut.mockRejectedValueOnce(new Error("Invalid project ID"));

    fireEvent.click(screen.getByRole("button", { name: "Save & Validate" }));

    await waitFor(() => {
      expect(screen.getByText("Invalid project ID")).toBeInTheDocument();
    });
    // Should still be on GCP step
    expect(screen.getByRole("heading", { name: "GCP Configuration" })).toBeInTheDocument();
  });
});

describe("Compute Stack step triggers background deploy", () => {
  async function advanceToComputeStep() {
    await advanceToGcpStep();
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    await screen.findByRole("heading", { name: "Compute Stack" });
  }

  test("clicking Continue fires background deploy and advances to Invite Team", async () => {
    await advanceToComputeStep();

    // Mock both the configure-compute-stack and deploy-background calls
    mockPost.mockResolvedValueOnce({}); // configure-compute-stack
    mockPost.mockResolvedValueOnce({ message: "Deployment started" }); // deploy-background

    fireEvent.click(screen.getByRole("button", { name: "Continue with Kubernetes + GCS" }));

    await screen.findByRole("heading", { name: "Invite Team" });

    expect(mockPost).toHaveBeenCalledWith(
      "/api/v1/infrastructure/stack/deploy-background",
      expect.objectContaining({ stack_type: "kubernetes" }),
    );
  });

  test("advances even if background deploy fails", async () => {
    await advanceToComputeStep();

    mockPost.mockRejectedValueOnce(new Error("not found")); // configure-compute-stack
    mockPost.mockRejectedValueOnce(new Error("preconditions")); // deploy-background

    fireEvent.click(screen.getByRole("button", { name: "Continue with Kubernetes + GCS" }));

    await screen.findByRole("heading", { name: "Invite Team" });
  });
});
