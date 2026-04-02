import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import GcpSettingsPage from "@/app/settings/gcp/page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  usePathname: () => "/settings/gcp",
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "admin@bioaf.org", role: "admin", sub: "1" }),
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockApiPost = jest.fn();

jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    put: (...args: unknown[]) => mockApiPut(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

const defaultConfig = {
  gcp_project_id: "my-project-123",
  gcp_region: "us-central1",
  gcp_zone: "us-central1-a",
  org_slug: "my-bioaf-org",
  gcp_credentials_configured: false,
  gcp_validation_status: null,
  gcp_credential_source: "vm_default",
};

const fullValidationResult = {
  passed: true,
  checks: [
    { name: "credentials_loaded", passed: true, message: "OK", status: "ok" },
    { name: "iam_permissions", passed: true, message: "All granted", status: "ok" },
  ],
  recommended_roles: [
    "roles/storage.admin",
    "roles/bigquery.dataEditor",
    "roles/container.admin",
  ],
  permission_details: [
    { permission: "storage.buckets.create", granted: true, recommended_role: "roles/storage.admin" },
    { permission: "bigquery.datasets.create", granted: false, recommended_role: "roles/bigquery.dataEditor" },
  ],
};

describe("GCP Settings Page", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPut.mockReset();
    mockApiPost.mockReset();
    mockApiGet.mockResolvedValue(defaultConfig);
    mockApiPut.mockResolvedValue(defaultConfig);
  });

  // Test 18: Page renders with heading
  it("renders GCP Configuration heading", async () => {
    render(<GcpSettingsPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "GCP Configuration" })).toBeInTheDocument();
    });
  });

  // Test 19: Page loads existing config from API
  it("loads existing config values from the API", async () => {
    render(<GcpSettingsPage />);
    await waitFor(() => {
      const projectInput = screen.getByTestId("gcp-project-id-input");
      expect((projectInput as HTMLInputElement).value).toBe("my-project-123");
    });
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/settings/gcp"),
    );
  });

  // Test 20: Org slug validation shows error for invalid input
  it("shows validation error for invalid org slug", async () => {
    render(<GcpSettingsPage />);
    await waitFor(() => screen.getByTestId("org-slug-input"));

    const slugInput = screen.getByTestId("org-slug-input");
    fireEvent.change(slugInput, { target: { value: "-bad-slug-" } });

    const saveBtn = screen.getByTestId("save-gcp-config-btn");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByTestId("org-slug-error")).toBeInTheDocument();
    });
  });

  // Test 21: Save button calls PUT then auto-validates
  it("save button calls PUT then auto-validates", async () => {
    mockApiPost.mockResolvedValue(fullValidationResult);

    render(<GcpSettingsPage />);
    await waitFor(() => screen.getByTestId("save-gcp-config-btn"));

    fireEvent.click(screen.getByTestId("save-gcp-config-btn"));

    await waitFor(() => {
      expect(mockApiPut).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/gcp"),
        expect.any(Object),
      );
    });

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/gcp/validate"),
      );
    });
  });

  // Test 22: Validate button calls POST validate API
  it("validate button calls POST /api/v1/settings/gcp/validate", async () => {
    mockApiPost.mockResolvedValue(fullValidationResult);

    render(<GcpSettingsPage />);
    await waitFor(() => screen.getByTestId("validate-gcp-btn"));

    fireEvent.click(screen.getByTestId("validate-gcp-btn"));

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/settings/gcp/validate"),
      );
    });
  });

  // Test 23: Validation results display after validate
  it("displays validation results after validate is clicked", async () => {
    mockApiPost.mockResolvedValue(fullValidationResult);

    render(<GcpSettingsPage />);
    await waitFor(() => screen.getByTestId("validate-gcp-btn"));

    fireEvent.click(screen.getByTestId("validate-gcp-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-results")).toBeInTheDocument();
    });
  });

  // Test 24: Recommended roles are shown
  it("shows recommended roles section", async () => {
    render(<GcpSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("recommended-roles")).toBeInTheDocument();
    });
    const rolesSection = screen.getByTestId("recommended-roles");
    expect(rolesSection.textContent).toContain("roles/bigquery.dataEditor");
    expect(rolesSection.textContent).toContain("roles/storage.admin");
    expect(rolesSection.textContent).toContain("roles/viewer");
    expect(rolesSection.textContent).toContain("roles/artifactregistry.admin");
    expect(rolesSection.textContent).toContain("roles/cloudbuild.builds.editor");
  });

  // Test 25: Per-permission results are shown after validation
  it("shows per-permission results after validation", async () => {
    mockApiPost.mockResolvedValue(fullValidationResult);

    render(<GcpSettingsPage />);
    await waitFor(() => screen.getByTestId("validate-gcp-btn"));

    fireEvent.click(screen.getByTestId("validate-gcp-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("permission-details")).toBeInTheDocument();
    });
    const details = screen.getByTestId("permission-details");
    expect(details.textContent).toContain("storage.buckets.create");
    expect(details.textContent).toContain("bigquery.datasets.create");
  });
});
