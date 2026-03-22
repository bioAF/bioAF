"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { InfrastructureHealthWidget } from "@/components/dashboard/InfrastructureHealthWidget";
import { RunningJobsWidget } from "@/components/dashboard/RunningJobsWidget";
import { QueueDepthWidget } from "@/components/dashboard/QueueDepthWidget";
import { CostBudgetWidget } from "@/components/dashboard/CostBudgetWidget";
import { IngestStatusWidget } from "@/components/dashboard/IngestStatusWidget";
import { ActivityFeedWidget } from "@/components/dashboard/ActivityFeedWidget";
import { api } from "@/lib/api";

interface GCPConfig {
  gcp_credentials_configured: boolean;
}

export default function DashboardPage() {
  const router = useRouter();
  const [gcpConfigured, setGcpConfigured] = useState<boolean | null>(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    api.get<GCPConfig>("/api/v1/settings/gcp")
      .then((cfg) => setGcpConfigured(cfg.gcp_credentials_configured))
      .catch(() => setGcpConfigured(true)); // don't block dashboard on API error
  }, [router]);

  const showGcpBanner = gcpConfigured === false && !bannerDismissed;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 flex flex-col overflow-hidden p-6" data-testid="dashboard">
          <h1 className="text-2xl font-bold mb-4 shrink-0">Dashboard</h1>

          {showGcpBanner && (
            <div
              data-testid="gcp-setup-banner"
              className="mb-6 shrink-0 flex items-start justify-between gap-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-blue-800"
            >
              <div className="flex-1 text-sm">
                <span className="font-semibold">GCP not configured.</span>{" "}
                Set up your GCP credentials so bioAF can deploy infrastructure.{" "}
                <Link href="/settings/gcp" className="underline font-medium hover:text-blue-900">
                  Configure GCP settings
                </Link>
              </div>
              <button
                data-testid="gcp-banner-dismiss"
                onClick={() => setBannerDismissed(true)}
                className="shrink-0 text-blue-600 hover:text-blue-900 text-lg leading-none"
                aria-label="Dismiss GCP banner"
              >
                &times;
              </button>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4 shrink-0">
            <InfrastructureHealthWidget />
            <RunningJobsWidget />
            <QueueDepthWidget />
            <CostBudgetWidget />
            <IngestStatusWidget />
          </div>

          <div className="flex-1 min-h-0">
            <ActivityFeedWidget className="h-full" />
          </div>
        </main>
      </div>
    </div>
  );
}
