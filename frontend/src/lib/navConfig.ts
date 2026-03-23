export interface NavChild {
  label: string;
  path: string;
}

export interface NavSection {
  label: string;
  path?: string;
  icon: string;
  children?: NavChild[];
  adminOnly?: boolean;
}

export const navConfig: NavSection[] = [
  { label: "Dashboard", path: "/dashboard", icon: "home" },
  {
    label: "Results",
    icon: "chart",
    children: [
      { label: "QC Dashboards", path: "/results/qc-dashboards" },
      { label: "Cellxgene", path: "/results/cellxgene" },
      { label: "Plot Archive", path: "/results/plot-archive" },
    ],
  },
  {
    label: "Pipelines",
    icon: "play",
    children: [
      { label: "Pipeline Catalog", path: "/pipelines/catalog" },
      { label: "Pipeline Runs", path: "/pipelines/runs" },
      { label: "Pipeline Scheduling", path: "/pipelines/scheduling" },
    ],
  },
  {
    label: "Projects",
    icon: "folder",
    children: [
      { label: "Project List", path: "/projects" },
      { label: "Experiment Templates", path: "/projects/experiment-templates" },
      { label: "Experiment List", path: "/projects/experiments" },
    ],
  },
  { label: "Notebooks", path: "/notebooks", icon: "notebook" },
  {
    label: "Data & Files",
    icon: "database",
    children: [
      { label: "Upload", path: "/data/upload" },
      { label: "Files", path: "/data/files" },
      { label: "Dataset Browser", path: "/data/browser" },
      { label: "Documents", path: "/data/documents" },
      { label: "Reference Data", path: "/data/references" },
    ],
  },
  {
    label: "Infrastructure",
    icon: "server",
    children: [
      { label: "Components", path: "/infrastructure/components" },
      { label: "Compute", path: "/infrastructure/compute" },
      { label: "Environments", path: "/infrastructure/environments" },
      { label: "Packages", path: "/infrastructure/packages" },
      { label: "Cost Center", path: "/infrastructure/cost-center" },
      { label: "Backup & Recovery", path: "/infrastructure/backup" },
    ],
  },
  {
    label: "Profile",
    icon: "user",
    children: [
      { label: "Account & Credentials", path: "/profile" },
      { label: "Notifications", path: "/profile/notifications" },
    ],
  },
  {
    label: "Settings",
    icon: "settings",
    adminOnly: true,
    children: [
      { label: "Users & Roles", path: "/settings/users" },
      { label: "Audit Log", path: "/settings/audit-log" },
      { label: "GCP Configuration", path: "/settings/gcp" },
      { label: "Naming Profiles", path: "/settings/naming-profiles" },
      { label: "SMTP Configuration", path: "/settings/smtp" },
      { label: "Slack Integration", path: "/settings/slack" },
      { label: "Information", path: "/settings/info" },
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
