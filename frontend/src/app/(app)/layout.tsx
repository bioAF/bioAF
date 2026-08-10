"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { useBackendReady } from "@/hooks/useBackendReady";
import { usePermissions, clearPermissionsCache } from "@/hooks/usePermissions";
import { useComponents, invalidateComponentCache } from "@/hooks/useComponents";
import { ToastProvider } from "@/components/shared/Toast";
import { BootSplash } from "@/components/layout/BootSplash";

/**
 * Shared shell for every authenticated page. It mounts the Sidebar + Header ONCE
 * (pages used to hand-mount them 57 times, each re-opening the same flex wrapper
 * in every return branch), hosts the single auth gate that redirects an
 * unauthenticated visitor to /login, and owns the app-loading splash while the
 * backend / permissions / components are still loading (this used to live inside
 * Sidebar, which is now pure navigation).
 *
 * The layout owns the outer chrome but NOT the `<main>`: each page renders its own
 * `<main>` so it keeps its per-page scroll/centered variant and its data-testid.
 * A page that used to render chrome-less (e.g. a bare loading spinner) now renders
 * inside this shell.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { ready: backendReady, unreachable: backendUnreachable, retryNow } = useBackendReady();
  const { loading: permissionsLoading, failed: permissionsFailed } = usePermissions();
  const { loading: componentsLoading } = useComponents();

  // Below `md` the sidebar is off-canvas, so the shell owns whether it is
  // showing: the control that opens it lives in the Header, which is the
  // sidebar's sibling rather than its parent.
  const [navOpen, setNavOpen] = useState(false);

  // Redirect (not render-gate) so there is no server/client hydration mismatch:
  // isAuthenticated() reads localStorage, which is client-only. This matches the
  // per-page pattern it replaces.
  useEffect(() => {
    if (!isAuthenticated()) router.replace("/login");
  }, [router]);

  // A boot dependency that failed is not the same as one still loading, and the
  // splash has to say which. Measured on the deployed app 2026-08-07: a 500 on
  // `/api/health/ready` held this splash forever with zero focusable elements and
  // zero live regions, so the entire UI was the string "Loading bioAF...".
  //
  // The installed-component check is deliberately absent from `bootFailed`: it
  // only decides which optional sections appear, so failing to read it is no
  // reason to withhold the product. useVisibleNavSections keeps those sections
  // visible instead.
  const booting = !backendReady || permissionsLoading || componentsLoading;
  const bootFailed = backendUnreachable || permissionsFailed;

  /** Retry from the splash. Safe: it covers the whole app, so nothing is discarded. */
  const retryBoot = () => {
    if (backendUnreachable) {
      retryNow();
      return;
    }
    // A cached load failed. Clear it and remount the tree from scratch, because
    // the hooks that already failed will not re-run their effects on their own.
    clearPermissionsCache();
    invalidateComponentCache();
    window.location.reload();
  };

  // One full-screen splash while the app boots, instead of a flash of empty shell.
  if (booting || bootFailed) {
    return (
      <BootSplash
        failed={bootFailed}
        message={
          backendUnreachable
            ? // Not "bioAF cannot be reached": the wordmark sits directly above
              // this line, so that spelling rendered as "bioAF / bioAF cannot be
              // reached" on the deployed page.
              "The server cannot be reached right now. This page keeps trying, and the technical detail is in the application logs."
            : permissionsFailed
              ? "Your permissions could not be loaded, so nothing is shown yet. The technical detail is in the application logs."
              : undefined
        }
        onRetry={retryBoot}
      />
    );
  }

  return (
    // ToastProvider wraps the shell so any page can surface a failure. It is the
    // app's only live region: before it, a failed mutation produced no message
    // and no announcement anywhere.
    <ToastProvider>
      <div className="flex h-screen">
        <Sidebar mobileOpen={navOpen} onMobileClose={() => setNavOpen(false)} />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header onOpenNav={() => setNavOpen(true)} navOpen={navOpen} />
          {children}
        </div>
      </div>
    </ToastProvider>
  );
}
