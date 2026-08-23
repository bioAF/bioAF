export interface PermissionRef {
  resource: string;
  action: string;
}

export interface ComponentGate {
  /** At least one component matching these keys must be enabled */
  keys?: string[];
  /** At least one component in this category must be enabled */
  category?: string;
}

export interface NavChild {
  label: string;
  path: string;
  permission?: PermissionRef;
  /** Shown if the user holds ANY of these permissions. Combined with
   * `permission` (which still must pass if set). */
  anyPermissions?: PermissionRef[];
  componentGate?: ComponentGate;
  /** Hidden when the active backend lacks this BAL capability (distinct from
   * componentGate availability). See useCapabilities / ProviderCapabilities. */
  capability?: string;
  /** Hidden unless this beta feature flag is enabled (spec-07). See useBetaFeatures. */
  betaFlag?: string;
}

export interface NavSection {
  label: string;
  path?: string;
  icon: string;
  children?: NavChild[];
  adminOnly?: boolean;
  permission?: PermissionRef;
  anyPermissions?: PermissionRef[];
  componentGate?: ComponentGate;
  capability?: string;
}

// Every label here is also the page's own <h1>, and Breadcrumb.tsx builds its
// trail from these strings, so a label that drifts from its page says the wrong
// thing in three places at once. Held by src/__tests__/nav-label-agreement.test.ts.
export const navConfig: NavSection[] = [
  { label: "Dashboard", path: "/dashboard", icon: "home" },
  // The assistant is now a global floating bubble (FloatingAssistant), not a nav destination, so it
  // follows the user across pages instead of being a single full-page surface.
  {
    label: "Experiments",
    icon: "folder",
    children: [
      { label: "Project List", path: "/projects", permission: { resource: "projects", action: "view" } },
      { label: "Experiment Templates", path: "/projects/experiment-templates", permission: { resource: "experiments", action: "view" } },
      { label: "Experiment List", path: "/projects/experiments", permission: { resource: "experiments", action: "view" } },
      { label: "Dataset Browser", path: "/data/browser", permission: { resource: "experiments", action: "view" } },
    ],
  },
  {
    label: "Pipelines",
    icon: "play",
    componentGate: { category: "pipeline_orchestration" },
    children: [
      { label: "Pipeline Catalog", path: "/pipelines/catalog", permission: { resource: "pipelines", action: "view" } },
      { label: "Custom Pipelines", path: "/pipelines/custom", permission: { resource: "custom_pipelines", action: "view" } },
      { label: "Pipeline Runs", path: "/pipelines/runs", permission: { resource: "pipelines", action: "view" } },
      { label: "Pipeline Templates", path: "/pipelines/environments", permission: { resource: "environments", action: "view" } },
    ],
  },
  {
    label: "Results",
    icon: "chart",
    children: [
      { label: "QC Dashboards", path: "/results/qc-dashboards", anyPermissions: [{ resource: "experiments", action: "view" }, { resource: "pipelines", action: "view" }], componentGate: { keys: ["qc_dashboard"] } },
      { label: "cellxgene Explorer", path: "/results/cellxgene", anyPermissions: [{ resource: "experiments", action: "view" }, { resource: "pipelines", action: "view" }], componentGate: { keys: ["cellxgene"] }, capability: "cellxgene" },
      { label: "Plot Archive", path: "/results/plot-archive", anyPermissions: [{ resource: "experiments", action: "view" }, { resource: "pipelines", action: "view" }] },
    ],
  },
  {
    label: "Workbench",
    icon: "notebook",
    children: [
      { label: "Notebook Sessions", path: "/notebooks", permission: { resource: "notebooks", action: "view" }, componentGate: { keys: ["jupyterhub", "rstudio"] } },
      { label: "Work Nodes", path: "/workbench/work-nodes", permission: { resource: "notebooks", action: "view" }, capability: "work_nodes" },
      { label: "Workbench Templates", path: "/environments", permission: { resource: "environments", action: "view" } },
    ],
  },
  {
    label: "Data & Files",
    icon: "database",
    children: [
      { label: "Data Upload", path: "/data/upload", permission: { resource: "files", action: "upload" } },
      { label: "Files", path: "/data/files", permission: { resource: "files", action: "view" } },
      { label: "Reference Data", path: "/data/references", permission: { resource: "files", action: "view" } },
      { label: "Naming Profiles", path: "/settings/naming-profiles", permission: { resource: "infrastructure", action: "configure" } },
    ],
  },
  {
    label: "Lab Knowledge",
    icon: "book",
    children: [
      { label: "Lab Documents", path: "/lab-knowledge/documents", permission: { resource: "lab_documents", action: "view" } },
      // Papers-as-knowledge: Literature + its Validation Studies live alongside Documents/Glossary,
      // where scientists look for reference material (moved out of Data & Files).
      { label: "Literature Library", path: "/lab-knowledge/literature", permission: { resource: "literature", action: "view" } },
      { label: "Validation Studies", path: "/lab-knowledge/validation-studies", permission: { resource: "lit_validation", action: "view" }, betaFlag: "lit_validation" },
      { label: "Lab Glossary", path: "/lab-knowledge/glossary", permission: { resource: "lab_glossary", action: "view" } },
      { label: "Scientific Decision Records", path: "/lab-knowledge/decision-records", permission: { resource: "sdr", action: "view" } },
    ],
  },
  {
    label: "Infrastructure",
    icon: "server",
    children: [
      { label: "Components", path: "/infrastructure/components", permission: { resource: "infrastructure", action: "view" } },
      { label: "Cost Center", path: "/infrastructure/cost-center", permission: { resource: "cost_center", action: "view" } },
      { label: "Backup & Recovery", path: "/infrastructure/backup", permission: { resource: "backups", action: "view" } },
    ],
  },
  {
    label: "Settings",
    icon: "settings",
    adminOnly: true,
    children: [
      { label: "Users & Accounts", path: "/settings/users", permission: { resource: "users", action: "view" } },
      { label: "Roles & Permissions", path: "/settings/roles", permission: { resource: "roles", action: "view" } },
      { label: "Audit Log", path: "/settings/audit-log", permission: { resource: "audit_log", action: "view" } },
      { label: "Integrations", path: "/settings/integrations", permission: { resource: "infrastructure", action: "configure" } },
      { label: "Workbench Settings", path: "/settings/work-nodes", permission: { resource: "work_nodes", action: "configure" }, capability: "work_nodes" },
      { label: "Networking", path: "/settings/networking", permission: { resource: "infrastructure", action: "edit" } },
      { label: "Beta Features", path: "/settings/beta-features", permission: { resource: "infrastructure", action: "configure" } },
      { label: "Platform Info", path: "/settings/info", permission: { resource: "infrastructure", action: "view" } },
    ],
  },
];

