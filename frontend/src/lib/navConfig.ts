export interface PermissionRef {
  resource: string;
  action: string;
}

export interface NavChild {
  label: string;
  path: string;
  permission?: PermissionRef;
}

export interface NavSection {
  label: string;
  path?: string;
  icon: string;
  children?: NavChild[];
  adminOnly?: boolean;
  permission?: PermissionRef;
}

export const navConfig: NavSection[] = [
  { label: "Dashboard", path: "/dashboard", icon: "home" },
  {
    label: "Results",
    icon: "chart",
    children: [
      { label: "QC Dashboards", path: "/results/qc-dashboards", permission: { resource: "experiments", action: "view" } },
      { label: "Cellxgene", path: "/results/cellxgene", permission: { resource: "experiments", action: "view" } },
      { label: "Plot Archive", path: "/results/plot-archive", permission: { resource: "experiments", action: "view" } },
    ],
  },
  {
    label: "Pipelines",
    icon: "play",
    children: [
      { label: "Pipeline Catalog", path: "/pipelines/catalog", permission: { resource: "pipelines", action: "view" } },
      { label: "Pipeline Runs", path: "/pipelines/runs", permission: { resource: "pipelines", action: "view" } },
      { label: "Pipeline Scheduling", path: "/pipelines/scheduling", permission: { resource: "pipelines", action: "view" } },
    ],
  },
  {
    label: "Projects",
    icon: "folder",
    children: [
      { label: "Project List", path: "/projects", permission: { resource: "projects", action: "view" } },
      { label: "Experiment Templates", path: "/projects/experiment-templates", permission: { resource: "experiments", action: "view" } },
      { label: "Experiment List", path: "/projects/experiments", permission: { resource: "experiments", action: "view" } },
    ],
  },
  {
    label: "Workbench",
    icon: "notebook",
    children: [
      { label: "Notebooks", path: "/notebooks", permission: { resource: "notebooks", action: "view" } },
      { label: "Work Nodes", path: "/workbench/work-nodes", permission: { resource: "notebooks", action: "view" } },
      { label: "Environments", path: "/environments", permission: { resource: "environments", action: "view" } },
    ],
  },
  {
    label: "Data & Files",
    icon: "database",
    children: [
      { label: "Upload", path: "/data/upload", permission: { resource: "files", action: "upload" } },
      { label: "Files", path: "/data/files", permission: { resource: "files", action: "view" } },
      { label: "Dataset Browser", path: "/data/browser", permission: { resource: "files", action: "view" } },
      { label: "Documents", path: "/data/documents", permission: { resource: "files", action: "view" } },
      { label: "Reference Data", path: "/data/references", permission: { resource: "files", action: "view" } },
    ],
  },
  {
    label: "Infrastructure",
    icon: "server",
    children: [
      { label: "Components", path: "/infrastructure/components", permission: { resource: "infrastructure", action: "view" } },
      { label: "Compute", path: "/infrastructure/compute", permission: { resource: "infrastructure", action: "view" } },
      { label: "Cost Center", path: "/infrastructure/cost-center", permission: { resource: "cost_center", action: "view" } },
      { label: "Backup & Recovery", path: "/infrastructure/backup", permission: { resource: "backups", action: "view" } },
    ],
  },
  {
    label: "Profile",
    icon: "user",
    children: [
      { label: "Account & Credentials", path: "/profile" },
      { label: "Notifications", path: "/profile/notifications", permission: { resource: "notifications", action: "view" } },
    ],
  },
  {
    label: "Settings",
    icon: "settings",
    adminOnly: true,
    children: [
      { label: "Users", path: "/settings/users", permission: { resource: "users", action: "view" } },
      { label: "Roles & Permissions", path: "/settings/roles", permission: { resource: "roles", action: "view" } },
      { label: "Audit Log", path: "/settings/audit-log", permission: { resource: "audit_log", action: "view" } },
      { label: "GCP Configuration", path: "/settings/gcp", permission: { resource: "infrastructure", action: "configure" } },
      { label: "Naming Profiles", path: "/settings/naming-profiles", permission: { resource: "infrastructure", action: "configure" } },
      { label: "Work Nodes", path: "/settings/work-nodes", permission: { resource: "work_nodes", action: "configure" } },
      { label: "SMTP Configuration", path: "/settings/smtp", permission: { resource: "notifications", action: "configure" } },
      { label: "Slack Integration", path: "/settings/slack", permission: { resource: "notifications", action: "configure" } },
      { label: "Information", path: "/settings/info", permission: { resource: "infrastructure", action: "view" } },
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
