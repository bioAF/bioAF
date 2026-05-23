import type { ComponentType } from "react";

import { InfrastructureHealthWidget } from "@/components/dashboard/InfrastructureHealthWidget";
import { RunningJobsWidget } from "@/components/dashboard/RunningJobsWidget";
import { QueueDepthWidget } from "@/components/dashboard/QueueDepthWidget";
import { CostBudgetWidget } from "@/components/dashboard/CostBudgetWidget";
import { ActivityFeedWidget } from "@/components/dashboard/ActivityFeedWidget";
import { ExperimentsStatusWidget } from "@/components/dashboard/ExperimentsStatusWidget";
import { FailedRunsWidget } from "@/components/dashboard/FailedRunsWidget";
import { RunsAwaitingReviewWidget } from "@/components/dashboard/RunsAwaitingReviewWidget";
import { MyCustomPipelinesWidget } from "@/components/dashboard/MyCustomPipelinesWidget";
import { MySessionsWidget } from "@/components/dashboard/MySessionsWidget";

export type PermissionPair = readonly [resource: string, action: string];
export type CanAccess = (resource: string, action: string) => boolean;

export interface WidgetDefinition {
  key: string;
  title: string;
  description: string;
  component: ComponentType;
  /** A user may use this widget if they hold ANY of these permissions. */
  permissions: ReadonlyArray<PermissionPair>;
  /** roleName values (from /api/auth/me) that get this widget by default. */
  defaultForRoles: ReadonlyArray<string>;
}

// RBAC built-in roles (bootstrap_roles.py). The CSO persona maps to `admin`, so
// the admin default set is the CSO oversight set plus the compact admin overlay.
const KNOWN_ROLES = new Set(["bench", "comp_bio", "admin", "viewer"]);

// What an unknown/custom role lands on until they customize.
const FALLBACK_DEFAULT_KEYS: readonly string[] = ["experiments_status"];

function ActivityFeedFullHeight() {
  return <ActivityFeedWidget className="h-full" />;
}

/**
 * The single source of truth for the dashboard widget catalog. Add new widgets
 * here; the picker, the renderer, and the role defaults all derive from this.
 */
export const WIDGETS: readonly WidgetDefinition[] = [
  {
    key: "experiments_status",
    title: "Experiments status",
    description: "Recent experiments and where they stand.",
    component: ExperimentsStatusWidget,
    permissions: [["experiments", "view"]],
    defaultForRoles: ["bench", "viewer"],
  },
  {
    key: "active_pipeline_runs",
    title: "Active pipeline runs",
    description: "Pipelines currently running or pending.",
    component: RunningJobsWidget,
    permissions: [["pipelines", "view"]],
    defaultForRoles: ["comp_bio"],
  },
  {
    key: "failed_runs",
    title: "Failed runs",
    description: "Recent pipeline failures, filterable by time window.",
    component: FailedRunsWidget,
    permissions: [["pipelines", "view"]],
    defaultForRoles: ["comp_bio"],
  },
  {
    key: "runs_awaiting_review",
    title: "Runs awaiting review",
    description: "Completed runs that still need a review verdict.",
    component: RunsAwaitingReviewWidget,
    permissions: [["pipelines", "view"]],
    defaultForRoles: ["comp_bio", "admin"],
  },
  {
    key: "queue_depth",
    title: "Queue depth",
    description: "Jobs queued, including any awaiting budget approval.",
    component: QueueDepthWidget,
    permissions: [["pipelines", "view"]],
    defaultForRoles: ["comp_bio"],
  },
  {
    key: "my_custom_pipelines",
    title: "My custom pipelines",
    description: "Custom pipelines you have registered.",
    component: MyCustomPipelinesWidget,
    permissions: [["custom_pipelines", "view"]],
    defaultForRoles: ["comp_bio"],
  },
  {
    key: "my_sessions",
    title: "My active sessions",
    description: "Running notebooks and work nodes.",
    component: MySessionsWidget,
    permissions: [
      ["notebooks", "view"],
      ["work_nodes", "view"],
    ],
    defaultForRoles: ["comp_bio"],
  },
  {
    key: "cost_budget",
    title: "Cost vs budget",
    description: "This month's spend against the monthly budget.",
    component: CostBudgetWidget,
    permissions: [["cost_center", "view"]],
    defaultForRoles: ["admin"],
  },
  {
    key: "infra_health",
    title: "Infrastructure health",
    description: "Health of the platform's backend services.",
    component: InfrastructureHealthWidget,
    permissions: [["infrastructure", "view"]],
    defaultForRoles: ["admin"],
  },
  {
    key: "activity_feed",
    title: "Activity feed",
    description: "Recent activity across the workspace.",
    component: ActivityFeedFullHeight,
    permissions: [["audit_log", "view"]],
    defaultForRoles: [],
  },
];

export function getWidget(key: string): WidgetDefinition | undefined {
  return WIDGETS.find((w) => w.key === key);
}

export function canUseWidget(def: WidgetDefinition, canAccess: CanAccess): boolean {
  return def.permissions.some(([resource, action]) => canAccess(resource, action));
}

/** Widgets the user is permitted to use, in catalog order. */
export function accessibleWidgets(canAccess: CanAccess): WidgetDefinition[] {
  return WIDGETS.filter((w) => canUseWidget(w, canAccess));
}

/**
 * The default widget keys for a role, in catalog order. Permission pruning is
 * applied separately at render/picker time, so a role default that a particular
 * user cannot access is harmless.
 */
export function defaultLayoutForRole(roleName: string): string[] {
  if (!KNOWN_ROLES.has(roleName)) {
    return [...FALLBACK_DEFAULT_KEYS];
  }
  return WIDGETS.filter((w) => w.defaultForRoles.includes(roleName)).map((w) => w.key);
}
