"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useMemo, useCallback } from "react";
import { usePermissions } from "@/hooks/usePermissions";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";
import { useCapabilities, type CapabilityFlag } from "@/hooks/useCapabilities";
import { useComponents } from "@/hooks/useComponents";
import { useBetaFeatures } from "@/hooks/useBetaFeatures";
import { navConfig, NavSection, NavChild, ComponentGate, PermissionRef, isChildActive } from "@/lib/navConfig";
import { NavIcon } from "./navIcons";

const SIDEBAR_COLLAPSED_KEY = "bioaf-sidebar-collapsed";

// A section is active when the current path is (or is under) one of its children,
// or matches its own path for a childless section. Shared by the expanded rows and
// the collapsed rail so both highlight the same section.
function sectionIsActive(section: NavSection, pathname: string): boolean {
  if (section.children) {
    return section.children.some((c) => isChildActive(pathname, c, section.children!));
  }
  return pathname === section.path || (section.path === "/dashboard" && pathname === "/");
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={`w-4 h-4 transition-transform ${expanded ? "rotate-90" : ""}`}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
    </svg>
  );
}

function CollapseToggleIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg
      className="w-5 h-5"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d={collapsed ? "M9 5l7 7-7 7" : "M15 19l-7-7 7-7"}
      />
    </svg>
  );
}

function SidebarChildItem({
  child,
  isActive,
  onNavigate,
}: {
  child: NavChild;
  isActive: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={child.path}
      onClick={onNavigate}
      className={`block pl-10 pr-3 py-1.5 rounded-md text-sm transition-colors ${
        isActive
          ? "bg-bioaf-700 text-white"
          : "text-gray-400 hover:bg-gray-800 hover:text-white"
      }`}
    >
      {child.label}
    </Link>
  );
}

