"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Modal } from "@/components/shared/Modal";

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
 * "Pending" once the dataset exists but no data has arrived yet.
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
    <div className="bg-white rounded-lg shadow p-5" data-testid={testId}>
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
            className="text-gray-400 hover:text-gray-600"
          >
            <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>
      )}
      <Modal
        open={explainOpen}
        title="Billing data is on its way"
        size="sm"
        onClose={() => setExplainOpen(false)}
      >
        <p className="text-sm text-gray-600">
          The billing dashboard is set up. Google Cloud can take up to 24 hours to deliver the
          first billing data, and costs will appear here once it arrives.
        </p>
      </Modal>
    </div>
  );
}
