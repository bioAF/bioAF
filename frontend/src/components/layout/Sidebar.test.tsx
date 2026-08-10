import { render, screen, fireEvent } from "@testing-library/react";
import { Sidebar } from "./Sidebar";

jest.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

jest.mock("next/link", () => {
  // Forwards the rest of the props (onClick above all): the real next/link
  // does, and the drawer closes itself from a link's click.
  return function MockLink({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
  } & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    );
  };
});

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
}));

jest.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => ({ ready: true }),
}));

jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));

const mockPermissions = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => mockPermissions(),
}));

const mockComponents = jest.fn();
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => mockComponents(),
}));

const mockBetaFeatures = jest.fn();
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => mockBetaFeatures(),
}));

beforeEach(() => {
  mockComponents.mockReset();
  mockPermissions.mockReset();
  mockPermissions.mockReturnValue({
    canAccess: () => true,
    roleName: "admin",
    loading: false,
  });
  mockBetaFeatures.mockReset();
  // Default-deny: beta items hidden unless a test opts in.
  mockBetaFeatures.mockReturnValue({ available: false, flags: {}, loading: false });
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

    expect(screen.queryByText("Notebook Sessions")).not.toBeInTheDocument();
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

    expect(screen.getByText("Notebook Sessions")).toBeInTheDocument();
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

    expect(screen.getByText("Notebook Sessions")).toBeInTheDocument();
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
    expect(screen.getByText("cellxgene Explorer")).toBeInTheDocument();
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
    expect(screen.queryByText("cellxgene Explorer")).not.toBeInTheDocument();
  });

  test("renders its container but no nav items while permissions are still loading", () => {
    // The app-loading splash now lives in the (app) layout (see (app)/layout.test.tsx);
    // Sidebar itself renders its container and gates nav items to empty while
    // permissions are still loading, so no flash of the wrong nav.
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

    expect(screen.getByTestId("sidebar")).toBeInTheDocument();
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

describe("Sidebar beta gating", () => {
  beforeEach(() => {
    mockComponents.mockReturnValue({ components: [], loading: false, refetch: jest.fn() });
  });

  test("hides Validation Studies when the lit_validation flag is off", () => {
    mockBetaFeatures.mockReturnValue({ available: false, flags: {}, loading: false });
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Lab Knowledge"));
    expect(screen.queryByText("Validation Studies")).not.toBeInTheDocument();
  });

  test("shows Validation Studies when the lit_validation flag is on", () => {
    mockBetaFeatures.mockReturnValue({ available: true, flags: { lit_validation: true }, loading: false });
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Lab Knowledge"));
    expect(screen.getByText("Validation Studies")).toBeInTheDocument();
  });

  test("hides the Beta Features settings menu when beta is not available", () => {
    mockBetaFeatures.mockReturnValue({ available: false, flags: {}, loading: false });
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Settings"));
    expect(screen.queryByText("Beta Features")).not.toBeInTheDocument();
  });

  test("shows the Beta Features settings menu when beta is available", () => {
    mockBetaFeatures.mockReturnValue({ available: true, flags: {}, loading: false });
    render(<Sidebar />);
    fireEvent.click(screen.getByText("Settings"));
    expect(screen.getByText("Beta Features")).toBeInTheDocument();
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

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

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

describe("Sidebar brand logo", () => {
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

  test("renders the bioAF mark in the header when expanded", () => {
    render(<Sidebar />);

    const logo = screen.getByTestId("sidebar-logo");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/bioAF-logo.svg");
    expect(logo).toHaveAttribute("alt", "bioAF");
  });

  test("keeps the bioAF mark in the header when collapsed", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

    const logo = screen.getByTestId("sidebar-logo");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/bioAF-logo.svg");
  });

  test("shows the wordmark alongside the logo when expanded", () => {
    render(<Sidebar />);

    expect(screen.getByText("bioAF")).toBeInTheDocument();
  });

  test("shows the tagline under the wordmark when expanded", () => {
    render(<Sidebar />);

    expect(screen.getByText("Comp Bio Automation Framework")).toBeInTheDocument();
  });

  test("does not show the tagline when collapsed", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

    expect(screen.queryByText("Comp Bio Automation Framework")).not.toBeInTheDocument();
  });

  test("renders a backdrop element behind the logo for contrast", () => {
    render(<Sidebar />);

    const backdrop = screen.getByTestId("sidebar-logo-backdrop");
    expect(backdrop).toBeInTheDocument();
    // The backdrop should be present in both expanded and collapsed states
    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    expect(screen.getByTestId("sidebar-logo-backdrop")).toBeInTheDocument();
  });
});

describe("Sidebar header height matches main header", () => {
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

  test("brand header is the same h-16 the main top bar uses", () => {
    render(<Sidebar />);

    // Header.tsx renders <header className="h-16 ...">. The sidebar's brand
    // block must use the same fixed height so the two top bars align.
    const header = screen.getByTestId("sidebar-header");
    expect(header.className).toMatch(/\bh-16\b/);
  });

  test("brand header keeps its h-16 height when collapsed", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

    const header = screen.getByTestId("sidebar-header");
    expect(header.className).toMatch(/\bh-16\b/);
  });
});

describe("Sidebar navigation icons", () => {
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

  test("renders an icon for each top-level section when expanded", () => {
    render(<Sidebar />);

    expect(screen.getByTestId("nav-icon-Dashboard")).toBeInTheDocument();
    expect(screen.getByTestId("nav-icon-Experiments")).toBeInTheDocument();
    expect(screen.getByTestId("nav-icon-Lab Knowledge")).toBeInTheDocument();
  });

  test("keeps navigation reachable as a labelled icon rail when collapsed", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));

    // The rail replaces the old empty collapsed sidebar with per-section icons.
    expect(screen.getByTestId("sidebar-rail")).toBeInTheDocument();
    expect(screen.getByTestId("nav-icon-Experiments")).toBeInTheDocument();
    // Icons are labelled for accessibility (no visible text label in the rail).
    expect(screen.getByLabelText("Experiments")).toBeInTheDocument();
    expect(screen.queryByText("Experiments")).not.toBeInTheDocument();
  });

  test("clicking a collapsed section icon re-expands the sidebar and opens that section", () => {
    render(<Sidebar />);

    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    fireEvent.click(screen.getByLabelText("Pipelines"));

    expect(screen.getByTestId("sidebar")).toHaveAttribute("data-collapsed", "false");
    expect(screen.getByTestId("children-Pipelines")).toBeInTheDocument();
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

// The shell, not the pages, is what breaks a narrow screen: measured on the
// deployed demo at 375px, no route overflowed and no table was clipped, but the
// fixed w-64 sidebar took 256px and left 119px for everything else. Below md the
// sidebar becomes an off-canvas drawer, so the page gets the full width and the
// nav is one tap away.
describe("Sidebar as a drawer on a narrow screen", () => {
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

  test("is off-canvas below md and back in the page flow above it", () => {
    render(<Sidebar />);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.className).toContain("fixed");
    expect(sidebar.className).toContain("-translate-x-full");
    expect(sidebar.className).toContain("md:static");
    expect(sidebar.className).toContain("md:translate-x-0");
  });

  test("slides into view when opened", () => {
    render(<Sidebar mobileOpen onMobileClose={jest.fn()} />);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.className).toContain("translate-x-0");
    expect(sidebar.className).not.toContain("-translate-x-full");
  });

  // A transform moves the drawer off screen but leaves it in the tab order. Measured
  // at 375px on the deployed app, the closed drawer was the first 11 tab stops, so a
  // keyboard user tabbed through 11 controls they could not see before reaching the
  // hamburger. `visibility: hidden` is the only one of translate/opacity/visibility
  // that removes a subtree from the tab order.
  test("is out of the tab order while closed below md, and back in it above md", () => {
    render(<Sidebar />);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar.className).toContain("invisible");
    expect(sidebar.className).toContain("md:visible");
  });

  test("is in the tab order once opened", () => {
    render(<Sidebar mobileOpen onMobileClose={jest.fn()} />);

    expect(screen.getByTestId("sidebar").className).not.toContain("invisible");
  });

  test("covers the page with a scrim only while it is open", () => {
    const { rerender } = render(<Sidebar />);
    expect(screen.queryByTestId("sidebar-scrim")).not.toBeInTheDocument();

    rerender(<Sidebar mobileOpen onMobileClose={jest.fn()} />);
    expect(screen.getByTestId("sidebar-scrim")).toBeInTheDocument();
  });

  test("closes when the scrim is tapped", () => {
    const onClose = jest.fn();
    render(<Sidebar mobileOpen onMobileClose={onClose} />);

    fireEvent.click(screen.getByTestId("sidebar-scrim"));

    expect(onClose).toHaveBeenCalled();
  });

  test("closes on Escape, so the keyboard has the same way out as the pointer", () => {
    const onClose = jest.fn();
    render(<Sidebar mobileOpen onMobileClose={onClose} />);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });

  test("closes once the user has gone somewhere", () => {
    const onClose = jest.fn();
    render(<Sidebar mobileOpen onMobileClose={onClose} />);

    fireEvent.click(screen.getByRole("link", { name: /Dashboard/ }));

    expect(onClose).toHaveBeenCalled();
  });

  test("takes the keyboard while it is open, and names itself", () => {
    render(<Sidebar mobileOpen onMobileClose={jest.fn()} />);

    const sidebar = screen.getByTestId("sidebar");
    expect(sidebar).toHaveAttribute("role", "dialog");
    expect(sidebar).toHaveAccessibleName();
    expect(sidebar.contains(document.activeElement)).toBe(true);
  });

  test("is not a dialog when it is just the page's sidebar", () => {
    render(<Sidebar />);

    expect(screen.getByTestId("sidebar")).not.toHaveAttribute("role", "dialog");
  });
});

// Found on the deployed demo at 375px: the drawer's own brand link goes to the
// dashboard and left the drawer sitting over the page it had just loaded.
test("the drawer closes when its brand link is followed too", () => {
  window.localStorage.clear();
  mockComponents.mockReturnValue({ components: [], loading: false, refetch: jest.fn() });
  const onClose = jest.fn();

  render(<Sidebar mobileOpen onMobileClose={onClose} />);
  fireEvent.click(screen.getByTestId("sidebar-logo").closest("a")!);

  expect(onClose).toHaveBeenCalled();
});
