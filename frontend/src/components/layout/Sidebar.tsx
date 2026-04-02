"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect, useMemo, useCallback } from "react";
import { getCurrentUser } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { useComponents } from "@/hooks/useComponents";
import { navConfig, NavSection, NavChild, ComponentGate, isChildActive } from "@/lib/navConfig";

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

function SidebarChildItem({
  child,
  isActive,
}: {
  child: NavChild;
  isActive: boolean;
}) {
  return (
    <Link
      href={child.path}
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
}: {
  section: NavSection;
  pathname: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const isExpandable = !!section.children;
  const isSectionActive = isExpandable
    ? section.children!.some((c) => isChildActive(pathname, c, section.children!))
    : pathname === section.path ||
      (section.path === "/dashboard" && pathname === "/");

  if (!isExpandable) {
    return (
      <Link
        href={section.path!}
        className={`flex items-center px-3 py-2 rounded-md transition-colors ${
          isSectionActive
            ? "bg-bioaf-700 text-white"
            : "text-gray-300 hover:bg-gray-800 hover:text-white"
        }`}
      >
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
        <span>{section.label}</span>
        <ChevronIcon expanded={expanded} />
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5" data-testid={`children-${section.label}`}>
          {section.children!.map((child) => (
            <SidebarChildItem
              key={child.path}
              child={child}
              isActive={isChildActive(pathname, child, section.children!)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const [user, setUser] = useState<Record<string, unknown> | null>(null);
  const { canAccess, roleName, loading } = usePermissions();
  const { components, loading: componentsLoading } = useComponents();

  useEffect(() => {
    setUser(getCurrentUser());
  }, []);

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

  // Filter sections and children based on permissions and component gates
  const visibleSections = useMemo(() => {
    if (loading) return [];
    return navConfig
      .filter((section) => {
        if (section.adminOnly && roleName !== "admin") return false;
        if (section.permission && !canAccess(section.permission.resource, section.permission.action)) {
          return false;
        }
        if (!passesComponentGate(section.componentGate)) return false;
        if (section.children) {
          return section.children.some(
            (child) =>
              (!child.permission || canAccess(child.permission.resource, child.permission.action)) &&
              passesComponentGate(child.componentGate),
          );
        }
        return true;
      })
      .map((section) => {
        if (!section.children) return section;
        const filteredChildren = section.children.filter(
          (child) =>
            (!child.permission || canAccess(child.permission.resource, child.permission.action)) &&
            passesComponentGate(child.componentGate),
        );
        return { ...section, children: filteredChildren };
      });
  }, [loading, roleName, canAccess, passesComponentGate]);

  // Initialize expanded state: auto-expand section containing active path
  const [expandedSections, setExpandedSections] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const section of navConfig) {
      if (section.children) {
        const hasActiveChild = section.children.some((c) =>
          isChildActive(pathname, c, section.children!),
        );
        if (hasActiveChild) {
          initial.add(section.label);
        }
      }
    }
    return initial;
  });

  // Auto-expand when navigating to a new section
  useEffect(() => {
    for (const section of visibleSections) {
      if (section.children) {
        const hasActiveChild = section.children.some((c) =>
          isChildActive(pathname, c, section.children!),
        );
        if (hasActiveChild && !expandedSections.has(section.label)) {
          setExpandedSections((prev) => new Set(prev).add(section.label));
        }
      }
    }
  }, [pathname, visibleSections]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSection = (label: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(label)) {
        next.delete(label);
      } else {
        next.add(label);
      }
      return next;
    });
  };

  return (
    <aside className="w-64 bg-gray-900 text-white min-h-screen flex flex-col" data-testid="sidebar">
      <div className="p-6 border-b border-gray-700">
        <Link href="/dashboard" className="text-xl font-bold text-bioaf-400">
          bioAF
        </Link>
        <p className="text-xs text-gray-400 mt-1">Comp Bio Automation Framework</p>
      </div>

      <nav className="flex-1 p-4 space-y-1 overflow-y-auto" data-testid="sidebar-nav">
        {visibleSections.map((section) => (
          <SidebarSection
            key={section.label}
            section={section}
            pathname={pathname}
            expanded={expandedSections.has(section.label)}
            onToggle={() => toggleSection(section.label)}
          />
        ))}
      </nav>

      <div className="p-4 border-t border-gray-700">
        {user && (
          <div className="text-xs text-gray-400">
            <div className="truncate">{user.email as string}</div>
            <div className="text-gray-500 mt-0.5">{user.role_name as string}</div>
          </div>
        )}
        <div className="text-xs text-gray-600 mt-2">v{process.env.NEXT_PUBLIC_APP_VERSION}</div>
      </div>
    </aside>
  );
}
