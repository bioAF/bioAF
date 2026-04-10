"use client";

import { useState } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { GcpSettingsContent } from "@/components/settings/GcpSettingsContent";
import { SmtpSettingsContent } from "@/components/settings/SmtpSettingsContent";
import { SlackSettingsContent } from "@/components/settings/SlackSettingsContent";

type Tab = "gcp" | "smtp" | "slack" | "seqera";

const tabs: { key: Tab; label: string }[] = [
  { key: "gcp", label: "GCP" },
  { key: "smtp", label: "SMTP" },
  { key: "slack", label: "Slack" },
  { key: "seqera", label: "Seqera" },
];

export default function IntegrationsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("gcp");

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Integrations</h1>

          <div className="border-b border-gray-200 mb-6">
            <nav className="flex -mb-px space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
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

          {activeTab === "gcp" && <GcpSettingsContent />}
          {activeTab === "smtp" && <SmtpSettingsContent />}
          {activeTab === "slack" && <SlackSettingsContent />}
          {activeTab === "seqera" && (
            <div className="bg-white rounded-lg shadow p-12 text-center max-w-2xl">
              <h2 className="text-lg font-semibold mb-2">Seqera Fusion</h2>
              <p className="text-gray-400">
                Support for Seqera Platform access tokens and Fusion file system licensing is coming soon.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
