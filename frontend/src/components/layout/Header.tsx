"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getCurrentUser, removeToken } from "@/lib/auth";
import { clearPermissionsCache } from "@/hooks/usePermissions";
import { clearCapabilitiesCache } from "@/hooks/useCapabilities";
import { api } from "@/lib/api";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { DeploymentBanner } from "@/components/infrastructure/DeploymentBanner";
import { GlobalSearch } from "@/components/layout/GlobalSearch";
import { QuickCreateMenu } from "@/components/layout/QuickCreateMenu";
import { AssistantLauncher } from "@/components/assistant/AssistantLauncher";
import { ThemeToggle } from "@/components/theme/ThemeToggle";

export function Header({
  onOpenNav,
  navOpen = false,
}: {
  /** Opens the off-canvas sidebar. Only reachable below `md`, where it is the
   *  only way to the navigation. */
  onOpenNav?: () => void;
  navOpen?: boolean;
} = {}) {
  const router = useRouter();
  const [user, setUser] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    const refresh = () => setUser(getCurrentUser());
    refresh();
    // The Account tab dispatches this after a name change so the header updates
    // without a full reload.
    window.addEventListener("profile-updated", refresh);
    return () => window.removeEventListener("profile-updated", refresh);
  }, []);

  const handleLogout = async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // Best effort -- token may already be expired
    }
    removeToken();
    clearPermissionsCache();
    clearCapabilitiesCache();
    router.push("/login");
  };

  return (
    <>
    <DeploymentBanner />
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between gap-4 px-6">
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        aria-expanded={navOpen}
        aria-controls="app-sidebar"
        className="md:hidden -ml-2 p-2 rounded-md text-gray-600 hover:bg-gray-100 hover:text-gray-900"
      >
        <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      <div className="flex-1 max-w-md">
        {user && <GlobalSearch />}
      </div>
      <div className="flex items-center gap-4">
        <ThemeToggle />
        {user && (
          <>
            <QuickCreateMenu />
            <AssistantLauncher />
            <NotificationBell />
            <Link
              href="/profile"
              className="text-sm text-gray-600 hover:text-bioaf-700 hover:underline"
              title="View your profile"
            >
              {(user.name as string) || (user.email as string) || "User"}
            </Link>
            <button
              onClick={handleLogout}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Logout
            </button>
          </>
        )}
      </div>
    </header>
    </>
  );
}
