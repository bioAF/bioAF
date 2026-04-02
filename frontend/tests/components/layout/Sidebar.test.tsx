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

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (...args: unknown[]) => mockCanAccess(...args),
    roleName: mockRoleName(),
    loading: false,
    permissions: new Set(),
  }),
  clearPermissionsCache: jest.fn(),
}));

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
  });

  it("renders all 9 top-level items for admin user", () => {
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    // 9 top-level sections: Dashboard, Results, Pipelines, Projects,
    // Workbench, Data & Files, Infrastructure, Profile, Settings
    expect(nav).toHaveTextContent("Dashboard");
    expect(nav).toHaveTextContent("Results");
    expect(nav).toHaveTextContent("Pipelines");
    expect(nav).toHaveTextContent("Projects");
    expect(nav).toHaveTextContent("Workbench");
    expect(nav).toHaveTextContent("Data & Files");
    expect(nav).toHaveTextContent("Infrastructure");
    expect(nav).toHaveTextContent("Profile");
    expect(nav).toHaveTextContent("Settings");
  });

  it("does not render Experiments as a top-level nav item", () => {
    render(<Sidebar />);
    const nav = screen.getByTestId("sidebar-nav");
    // Experiments should only appear as a child of Projects, not as a top-level button
    const buttons = Array.from(nav.querySelectorAll("button"));
    const experimentButton = buttons.find((b) => b.textContent?.trim() === "Experiments");
    expect(experimentButton).toBeUndefined();
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

  it("renders Projects as an expandable section with 3 children", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Projects"));
    expect(screen.getByText("Project List")).toBeInTheDocument();
    expect(screen.getByText("Experiment Templates")).toBeInTheDocument();
    expect(screen.getByText("Experiment List")).toBeInTheDocument();
  });

  it("Project List child navigates to /projects", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Projects"));
    const projectListLink = screen.getByText("Project List").closest("a");
    expect(projectListLink).toHaveAttribute("href", "/projects");
  });

  it("Experiment Templates child navigates to /projects/experiment-templates", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Projects"));
    const templatesLink = screen.getByText("Experiment Templates").closest("a");
    expect(templatesLink).toHaveAttribute("href", "/projects/experiment-templates");
  });

  it("Experiment List child navigates to /projects/experiments", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Projects"));
    const listLink = screen.getByText("Experiment List").closest("a");
    expect(listLink).toHaveAttribute("href", "/projects/experiments");
  });

  it("highlights Projects section and auto-expands when on an experiment page", () => {
    mockPathname.mockReturnValue("/projects/experiments");
    render(<Sidebar />);
    const projectsButton = screen.getByText("Projects").closest("button");
    expect(projectsButton?.className).toContain("bg-gray-800");
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
    expect(screen.getByText("Cellxgene")).toBeInTheDocument();
    expect(screen.getByText("Plot Archive")).toBeInTheDocument();

    // Click again to collapse
    fireEvent.click(screen.getByText("Results"));
    expect(screen.queryByText("QC Dashboards")).not.toBeInTheDocument();
  });

  it("highlights active path for top-level items", () => {
    mockPathname.mockReturnValue("/dashboard");
    render(<Sidebar />);
    const dashboardLink = screen.getByText("Dashboard").closest("a");
    expect(dashboardLink?.className).toContain("bg-bioaf-700");
  });

  it("highlights active path and auto-expands parent for child items", () => {
    mockPathname.mockReturnValue("/pipelines/runs");
    render(<Sidebar />);
    // Parent section should be auto-expanded
    expect(screen.getByText("Pipeline Runs")).toBeInTheDocument();
    // Child item should be highlighted
    const runsLink = screen.getByText("Pipeline Runs").closest("a");
    expect(runsLink?.className).toContain("bg-bioaf-700");
  });

  it("shows child items when parent section is expanded", () => {
    render(<Sidebar />);
    // Expand Infrastructure
    fireEvent.click(screen.getByText("Infrastructure"));
    expect(screen.getByText("Components")).toBeInTheDocument();
    expect(screen.getByText("Compute")).toBeInTheDocument();
    expect(screen.getByText("Cost Center")).toBeInTheDocument();
    expect(screen.getByText("Backup & Recovery")).toBeInTheDocument();
  });

  it("shows Workbench children: Notebooks, Work Nodes, Environments", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Workbench"));
    expect(screen.getByText("Notebooks")).toBeInTheDocument();
    expect(screen.getByText("Work Nodes")).toBeInTheDocument();
    expect(screen.getByText("Environments")).toBeInTheDocument();
  });
});
