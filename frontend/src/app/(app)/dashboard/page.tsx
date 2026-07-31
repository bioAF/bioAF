"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePermissions } from "@/hooks/usePermissions";
import { useStackOptions } from "@/hooks/useStackOptions";
import { DashboardContent } from "@/components/dashboard/DashboardContent";
import { api } from "@/lib/api";

interface CloudConfig {
  // The GCP endpoint returns gcp_credentials_configured; the AWS endpoint
  // returns aws_credentials_configured. Read both optionally so one fetch path
  // serves either cloud.
  gcp_credentials_configured?: boolean;
  aws_credentials_configured?: boolean;
}

export default function DashboardPage() {
  const { roleName } = usePermissions();
  const { cloudProvider } = useStackOptions();
  const isAws = cloudProvider === "aws";
  const [cloudConfigured, setCloudConfigured] = useState<boolean | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    // The onboarding nudge is provider-specific: a GCP install checks its GCP
    // credentials, an AWS install checks its AWS credentials. cloudProvider falls
    // safe to "gcp" pre-auth/on error, so the GCP path is unchanged.
    const endpoint = isAws ? "/api/v1/settings/aws" : "/api/v1/settings/gcp";
    api
      .get<CloudConfig>(endpoint)
      .then((cfg) =>
        setCloudConfigured(
          isAws ? !!cfg.aws_credentials_configured : !!cfg.gcp_credentials_configured
        )
      )
      .catch(() => setCloudConfigured(true)); // don't block dashboard on API error
  }, [isAws]);

  const cloudLabel = isAws ? "AWS" : "GCP";
  const settingsPath = isAws ? "/settings/aws" : "/settings/gcp";
  const showCloudBanner = cloudConfigured === false && !bannerDismissed && roleName === "admin";

  return (
    <main className="flex-1 flex flex-col overflow-y-auto p-6" data-testid="dashboard">
          {showCloudBanner && (
            <div
              data-testid="cloud-setup-banner"
              className="mb-6 shrink-0 flex items-start justify-between gap-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-blue-800"
            >
              <div className="flex-1 text-sm">
                <span className="font-semibold">{cloudLabel} not configured.</span>{" "}
                Set up your {cloudLabel} credentials so bioAF can deploy infrastructure.{" "}
                <Link href={settingsPath} className="underline font-medium hover:text-blue-900">
                  Configure {cloudLabel} settings
                </Link>
              </div>
              <button
                data-testid="cloud-banner-dismiss"
                onClick={() => setBannerDismissed(true)}
                className="shrink-0 text-blue-600 hover:text-blue-900 text-lg leading-none"
                aria-label={`Dismiss ${cloudLabel} banner`}
              >
                &times;
              </button>
            </div>
          )}

          <DashboardContent />
        </main>
  );
}
