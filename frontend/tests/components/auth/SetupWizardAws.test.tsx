import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SetupWizard } from "@/components/auth/SetupWizard";
import { invalidateStackOptionsCache } from "@/hooks/useStackOptions";

// Mock fetch globally (the bootstrap steps use raw fetch).
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
  // Authenticated so useStackOptions fetches /stack-options and learns the
  // install is on AWS (the default-unauthenticated path stays GCP).
  isAuthenticated: jest.fn(() => true),
}));

function mockFetchResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

const AWS_STACK_OPTIONS = {
  cloud_provider: "aws",
  options: [
    {
      compute_stack: "kubernetes",
      storage_backend: "s3",
      label: "Kubernetes + S3",
      compute_label: "Kubernetes (EKS)",
      storage_label: "S3",
      available: true,
      recommended: true,
    },
    {
      compute_stack: "slurm",
      storage_backend: "nfs",
      label: "SLURM + NFS",
      compute_label: "SLURM",
      storage_label: "NFS",
      available: false,
      recommended: false,
    },
  ],
};

const AWS_SETTINGS = {
  aws_account_id: "043671579834",
  aws_region: "us-west-1",
  aws_app_role_arn: "arn:aws:iam::043671579834:role/bioaf-app",
  aws_bootstrap_role_arn: null,
  org_slug: null,
  aws_credential_source: "instance_profile",
};

beforeEach(() => {
  mockFetch.mockReset();
  mockApiPost.mockReset();
  mockApiPut.mockReset();
  mockApiGet.mockReset();
  mockApiPost.mockResolvedValue({});
  mockApiPut.mockResolvedValue({});
  mockApiGet.mockImplementation((url: string) => {
    if (typeof url === "string") {
      // The wizard reads the install's cloud from bootstrap/status (not the
      // authenticated stack-options endpoint, which is unavailable during setup).
      if (url.includes("/api/bootstrap/status")) {
        return Promise.resolve({ setup_complete: false, has_admin: false, cloud_provider: "aws" });
      }
      if (url.includes("/infrastructure/stack-options")) return Promise.resolve(AWS_STACK_OPTIONS);
      if (url.includes("/api/v1/settings/aws")) return Promise.resolve(AWS_SETTINGS);
    }
    return Promise.resolve({});
  });
  // The hook caches stack-options module-side; reset so each test re-reads "aws".
  invalidateStackOptionsCache();
  localStorage.clear();
});

/** Advance from step 0 (Setup Code) through to the AWS credentials step (step 3). */
async function advanceToAwsStep(user: ReturnType<typeof userEvent.setup>) {
  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { setup_token: "fake-jwt", message: "ok" })
  );
  await user.type(screen.getByPlaceholderText("Enter 6-character code"), "ABC123");
  await user.click(screen.getByRole("button", { name: /verify/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Create Admin Account" })).toBeInTheDocument());

  mockFetch.mockImplementationOnce(() =>
    mockFetchResponse(200, { access_token: "admin-jwt", token_type: "bearer", message: "ok" })
  );
  await user.type(screen.getByLabelText(/name/i), "Admin");
  await user.type(screen.getByLabelText(/email/i), "admin@test.com");
  await user.type(screen.getByLabelText(/^password$/i), "password123");
  await user.type(screen.getByLabelText(/confirm password/i), "password123");
  await user.click(screen.getByRole("button", { name: /create admin/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Organization Name" })).toBeInTheDocument());

  await user.type(screen.getByLabelText(/organization name/i), "Acme Bio");
  await user.click(screen.getByRole("button", { name: /save organization/i }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "AWS Credentials" })).toBeInTheDocument());
}

describe("SetupWizard (AWS install)", () => {
  it("renders the AWS credentials step (not GCP) when cloud_provider is aws", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToAwsStep(user);

    expect(screen.getByRole("heading", { name: "AWS Credentials" })).toBeInTheDocument();
    expect(screen.getByLabelText("AWS Account ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Region")).toBeInTheDocument();
    // The GCP-specific fields must NOT appear on an AWS install.
    expect(screen.queryByLabelText("GCP Project ID")).not.toBeInTheDocument();
  });

  it("prefills account and region from /api/v1/settings/aws", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToAwsStep(user);

    await waitFor(() => expect(screen.getByLabelText("AWS Account ID")).toHaveValue("043671579834"));
    expect(screen.getByLabelText("Region")).toHaveValue("us-west-1");
  });

  it("Save & Validate saves AWS config then validates and advances to SMTP", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToAwsStep(user);

    mockApiPut.mockResolvedValueOnce({});
    mockApiPost.mockResolvedValueOnce({ passed: true, checks: [], account_id: "043671579834" });

    await user.type(screen.getByLabelText(/organization slug/i), "my-bioaf-org");
    await user.click(screen.getByRole("button", { name: "Save & Validate" }));

    await screen.findByRole("heading", { name: "SMTP Settings" });

    expect(mockApiPut).toHaveBeenCalledWith(
      "/api/v1/settings/aws",
      expect.objectContaining({ org_slug: "my-bioaf-org", aws_region: "us-west-1" })
    );
    expect(mockApiPost).toHaveBeenCalledWith("/api/v1/settings/aws/validate");
  });

  it("blocks 'Set up infrastructure' until AWS validation passes", async () => {
    const user = userEvent.setup();
    render(<SetupWizard onComplete={jest.fn()} />);
    await advanceToAwsStep(user);

    // Skip AWS creds -> SMTP -> Infrastructure decision (step 5).
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "SMTP Settings" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /do this later/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Infrastructure" })).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /set up infrastructure/i })).toBeDisabled();
    expect(screen.getByText(/AWS credentials are required/i)).toBeInTheDocument();
  });
});
