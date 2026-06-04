import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "./Sidebar";

jest.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
}));

jest.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => ({ ready: true }),
}));

const mockPermissions = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => mockPermissions(),
}));

const mockComponents = jest.fn();
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => mockComponents(),
}));

beforeEach(() => {
  mockComponents.mockReset();
  mockPermissions.mockReset();
  mockPermissions.mockReturnValue({
    canAccess: () => true,
    roleName: "admin",
    loading: false,
  });
});

function makeComponent(key: string, category: string, enabled: boolean) {
  return { key, name: key, description: "", category, enabled, status: enabled ? "ready" : "disabled", config: {}, dependencies: [], estimated_monthly_cost: "", updated_at: null };
}

describe("Sidebar component gating", () => {
  test("hides Pipelines section when no pipeline_orchestration component is enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", false),
        makeComponent("snakemake_k8s", "pipeline_orchestration", false),
        makeComponent("jupyterhub", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
  });

  test("shows Pipelines section when a pipeline_orchestration component is enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", true),
        makeComponent("jupyterhub", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    expect(screen.getByText("Pipelines")).toBeInTheDocument();
  });

  test("hides Notebooks child when neither jupyterhub nor rstudio is enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("jupyterhub", "analysis", false),
        makeComponent("rstudio", "analysis", false),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    // Expand Workbench to check children
    fireEvent.click(screen.getByText("Workbench"));

    expect(screen.queryByText("Notebooks")).not.toBeInTheDocument();
  });

  test("shows Notebooks child when jupyterhub is enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("jupyterhub", "analysis", true),
        makeComponent("rstudio", "analysis", false),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    fireEvent.click(screen.getByText("Workbench"));

    expect(screen.getByText("Notebooks")).toBeInTheDocument();
  });

  test("shows Notebooks child when rstudio is enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("jupyterhub", "analysis", false),
        makeComponent("rstudio", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    fireEvent.click(screen.getByText("Workbench"));

    expect(screen.getByText("Notebooks")).toBeInTheDocument();
  });

  test("hides QC Dashboards when qc_dashboard component is not enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("qc_dashboard", "visualization", false),
        makeComponent("cellxgene", "visualization", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    fireEvent.click(screen.getByText("Results"));

    expect(screen.queryByText("QC Dashboards")).not.toBeInTheDocument();
    expect(screen.getByText("Cellxgene")).toBeInTheDocument();
  });

  test("hides Cellxgene when cellxgene component is not enabled", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("qc_dashboard", "visualization", true),
        makeComponent("cellxgene", "visualization", false),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    fireEvent.click(screen.getByText("Results"));

    expect(screen.getByText("QC Dashboards")).toBeInTheDocument();
    expect(screen.queryByText("Cellxgene")).not.toBeInTheDocument();
  });

  test("shows loading screen when components are still loading", () => {
    mockComponents.mockReturnValue({
      components: [],
      loading: true,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    expect(screen.getByText("Loading bioAF...")).toBeInTheDocument();
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
  });

  test("shows loading screen when permissions are still loading", () => {
    mockPermissions.mockReturnValue({
      canAccess: () => true,
      roleName: "",
      loading: true,
    });
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    expect(screen.getByText("Loading bioAF...")).toBeInTheDocument();
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
  });

  test("shows nav after loading completes", () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow", "pipeline_orchestration", true),
        makeComponent("jupyterhub", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });

    render(<Sidebar />);

    expect(screen.queryByText("Loading bioAF...")).not.toBeInTheDocument();
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
  });
});

describe("Sidebar single-expanded behavior", () => {
  beforeEach(() => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", true),
        makeComponent("jupyterhub", "analysis", true),
        makeComponent("qc_dashboard", "visualization", true),
        makeComponent("cellxgene", "visualization", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });
  });

  test("expanding a second section collapses the first", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Pipelines"));
    expect(screen.getByTestId("children-Pipelines")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Workbench"));

    expect(screen.getByTestId("children-Workbench")).toBeInTheDocument();
    expect(screen.queryByTestId("children-Pipelines")).not.toBeInTheDocument();
  });

  test("clicking an expanded section collapses it leaving none expanded", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Pipelines"));
    expect(screen.getByTestId("children-Pipelines")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Pipelines"));

    expect(screen.queryByTestId("children-Pipelines")).not.toBeInTheDocument();
  });

  test("only one section is expanded at any time across many toggles", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByText("Pipelines"));
    fireEvent.click(screen.getByText("Workbench"));
    fireEvent.click(screen.getByText("Results"));

    expect(screen.getByTestId("children-Results")).toBeInTheDocument();
    expect(screen.queryByTestId("children-Pipelines")).not.toBeInTheDocument();
    expect(screen.queryByTestId("children-Workbench")).not.toBeInTheDocument();
  });
});

describe("Sidebar collapse toggle", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", true),
        makeComponent("jupyterhub", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });
  });

  test("renders a collapse toggle button and starts expanded", () => {
    render(<Sidebar />);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "false");
    expect(screen.getByTestId("sidebar-collapse-toggle")).toBeInTheDocument();
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
  });

  test("clicking the toggle collapses the sidebar and hides section labels", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "true");
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
    expect(screen.queryByText("Workbench")).not.toBeInTheDocument();
  });

  test("clicking the toggle again re-expands the sidebar", () => {
    render(<Sidebar />);

    const toggle = screen.getByTestId("sidebar-collapse-toggle");
    fireEvent.click(toggle);
    fireEvent.click(toggle);

    expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
  });

  test("toggle button is reachable even when collapsed", () => {
    render(<Sidebar />);

    const toggle = screen.getByTestId("sidebar-collapse-toggle");
    fireEvent.click(toggle);

    // Still present after collapse, so the user can re-expand
    expect(screen.getByTestId("sidebar-collapse-toggle")).toBeInTheDocument();
  });
});

describe("Sidebar collapse persistence", () => {
  const STORAGE_KEY = "bioaf-sidebar-collapsed";

  beforeEach(() => {
    window.localStorage.clear();
    mockComponents.mockReturnValue({
      components: [
        makeComponent("nextflow_k8s", "pipeline_orchestration", true),
        makeComponent("jupyterhub", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });
  });

  test("starts collapsed when localStorage says so", () => {
    window.localStorage.setItem(STORAGE_KEY, "true");

    render(<Sidebar />);

    expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "true");
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
  });

  test("starts expanded when localStorage is empty", () => {
    render(<Sidebar />);

    expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
  });

  test("starts expanded when localStorage value is not 'true'", () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");

    render(<Sidebar />);

    expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
  });

  test("writes the new state to localStorage when toggled", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("true");

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("false");
  });
});
