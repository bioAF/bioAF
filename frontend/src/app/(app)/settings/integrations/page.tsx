"use client";

import { useState, useEffect } from "react";
import { GcpSettingsContent } from "@/components/settings/GcpSettingsContent";
import { AwsSettingsContent } from "@/components/settings/AwsSettingsContent";
import { SmtpSettingsContent } from "@/components/settings/SmtpSettingsContent";
import { SlackSettingsContent } from "@/components/settings/SlackSettingsContent";
import { LlmSettingsContent } from "@/components/settings/LlmSettingsContent";
import { useStackOptions } from "@/hooks/useStackOptions";

type Tab = "gcp" | "aws" | "smtp" | "slack" | "seqera" | "llms";

// The cloud integration tab is provider-specific: a GCP install shows the GCP
// panel, an AWS install shows the AWS panel. The non-cloud tabs are identical on
// both. cloudProvider comes from the backend POLICY (useStackOptions); it falls
// safe to "gcp" pre-auth/on error, so a GCP install is byte-identical.
const NON_CLOUD_TABS: { key: Tab; label: string }[] = [
  { key: "smtp", label: "SMTP" },
  { key: "slack", label: "Slack" },
  { key: "seqera", label: "Seqera" },
  { key: "llms", label: "LLMs" },
];

export default function IntegrationsPage() {
  const { cloudProvider } = useStackOptions();
  const isAws = cloudProvider === "aws";
  const cloudTab: { key: Tab; label: string } = isAws
    ? { key: "aws", label: "AWS" }
    : { key: "gcp", label: "GCP" };
  const tabs = [cloudTab, ...NON_CLOUD_TABS];

  const [activeTab, setActiveTab] = useState<Tab>(cloudTab.key);

  // Keep the active tab valid when cloudProvider resolves after mount (the hook
  // starts on the GCP default, then may flip to "aws"): if we are still on the
  // cloud tab, follow it so an AWS install does not strand on a hidden "gcp" tab.
  useEffect(() => {
    setActiveTab((prev) => (prev === "gcp" || prev === "aws" ? cloudTab.key : prev));
  }, [cloudTab.key]);

  // Honor ?tab= so deep links (and the Slack OAuth return) open the right tab.
  useEffect(() => {
    const tab = new URLSearchParams(window.location.search).get("tab");
    if (tab && tabs.some((t) => t.key === tab)) {
      setActiveTab(tab as Tab);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloudTab.key]);

  return (
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
      {activeTab === "aws" && <AwsSettingsContent />}
      {activeTab === "smtp" && <SmtpSettingsContent />}
      {activeTab === "slack" && <SlackSettingsContent />}
      {activeTab === "seqera" && (
        <div className="bg-white rounded-lg shadow p-12 text-center max-w-2xl">
          <h2 className="text-lg font-semibold mb-2">Seqera Fusion</h2>
          <p className="text-gray-500">
            Support for Seqera Platform access tokens and Fusion file system licensing is coming soon.
          </p>
        </div>
      )}
      {activeTab === "llms" && <LlmSettingsContent />}
    </main>
  );
}
