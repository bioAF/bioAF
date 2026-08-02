"use client";

import { useStackOptions } from "@/hooks/useStackOptions";
import { storageDisplay } from "@/lib/storageDisplay";

interface BootstrapCardProps {
  terraformInitialized: boolean;
  gcpCredentialsConfigured: boolean;
  onBootstrapStart: () => void;
}

export function BootstrapCard({
  terraformInitialized,
  gcpCredentialsConfigured,
  onBootstrapStart,
}: BootstrapCardProps) {
  // Provider-appropriate labels + settings route; defaults to GCP so a GCP
  // install renders unchanged.
  const { cloudProvider, kubernetesOption } = useStackOptions();
  const storageLabel = storageDisplay(kubernetesOption?.storage_backend).label;
  const cloudLabel = cloudProvider === "aws" ? "AWS" : "GCP";
  const settingsPath = cloudProvider === "aws" ? "/settings/aws" : "/settings/gcp";

  if (terraformInitialized) {
    return null;
  }

  return (
    <div
      data-testid="bootstrap-card"
      className="border border-blue-200 bg-blue-50 rounded-xl p-5 mb-6"
    >
      <h3 className="font-semibold text-blue-900 mb-1">Initialize Infrastructure</h3>
      <p className="text-sm text-blue-700 mb-4">
        Create the Terraform state bucket to enable infrastructure provisioning. This runs
        a one-time bootstrap that creates a {storageLabel} bucket for storing Terraform state.
      </p>

      {!gcpCredentialsConfigured && (
        <p className="text-sm text-amber-700 mb-3">
          {cloudLabel} credentials must be configured before initializing.{" "}
          <a
            data-testid="gcp-settings-link"
            href={settingsPath}
            className="underline font-medium"
          >
            Configure {cloudLabel} Settings
          </a>
        </p>
      )}

      <button
        data-testid="bootstrap-btn"
        disabled={!gcpCredentialsConfigured}
        onClick={onBootstrapStart}
        className="px-4 py-2 bg-bioaf-600 text-white rounded-lg text-sm font-medium
                   hover:bg-bioaf-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        Initialize Infrastructure
      </button>
    </div>
  );
}
