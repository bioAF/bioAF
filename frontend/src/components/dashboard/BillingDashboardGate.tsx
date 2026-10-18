"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Modal } from "@/components/shared/Modal";
import { Card } from "@/components/ui/Card";

interface BillingExportStatus {
  configured: boolean;
  dataset_id: string;
}

/**
 * Hold a cost widget back until the billing dashboard is set up.
 *
 * Without BigQuery billing export, cost data is an adapter estimate. On the
 * dashboard that estimate read as real spend, and users reported the gap
 * against their invoice as a bug. So the widget shows no number at all until
 * the export is verified: "Please enable the billing dashboard" before setup,
 * "Pending" once the dataset exists but the export has not been verified.
 *
 * Pending never resolves by itself. Creating the dataset does not enable the
 * export in Google Cloud, and `configured` turns true only when Check Status in
 * the Cost Center finds the export table. So Pending always links there.
 *
 * The children mount only once configured, so their cost request is never
 * made against estimated data.
 */
export function BillingDashboardGate({
  title,
  testId,
  children,
}: {
  title: string;
  testId: string;
  children: ReactNode;
}) {
  const { data, loading, error, retry } = useWidgetData(
    () => api.get<BillingExportStatus>("/api/v1/infrastructure/billing-export/status"),
    "Billing dashboard status",
  );
  const [explainOpen, setExplainOpen] = useState(false);

  if (!loading && !error && data?.configured) return <>{children}</>;

  return (
    <div data-testid={testId}>
      <Card padding="none" className="p-5">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          {title}
        </h3>
        {loading && (
          <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
            <LoadingSpinner size="sm" />
            <span className="text-sm">Loading costs...</span>
          </div>
        )}
        {error && !loading && (
          <div className="text-sm text-red-600" data-testid="widget-error">
            {error}
            <button onClick={retry} className="ml-2 text-bioaf-600 hover:underline">
              Retry
            </button>
          </div>
        )}
        {!loading && !error && data && !data.dataset_id && (
          <Link
            href="/infrastructure/cost-center"
            className="text-sm text-bioaf-600 hover:underline"
            data-testid="billing-dashboard-not-enabled"
          >
            Please enable the billing dashboard
          </Link>
        )}
        {!loading && !error && data && data.dataset_id && (
          <div className="flex items-center gap-1.5 text-sm text-gray-600" data-testid="billing-dashboard-pending">
            <span>Pending</span>
            <button
              type="button"
              onClick={() => setExplainOpen(true)}
              aria-label="Why is this pending?"
              className="text-gray-500 hover:text-gray-700"
            >
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path
                  fillRule="evenodd"
                  d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
            <Link
              href="/infrastructure/cost-center"
              className="ml-auto text-xs text-bioaf-600 hover:underline"
            >
              Finish setup in Cost Center
            </Link>
          </div>
        )}
        <Modal
          open={explainOpen}
          title="Billing dashboard setup is pending"
          size="sm"
          onClose={() => setExplainOpen(false)}
        >
          <div className="space-y-2 text-sm text-gray-600">
            <p>
              The billing dataset has been created, but costs only appear here once setup is
              finished. Make sure you have enabled billing export to that dataset in the Google
              Cloud Console, then click Check Status in the Cost Center.
            </p>
            <p>
              Google Cloud can take up to 24 hours to deliver the first billing data. If Check
              Status finds none yet, try again later.
            </p>
            <Link
              href="/infrastructure/cost-center"
              className="inline-block text-bioaf-600 hover:underline"
            >
              Open Cost Center
            </Link>
          </div>
        </Modal>
      </Card>
    </div>
  );
}
