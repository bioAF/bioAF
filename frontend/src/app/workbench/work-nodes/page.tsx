"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";

export default function WorkNodesPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("notebooks", "view")) { router.push("/dashboard"); return; }
  }, [router, permLoading, canAccess]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Work Nodes</h1>
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
            <p className="text-gray-500">
              Custom ephemeral work nodes are coming soon (ADR-034).
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}
