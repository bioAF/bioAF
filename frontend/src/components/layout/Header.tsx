"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, removeToken } from "@/lib/auth";
import { clearPermissionsCache } from "@/hooks/usePermissions";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { DeploymentBanner } from "@/components/infrastructure/DeploymentBanner";

export function Header() {
  const router = useRouter();
  const [user, setUser] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    setUser(getCurrentUser());
  }, []);

  const handleLogout = () => {
    removeToken();
    clearPermissionsCache();
    router.push("/login");
  };

  return (
    <>
    <DeploymentBanner />
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6">
      <div />
      <div className="flex items-center gap-4">
        {user && (
          <>
            <NotificationBell />
            <span className="text-sm text-gray-600">
              {(user.email as string) || "User"}
            </span>
            <span className="text-xs bg-bioaf-100 text-bioaf-700 px-2 py-1 rounded">
              {user.role_name as string}
            </span>
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
