"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { SmtpSettingsContent } from "@/components/settings/SmtpSettingsContent";

export default function SettingsSmtpPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <SmtpSettingsContent />
        </main>
      </div>
    </div>
  );
}
