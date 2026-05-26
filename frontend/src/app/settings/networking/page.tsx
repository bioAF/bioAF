"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { NetworkingSettingsContent } from "@/components/settings/NetworkingSettingsContent";

export default function NetworkingSettingsPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <NetworkingSettingsContent />
        </main>
      </div>
    </div>
  );
}
