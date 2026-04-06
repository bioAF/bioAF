import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupWizard } from "./SetupWizard";

// Mock fetch globally (used by setup code verification and admin creation)
const mockFetch = jest.fn();
global.fetch = mockFetch;

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn().mockResolvedValue({}),
    put: jest.fn().mockResolvedValue({}),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  setToken: jest.fn(),
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
}));

import { api } from "@/lib/api";

const mockPost = api.post as jest.Mock;
const mockPut = api.put as jest.Mock;

function mockFetchResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  mockFetch.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  mockPost.mockResolvedValue({});
  mockPut.mockResolvedValue({});
  localStorage.clear();
});

const onComplete = jest.fn();

/** Advance the wizard to the GCP Credentials step (step 3). */
async function advanceToGcpStep(user: ReturnType<typeof userEvent.setup>) {
  // Step 0: verify setup code
  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { setup_token: "fake-jwt", message: "ok" })
  );
  await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
  await user.click(screen.getByRole("button", { name: /verify/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());

  // Step 1: create admin
  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { access_token: "admin-jwt", token_type: "bearer", message: "ok" })
  );
  await user.type(screen.getByLabelText(/name/i), "Admin");
  await user.type(screen.getByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/^password$/i), "password123");
  await user.type(screen.getByLabelText(/confirm password/i), "password123");
  await user.click(screen.getByRole("button", { name: /create admin/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument());

  // Step 2: org name
  mockPost.mockResolvedValueOnce({ message: "ok" });
  await user.type(screen.getByLabelText(/organization name/i), "Test Org");
  await user.click(screen.getByRole("button", { name: /save organization/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument());
}

describe("SetupWizard step ordering", () => {
  test("has 9 steps total", () => {
    render(<SetupWizard onComplete={onComplete} />);
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  test("GCP Credentials step appears after Organization Name", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);
    expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument();
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
  });

  test("skipping GCP advances to SMTP Settings", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await screen.findByRole("heading", { name: "SMTP Settings" });
  });
});

describe("GCP Credentials step fields", () => {
  test("shows project ID, region, zone, org slug, and auth fields", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);
    expect(screen.getByLabelText("GCP Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    expect(screen.getByLabelText("Zone")).toBeInTheDocument();
    expect(screen.getByText("VM default credentials")).toBeInTheDocument();
    expect(screen.getByText("Service account key (JSON)")).toBeInTheDocument();
  });

  test("shows prerequisites section with IAM roles and APIs", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);
    const prereqs = screen.getByTestId("gcp-prerequisites");
    expect(prereqs).toBeInTheDocument();
    expect(prereqs.textContent).toContain("roles/artifactregistry.admin");
    expect(prereqs.textContent).toContain("cloudbuild.googleapis.com");
  });

  test("Save & Validate calls PUT then POST validate, advances to SMTP", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);

    mockPut.mockResolvedValueOnce({});
    mockPost.mockResolvedValueOnce({ passed: true, checks: [] });

    await user.type(screen.getByLabelText("GCP Project ID"), "my-project");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));

    await screen.findByRole("heading", { name: "SMTP Settings" });

    expect(mockPut).toHaveBeenCalledWith(
      "/api/v1/settings/gcp",
      expect.objectContaining({ gcp_project_id: "my-project" }),
    );
    expect(mockPost).toHaveBeenCalledWith("/api/v1/settings/gcp/validate");
  });

  test("shows error when GCP save fails", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={onComplete} />);
    await advanceToGcpStep(user);

    mockPut.mockRejectedValueOnce(new Error("Invalid project ID"));

    await user.click(screen.getByRole("button", { name: "Save & Validate" }));

    await waitFor(() => {
      expect(screen.getByText("Invalid project ID")).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: "GCP Credentials" })).toBeInTheDocument();
  });
});
