"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { useBackendReady } from "@/hooks/useBackendReady";
import { usePermissions } from "@/hooks/usePermissions";
import { useComponents } from "@/hooks/useComponents";
import { ToastProvider } from "@/components/shared/Toast";

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
  const { ready: backendReady } = useBackendReady();
  const { loading: permissionsLoading } = usePermissions();
  const { loading: componentsLoading } = useComponents();

  // Redirect (not render-gate) so there is no server/client hydration mismatch:
  // isAuthenticated() reads localStorage, which is client-only. This matches the
  // per-page pattern it replaces.
  useEffect(() => {
    if (!isAuthenticated()) router.replace("/login");
  }, [router]);

  // One full-screen splash while the app boots, instead of a flash of empty shell.
  if (!backendReady || permissionsLoading || componentsLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900" data-testid="app-loading">
        <div className="text-center">
          <div className="text-3xl font-bold text-bioaf-400 mb-4">bioAF</div>
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-bioaf-400 border-t-transparent" />
          <p className="mt-3 text-sm text-gray-400">Loading bioAF...</p>
        </div>
      </div>
    );
  }

  return (
    // ToastProvider wraps the shell so any page can surface a failure. It is the
    // app's only live region: before it, a failed mutation produced no message
    // and no announcement anywhere.
    <ToastProvider>
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header />
          {children}
        </div>
      </div>
    </ToastProvider>
  );
}
