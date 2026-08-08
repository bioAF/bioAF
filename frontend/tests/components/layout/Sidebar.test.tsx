import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "@/components/layout/Sidebar";

// Mock next/navigation
const mockPathname = jest.fn().mockReturnValue("/dashboard");
jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname(),
}));

// Mock auth
const mockGetCurrentUser = jest.fn();
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => mockGetCurrentUser(),
}));

// Mock usePermissions
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

// Beta features default-deny in these tests (the Sidebar now consults useBetaFeatures for the
// Validation Studies / Beta Features entries; without this it would hit the real hook -> auth/network).
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => ({ available: false, flags: {}, loading: false }),
}));

const mockHasCapability = jest.fn().mockReturnValue(true);
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    has: (flag: string) => mockHasCapability(flag),
    capabilities: {},
    loading: false,
  }),
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

// These tests are about WHICH item is marked active, not what the marker looks
// like. They used to pin `bg-bioaf-700` and `bg-gray-800` inline, which made them
// a second source of truth for the palette: the 2026-08-08 move of the sidebar
// onto the shell surface broke three of them without any behaviour changing.
// The colours themselves are owned by src/__tests__/app-shell-surface.test.ts,
// which checks them for contrast in both themes.
const ACTIVE_MARKER = "bg-bioaf-50";
const EXPANDED_SECTION_MARKER = "bg-gray-100";

