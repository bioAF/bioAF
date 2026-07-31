"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { AccountTab } from "./components/AccountTab";
import { SessionCredentialsTab } from "./components/SessionCredentialsTab";
import { SSHKeyTab } from "./components/SSHKeyTab";
import { NotificationsTab } from "./components/NotificationsTab";

type TabKey = "account" | "session" | "ssh" | "notifications";

export default function ProfilePage() {
  const router = useRouter();
  const { canAccess, loading: permsLoading } = usePermissions();
  const [activeTab, setActiveTab] = useState<TabKey>("account");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
    }
  }, [router]);

  const canSeeNotifications = canAccess("notifications", "view");

  const tabs: { key: TabKey; label: string }[] = [
    { key: "account", label: "Account" },
    { key: "session", label: "Session Credentials" },
    { key: "ssh", label: "Git SSH Key" },
    ...(canSeeNotifications
      ? [{ key: "notifications" as TabKey, label: "Notifications" }]
      : []),
  ];

  // Honor ?tab= deep-links once permissions are known (so a gated tab is not
  // selected for a user who cannot see it).
  useEffect(() => {
    if (permsLoading) return;
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested && tabs.some((t) => t.key === requested)) {
      setActiveTab(requested as TabKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permsLoading, canSeeNotifications]);

  const selectTab = (key: TabKey) => {
    setActiveTab(key);
    const url = key === "account" ? "/profile" : `/profile?tab=${key}`;
    window.history.replaceState(null, "", url);
  };

  return (
    <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Profile</h1>

          <div className="border-b border-gray-200 mb-6">
            <nav className="flex -mb-px space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => selectTab(tab.key)}
                  className={`py-2 px-1 border-b-2 text-sm font-medium ${
                    activeTab === tab.key
                      ? "border-bioaf-500 text-bioaf-600"
                      : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {activeTab === "account" && <AccountTab />}
          {activeTab === "session" && <SessionCredentialsTab />}
          {activeTab === "ssh" && <SSHKeyTab />}
          {activeTab === "notifications" && canSeeNotifications && <NotificationsTab />}
        </main>
  );
}
