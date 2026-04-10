"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { GcpSettingsContent } from "@/components/settings/GcpSettingsContent";

export default function GcpSettingsPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <GcpSettingsContent />
        </main>
      </div>
    </div>
  );
}