describe("Sidebar", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/dashboard");
    mockGetCurrentUser.mockReturnValue({
      email: "test@bioaf.org",
      role_name: "admin",
      sub: "1",
    });
    mockCanAccess.mockReturnValue(true);
    mockRoleName.mockReturnValue("admin");
    mockHasCapability.mockReset();
    mockHasCapability.mockReturnValue(true);
  });

  it("renders all 8 top-level items for admin user", () => {
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    // 8 top-level sections: Dashboard, Experiments, Pipelines, Results,
    // Workbench, Data & Files, Infrastructure, Settings. Profile is reached by
    // clicking the username in the header, not from the sidebar.
    expect(nav).toHaveTextContent("Dashboard");
    expect(nav).toHaveTextContent("Experiments");
    expect(nav).toHaveTextContent("Pipelines");
    expect(nav).toHaveTextContent("Results");
    expect(nav).toHaveTextContent("Workbench");
    expect(nav).toHaveTextContent("Data & Files");
    expect(nav).toHaveTextContent("Infrastructure");
    expect(nav).toHaveTextContent("Settings");
    expect(nav).not.toHaveTextContent("Profile");
  });

  it("renders Experiments as a top-level nav item (renamed from Projects)", () => {
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    const buttons = Array.from(nav.querySelectorAll("button"));
    // The section is now surfaced as a top-level "Experiments" button, and the
    // old "Projects" top-level label is gone.
    expect(buttons.find((b) => b.textContent?.trim() === "Experiments")).toBeDefined();
    expect(buttons.find((b) => b.textContent?.trim() === "Projects")).toBeUndefined();
  });

  it("hides Settings when user role is not admin", () => {
    mockGetCurrentUser.mockReturnValue({
      email: "bench@bioaf.org",
      role_name: "bench",
      sub: "2",
    });
    mockRoleName.mockReturnValue("bench");
    // Bench users: only experiments, samples, pipelines(view), notebooks(view), environments(view), files(view/upload), projects(view)
    mockCanAccess.mockImplementation((resource: string, action: string) => {
      const benchPerms: Record<string, string[]> = {
        experiments: ["view", "create", "edit", "upload"],
        samples: ["view", "create", "edit"],
        pipelines: ["view"],
        notebooks: ["view"],
        environments: ["view"],
        files: ["view", "upload"],
        projects: ["view"],
      };
      return benchPerms[resource]?.includes(action) ?? false;
    });
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    expect(nav).not.toHaveTextContent("Settings");
  });

  it("shows Settings when user role is admin", () => {
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    expect(nav).toHaveTextContent("Settings");
  });

  it("renders Experiments as an expandable section with its children", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Experiments"));
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("Experiment Templates")).toBeInTheDocument();
    expect(screen.getByText("Experiment List")).toBeInTheDocument();
  });

  it("Projects child navigates to /projects", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Experiments"));
    const projectListLink = screen.getByText("Projects").closest("a");
    expect(projectListLink).toHaveAttribute("href", "/projects");
  });

  it("Experiment Templates child navigates to /projects/experiment-templates", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Experiments"));
    const templatesLink = screen.getByText("Experiment Templates").closest("a");
    expect(templatesLink).toHaveAttribute("href", "/projects/experiment-templates");
  });

  it("Experiment List child navigates to /projects/experiments", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Experiments"));
    const listLink = screen.getByText("Experiment List").closest("a");
    expect(listLink).toHaveAttribute("href", "/projects/experiments");
  });

  it("highlights Experiments section and auto-expands when on an experiment page", () => {
    mockPathname.mockReturnValue("/projects/experiments");
    render(<Sidebar />);
    const experimentsButton = screen.getByText("Experiments").closest("button");
    expect(experimentsButton?.className).toContain(EXPANDED_SECTION_MARKER);
    expect(screen.getByText("Experiment List")).toBeInTheDocument();
  });

  it("navigates to correct path for single-page items", () => {
    render(<Sidebar />);
    const dashboardLink = screen.getByText("Dashboard").closest("a");
    expect(dashboardLink).toHaveAttribute("href", "/dashboard");
  });

  it("toggles children visibility when clicking expandable section", () => {
    render(<Sidebar />);
    // Results section should not show children initially (not active)
    expect(screen.queryByText("QC Dashboards")).not.toBeInTheDocument();

    // Click Results to expand
    fireEvent.click(screen.getByText("Results"));
    expect(screen.getByText("QC Dashboards")).toBeInTheDocument();
    expect(screen.getByText("cellxgene Explorer")).toBeInTheDocument();
    expect(screen.getByText("Plot Archive")).toBeInTheDocument();

    // Click again to collapse
    fireEvent.click(screen.getByText("Results"));
    expect(screen.queryByText("QC Dashboards")).not.toBeInTheDocument();
  });

  it("highlights active path for top-level items", () => {
    mockPathname.mockReturnValue("/dashboard");
    render(<Sidebar />);
    const dashboardLink = screen.getByText("Dashboard").closest("a");
    expect(dashboardLink?.className).toContain(ACTIVE_MARKER);
  });

  it("highlights active path and auto-expands parent for child items", () => {
    mockPathname.mockReturnValue("/pipelines/runs");
    render(<Sidebar />);
    // Parent section should be auto-expanded
    expect(screen.getByText("Pipeline Runs")).toBeInTheDocument();
    // Child item should be highlighted
    const runsLink = screen.getByText("Pipeline Runs").closest("a");
    expect(runsLink?.className).toContain(ACTIVE_MARKER);
  });

  it("shows child items when parent section is expanded", () => {
    render(<Sidebar />);
    // Expand Infrastructure
    fireEvent.click(screen.getByText("Infrastructure"));
    expect(screen.getByText("Components")).toBeInTheDocument();
    expect(screen.getByText("Cost Center")).toBeInTheDocument();
    expect(screen.getByText("Backup & Recovery")).toBeInTheDocument();
  });

  it("shows Workbench children: Notebook Sessions, Work Nodes, Workbench Images", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Workbench"));
    expect(screen.getByText("Notebook Sessions")).toBeInTheDocument();
    expect(screen.getByText("Work Nodes")).toBeInTheDocument();
    expect(screen.getByText("Workbench Images")).toBeInTheDocument();
  });

  it("shows Results to a role with only pipelines:view (View Results via OR)", () => {
    mockRoleName.mockReturnValue("custom");
    mockCanAccess.mockImplementation(
      (resource: string, action: string) => resource === "pipelines" && action === "view",
    );
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    expect(nav).toHaveTextContent("Results");
    fireEvent.click(screen.getByText("Results"));
    expect(screen.getByText("QC Dashboards")).toBeInTheDocument();
  });

  it("hides the Cellxgene entry when the backend lacks the cellxgene capability", () => {
    mockHasCapability.mockImplementation((flag: string) => flag !== "cellxgene");
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Results"));
    expect(screen.getByText("QC Dashboards")).toBeInTheDocument();
    expect(screen.queryByText("cellxgene Explorer")).not.toBeInTheDocument();
  });

  it("hides Work Nodes entries when the backend lacks the work_nodes capability", () => {
    mockHasCapability.mockImplementation((flag: string) => flag !== "work_nodes");
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Workbench"));
    expect(screen.getByText("Notebook Sessions")).toBeInTheDocument();
    expect(screen.queryByText("Work Nodes")).not.toBeInTheDocument();
  });

  it("hides Results from a role with neither experiments nor pipelines view", () => {
    mockRoleName.mockReturnValue("custom");
    mockCanAccess.mockImplementation(
      (resource: string, action: string) => resource === "samples" && action === "view",
    );
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    expect(nav).not.toHaveTextContent("Results");
  });
});
