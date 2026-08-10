"use client";

import { useStackOptions } from "@/hooks/useStackOptions";
import { storageDisplay } from "@/lib/storageDisplay";
import { Button } from "@/components/ui/Button";

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

      <Button
        data-testid="bootstrap-btn"
        disabled={!gcpCredentialsConfigured}
        onClick={onBootstrapStart}>
        Initialize Infrastructure
      </Button>
    </div>
  );
}
