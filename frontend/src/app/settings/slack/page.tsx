"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { SlackSettingsContent } from "@/components/settings/SlackSettingsContent";

export default function SettingsSlackPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <SlackSettingsContent />
        </main>
      </div>
    </div>
  );
}
