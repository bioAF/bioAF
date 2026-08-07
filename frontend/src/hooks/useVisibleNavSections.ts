"use client";

import { useCallback, useMemo } from "react";
import { usePermissions } from "@/hooks/usePermissions";
import { useCapabilities, type CapabilityFlag } from "@/hooks/useCapabilities";
import { useComponents } from "@/hooks/useComponents";
import { useBetaFeatures } from "@/hooks/useBetaFeatures";
import { navConfig, type NavSection, type ComponentGate, type PermissionRef } from "@/lib/navConfig";

/**
 * The navigation this user can actually reach.
 *
 * Four independent gates decide that: role, permission, whether the installed
 * components back the feature, whether the active backend declares the
 * capability, and whether a beta flag is on. The Sidebar owned all of it
 * inline, which was fine while the Sidebar was the only reader. The section
 * index routes are a second reader, and two copies of a five-way gate is how a
 * menu and a page start disagreeing about what exists.
 */
export function useVisibleNavSections() {
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

  const sections: NavSection[] = useMemo(() => {
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

  /** Where a bare section URL should land, or null if it holds nothing for this user. */
  const firstChildPath = useCallback(
    (label: string): string | null => {
      const section = sections.find((s) => s.label === label);
      if (!section) return null;
      return section.children?.[0]?.path ?? section.path ?? null;
    },
    [sections],
  );

  return { sections, loading, firstChildPath };
}