/**
 * Check if a child nav item should be active for the given pathname.
 * Uses startsWith matching but excludes paths that match a more specific sibling.
 */
export function isChildActive(
  pathname: string,
  child: NavChild,
  siblings: NavChild[],
): boolean {
  if (pathname === child.path) return true;
  if (!pathname.startsWith(child.path + "/")) return false;
  // Ensure no sibling is a more specific match
  for (const sibling of siblings) {
    if (sibling.path === child.path) continue;
    if (
      sibling.path.startsWith(child.path + "/") &&
      (pathname === sibling.path || pathname.startsWith(sibling.path + "/"))
    ) {
      return false;
    }
  }
  return true;
}

/**
 * Find the nav section and child for a given pathname.
 * Returns { section, child } or { section } for top-level pages.
 */
export function findNavMatch(
  pathname: string,
): { section: NavSection; child?: NavChild } | null {
  for (const section of navConfig) {
    if (section.path && pathname === section.path) {
      return { section };
    }
    if (section.children) {
      // Sort by path length descending so more specific paths match before shorter ones
      const sorted = [...section.children].sort((a, b) => b.path.length - a.path.length);
      for (const child of sorted) {
        if (pathname === child.path || pathname.startsWith(child.path + "/")) {
          return { section, child };
        }
      }
    }
  }
  // Special case: root path maps to dashboard
  if (pathname === "/") {
    return { section: navConfig[0] };
  }
  return null;
}
