/**
 * Tests that "Integrations" appears in the Settings nav for admin users
 * (replacing the separate GCP Configuration, SMTP, and Slack entries)
 * and does not appear for non-admin users.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "@/components/layout/Sidebar";

const mockPathname = jest.fn().mockReturnValue("/settings/integrations");
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));

const mockGetCurrentUser = jest.fn();
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => mockGetCurrentUser(),
}));

const mockCanAccess = jest.fn().mockReturnValue(true);
const mockRoleName = jest.fn().mockReturnValue("admin");
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({
    components: [
      { key: "nextflow", category: "pipeline_orchestration", enabled: true },
      { key: "jupyterhub", category: "analysis", enabled: true },
      { key: "rstudio", category: "analysis", enabled: true },
      { key: "qc_dashboard", category: "visualization", enabled: true },
      { key: "cellxgene", category: "visualization", enabled: true },
    ],
    loading: false,
    refetch: jest.fn(),
  }),
}));

jest.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => ({ ready: true }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (...args: unknown[]) => mockCanAccess(...args),
    roleName: mockRoleName(),
    loading: false,
    permissions: new Set(),
  }),
  clearPermissionsCache: jest.fn(),
}));

describe("Integrations nav item", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/dashboard");
    mockCanAccess.mockReturnValue(true);
    mockRoleName.mockReturnValue("admin");
  });

  it("appears in Settings section for admin users", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role_name: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByText("Integrations")).toBeInTheDocument();
  });

  it("links to /settings/integrations", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role_name: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));
    const link = screen.getByText("Integrations").closest("a");
    expect(link).toHaveAttribute("href", "/settings/integrations");
  });

  it("does not appear for non-admin users (Settings section hidden)", () => {
    mockGetCurrentUser.mockReturnValue({ email: "bench@bioaf.org", role_name: "bench", sub: "2" });
    mockRoleName.mockReturnValue("bench");
    mockCanAccess.mockReturnValue(false);
    render(<Sidebar />);
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument();
  });

  it("replaces separate GCP, SMTP, and Slack nav entries", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role_name: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));
    expect(screen.queryByText("GCP Configuration")).not.toBeInTheDocument();
    expect(screen.queryByText("SMTP Configuration")).not.toBeInTheDocument();
    expect(screen.queryByText("Slack Integration")).not.toBeInTheDocument();
  });

  it("is positioned after Audit Log in Settings", () => {
    mockGetCurrentUser.mockReturnValue({ email: "admin@bioaf.org", role_name: "admin", sub: "1" });
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Settings"));

    const items = screen.getAllByRole("link").map((el) => el.textContent);
    const auditLogIdx = items.findIndex((t) => t === "Audit Log");
    const integrationsIdx = items.findIndex((t) => t === "Integrations");

    expect(auditLogIdx).toBeGreaterThanOrEqual(0);
    expect(integrationsIdx).toBeGreaterThan(auditLogIdx);
  });
});