function SidebarSection({
  section,
  pathname,
  expanded,
  onToggle,
  onNavigate,
}: {
  section: NavSection;
  pathname: string;
  expanded: boolean;
  onToggle: () => void;
  onNavigate?: () => void;
}) {
  const isExpandable = !!section.children;
  const isSectionActive = sectionIsActive(section, pathname);

  if (!isExpandable) {
    return (
      <Link
        href={section.path!}
        onClick={onNavigate}
        className={`flex items-center gap-3 px-3 py-2 rounded-md transition-colors ${
          isSectionActive
            ? "bg-bioaf-700 text-white"
            : "text-gray-300 hover:bg-gray-800 hover:text-white"
        }`}
      >
        <NavIcon name={section.icon} testId={`nav-icon-${section.label}`} />
        <span>{section.label}</span>
      </Link>
    );
  }

  return (
    <div>
      <button
        onClick={onToggle}
        className={`w-full flex items-center justify-between px-3 py-2 rounded-md transition-colors ${
          isSectionActive
            ? "text-white bg-gray-800"
            : "text-gray-300 hover:bg-gray-800 hover:text-white"
        }`}
      >
        <span className="flex items-center gap-3">
          <NavIcon name={section.icon} testId={`nav-icon-${section.label}`} />
          <span>{section.label}</span>
        </span>
        <ChevronIcon expanded={expanded} />
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5" data-testid={`children-${section.label}`}>
          {section.children!.map((child) => (
            <SidebarChildItem
              key={child.path}
              child={child}
              isActive={isChildActive(pathname, child, section.children!)}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Below `md` the sidebar leaves the page flow and becomes a drawer.
 *
 * Measured on the deployed demo at 375px with real data: no route overflowed
 * and no table was clipped, so the pages were never the problem. This element
 * was: a fixed `w-64` with no breakpoint takes 256px of a 375px screen and
 * leaves 119px for the page. Off-canvas, the page gets all of it, and the nav
 * is one tap on the header's control.
 *
 * `mobileOpen` is owned by the (app) layout, because the control that opens it
 * lives in the Header, which is the sidebar's sibling.
 */
export function Sidebar({
  mobileOpen = false,
  onMobileClose,
}: {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
} = {}) {
  const pathname = usePathname();
  const { canAccess, roleName, loading } = usePermissions();
  const { has: hasCapability } = useCapabilities();
  const { components, loading: componentsLoading } = useComponents();
  const { available: betaAvailable, flags: betaFlags } = useBetaFeatures();

  const passesComponentGate = useCallback(
    (gate?: ComponentGate): boolean => {
      if (!gate) return true;
      // While components are loading, show everything to avoid flash-of-missing-nav
      if (componentsLoading) return true;
      if (gate.category) {
        return components.some((c) => c.category === gate.category && c.enabled);
      }
      if (gate.keys) {
        return components.some((c) => gate.keys!.includes(c.key) && c.enabled);
      }
      return true;
    },
    [components, componentsLoading],
  );

  // A nav item passes when its single `permission` (if any) is granted AND, when
  // `anyPermissions` is set, at least one of those is granted. This lets a
  // surface reachable from several places (e.g. Results) require "view
  // experiments OR view pipelines".
  const passesPermission = useCallback(
    (item: { permission?: PermissionRef; anyPermissions?: PermissionRef[] }): boolean => {
      if (item.permission && !canAccess(item.permission.resource, item.permission.action)) {
        return false;
      }
      if (item.anyPermissions && !item.anyPermissions.some((p) => canAccess(p.resource, p.action))) {
        return false;
      }
      return true;
    },
    [canAccess],
  );

  // A nav item passes its capability gate when the active backend declares the
  // required BAL capability (or the item has none). This hides entry points the
  // backend cannot serve (e.g. cellxgene / work nodes on a SLURM/NFS stack),
  // distinct from componentGate availability.
  const passesCapability = useCallback(
    (item: { capability?: string }): boolean => {
      if (!item.capability) return true;
      return hasCapability(item.capability as CapabilityFlag);
    },
    [hasCapability],
  );

  // A nav item passes its beta gate when its required beta flag is enabled (and, for the Beta Features
  // menu itself, when beta features are available on this instance). useBetaFeatures default-denies
  // while loading, so a hidden beta feature never flashes (spec-07).
  const passesBetaGate = useCallback(
    (item: { betaFlag?: string; requiresBetaAvailability?: boolean }): boolean => {
      if (item.requiresBetaAvailability && !betaAvailable) return false;
      if (item.betaFlag && !betaFlags[item.betaFlag]) return false;
      return true;
    },
    [betaAvailable, betaFlags],
  );

  // Filter sections and children based on permissions, component gates, and
  // backend capabilities
  const visibleSections = useMemo(() => {
    if (loading) return [];
    return navConfig
      .filter((section) => {
        if (section.adminOnly && roleName !== "admin") return false;
        if (!passesPermission(section)) return false;
        if (!passesComponentGate(section.componentGate)) return false;
        if (!passesCapability(section)) return false;
        if (section.children) {
          return section.children.some(
            (child) =>
              passesPermission(child) &&
              passesComponentGate(child.componentGate) &&
              passesCapability(child) &&
              passesBetaGate(child),
          );
        }
        return true;
      })
      .map((section) => {
        if (!section.children) return section;
        const filteredChildren = section.children.filter(
          (child) =>
            passesPermission(child) &&
            passesComponentGate(child.componentGate) &&
            passesCapability(child) &&
            passesBetaGate(child),
        );
        return { ...section, children: filteredChildren };
      });
  }, [loading, roleName, passesPermission, passesComponentGate, passesCapability, passesBetaGate]);

  // Initialize expanded state: auto-expand the section containing active path.
  // Only one section can be expanded at a time.
  const [expandedSection, setExpandedSection] = useState<string | null>(() => {
    for (const section of navConfig) {
      if (section.children) {
        const hasActiveChild = section.children.some((c) =>
          isChildActive(pathname, c, section.children!),
        );
        if (hasActiveChild) {
          return section.label;
        }
      }
    }
    return null;
  });

  // Auto-expand when navigating to a new section. Replaces any previously
  // expanded section so the one-at-a-time invariant holds.
  useEffect(() => {
    for (const section of visibleSections) {
      if (section.children) {
        const hasActiveChild = section.children.some((c) =>
          isChildActive(pathname, c, section.children!),
        );
        if (hasActiveChild && expandedSection !== section.label) {
          setExpandedSection(section.label);
          return;
        }
      }
    }
  }, [pathname, visibleSections]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSection = (label: string) => {
    setExpandedSection((prev) => (prev === label ? null : label));
  };

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "true" : "false");
  }, [collapsed]);

  // Open over the page, it behaves as a dialog: the keyboard stays inside it,
  // Escape closes it, and following a link closes it rather than leaving the
  // drawer covering the page the user just asked for.
  const drawerRef = useFocusTrap<HTMLElement>(mobileOpen);
  useDismissOnEscape(mobileOpen, () => onMobileClose?.());
  const closeDrawer = () => onMobileClose?.();

  return (
    <>
    {mobileOpen && (
      <div
        data-testid="sidebar-scrim"
        onClick={closeDrawer}
        aria-hidden="true"
        className="fixed inset-0 z-30 bg-black/50 md:hidden"
      />
    )}
    <aside
      ref={drawerRef}
      tabIndex={mobileOpen ? -1 : undefined}
      role={mobileOpen ? "dialog" : undefined}
      aria-modal={mobileOpen ? true : undefined}
      aria-label={mobileOpen ? "Main navigation" : undefined}
      className={`${collapsed ? "w-12" : "w-64"} ${
        mobileOpen ? "translate-x-0" : "-translate-x-full"
      } fixed inset-y-0 left-0 z-40 md:static md:z-auto md:translate-x-0 bg-gray-900 text-white min-h-screen flex flex-col transition-transform md:transition-[width] duration-150 dark:border-r dark:border-gray-800`}
      id="app-sidebar"
      data-testid="sidebar"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <div
        data-testid="sidebar-header"
        className={`h-16 flex items-center border-b border-gray-700 ${collapsed ? "justify-center px-2" : "justify-between px-4"}`}
      >
        {collapsed ? (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            aria-label="Expand sidebar"
            aria-expanded={false}
            data-testid="sidebar-collapse-toggle"
            className="rounded-md hover:bg-gray-800"
          >
            <span
              data-testid="sidebar-logo-backdrop"
              className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10"
            >
              <img
                src="/bioAF-logo.svg"
                alt="bioAF"
                data-testid="sidebar-logo"
                className="h-7 w-7"
              />
            </span>
          </button>
        ) : (
          <>
            <Link href="/dashboard" onClick={closeDrawer} className="flex items-center gap-2">
              <span
                data-testid="sidebar-logo-backdrop"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/10"
              >
                <img
                  src="/bioAF-logo.svg"
                  alt="bioAF"
                  data-testid="sidebar-logo"
                  className="h-7 w-7"
                />
              </span>
              <span className="flex flex-col leading-none">
                <span className="text-base font-bold text-bioaf-400">bioAF</span>
                <span className="mt-0.5 text-[10px] tracking-tight text-gray-400 whitespace-nowrap">
                  Comp Bio Automation Framework
                </span>
              </span>
            </Link>
            <button
              type="button"
              onClick={() => setCollapsed(true)}
              aria-label="Collapse sidebar"
              aria-expanded={true}
              data-testid="sidebar-collapse-toggle"
              className="p-1 rounded-md text-gray-400 hover:bg-gray-800 hover:text-white"
            >
              <CollapseToggleIcon collapsed={false} />
            </button>
          </>
        )}
      </div>

      {!collapsed && (
        <nav className="flex-1 p-4 space-y-1 overflow-y-auto" data-testid="sidebar-nav">
          {visibleSections.map((section) => (
            <SidebarSection
              key={section.label}
              section={section}
              pathname={pathname}
              expanded={expandedSection === section.label}
              onToggle={() => toggleSection(section.label)}
              onNavigate={closeDrawer}
            />
          ))}
        </nav>
      )}

      {collapsed && (
        <nav
          className="flex-1 overflow-y-auto py-4 flex flex-col items-center gap-1"
          data-testid="sidebar-rail"
        >
          {visibleSections.map((section) => {
            const active = sectionIsActive(section, pathname);
            const cls = `flex h-10 w-10 items-center justify-center rounded-md transition-colors ${
              active
                ? "bg-bioaf-700 text-white"
                : "text-gray-300 hover:bg-gray-800 hover:text-white"
            }`;
            return section.children ? (
              <button
                key={section.label}
                type="button"
                aria-label={section.label}
                title={section.label}
                onClick={() => {
                  setCollapsed(false);
                  setExpandedSection(section.label);
                }}
                className={cls}
              >
                <NavIcon name={section.icon} testId={`nav-icon-${section.label}`} />
              </button>
            ) : (
              <Link
                key={section.label}
                href={section.path!}
                onClick={closeDrawer}
                aria-label={section.label}
                title={section.label}
                className={cls}
              >
                <NavIcon name={section.icon} testId={`nav-icon-${section.label}`} />
              </Link>
            );
          })}
        </nav>
      )}

      <div className={`border-t border-gray-700 ${collapsed ? "p-2 text-center" : "p-4"}`}>
        <div className="text-xs text-gray-400">v{process.env.NEXT_PUBLIC_APP_VERSION}</div>
      </div>
    </aside>
    </>
  );
}
