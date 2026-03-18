"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface BillingSetupModalProps {
  onComplete: () => void;
  onClose: () => void;
  datasetExists?: boolean;
  consoleUrl?: string;
}

interface BillingExportStatusResponse {
  configured: boolean;
  dataset_id: string;
  console_url: string;
  table_id: string;
}

interface BillingExportEnableResponse {
  status: string;
  message: string;
}

interface BillingExportVerifyResponse {
  configured: boolean;
  table_id: string;
  message: string;
}

type Step = "intro" | "creating" | "instructions" | "verifying" | "verified" | "not_yet" | "error";

export function BillingSetupModal({ onComplete, onClose, datasetExists, consoleUrl: initialConsoleUrl }: BillingSetupModalProps) {
  const [step, setStep] = useState<Step>(datasetExists ? "instructions" : "intro");
  const [consoleUrl, setConsoleUrl] = useState(initialConsoleUrl || "");
  const [errorMessage, setErrorMessage] = useState("");
  const [verifyMessage, setVerifyMessage] = useState("");

  const handleEnable = async () => {
    setStep("creating");
    setErrorMessage("");
    try {
      // First get the status to have the console URL
      const status = await api.get<BillingExportStatusResponse>(
        "/api/v1/infrastructure/billing-export/status",
      );
      setConsoleUrl(status.console_url);

      // Kick off Terraform to create the BQ dataset
      const result = await api.post<BillingExportEnableResponse>(
        "/api/v1/infrastructure/billing-export/enable",
      );
      if (result.status === "completed") {
        setStep("instructions");
      } else {
        setStep("error");
        setErrorMessage(result.message || "Failed to create BigQuery dataset");
      }
    } catch (err) {
      setStep("error");
      setErrorMessage(err instanceof Error ? err.message : "An error occurred");
    }
  };

  const handleVerify = async () => {
    setStep("verifying");
    setVerifyMessage("");
    try {
      const result = await api.post<BillingExportVerifyResponse>(
        "/api/v1/infrastructure/billing-export/verify",
      );
      if (result.configured) {
        setStep("verified");
      } else {
        setStep("not_yet");
        setVerifyMessage(result.message);
      }
    } catch (err) {
      setStep("error");
      setErrorMessage(err instanceof Error ? err.message : "Verification failed");
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 p-6">
        <h2 className="text-lg font-semibold mb-4">Set Up Billing Export</h2>

        {/* Intro */}
        {step === "intro" && (
          <div>
            <p className="text-sm text-gray-600 mb-4">
              Connect your GCP billing data to get accurate, invoice-matched cost
              tracking. This creates a BigQuery dataset in your project, then
              guides you through enabling billing export in the Google Cloud Console.
            </p>
            <p className="text-sm text-gray-500 mb-4">
              After enabling, cost data typically appears within 24 hours.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
              >
                Cancel
              </button>
              <button
                onClick={handleEnable}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-lg text-sm font-medium hover:bg-bioaf-700"
              >
                Get Started
              </button>
            </div>
          </div>
        )}

        {/* Creating dataset */}
        {step === "creating" && (
          <div>
            <p className="text-sm text-gray-600 mb-4 flex items-center gap-2">
              <span className="inline-block h-4 w-4 border-2 border-bioaf-600 border-t-transparent rounded-full animate-spin" />
              Creating BigQuery dataset and configuring permissions...
            </p>
          </div>
        )}

        {/* Console instructions */}
        {step === "instructions" && (
          <div>
            <p className="text-sm text-gray-600 mb-3">
              BigQuery dataset created. Now enable billing export in the Google Cloud Console:
            </p>
            <ol className="text-sm text-gray-700 space-y-2 mb-4 list-decimal list-inside">
              <li>
                Open the{" "}
                <a
                  href={consoleUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-bioaf-600 hover:underline font-medium"
                >
                  Billing Export page
                </a>
              </li>
              <li>Under &ldquo;Detailed usage cost&rdquo;, click <strong>Edit settings</strong></li>
              <li>Select your project and choose the <code className="bg-gray-100 px-1 rounded text-xs">billing_export</code> dataset</li>
              <li>Click <strong>Save</strong></li>
            </ol>
            <p className="text-xs text-gray-400 mb-4">
              Data may take up to 24 hours to appear after enabling.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
              >
                I&apos;ll do this later
              </button>
              <button
                onClick={handleVerify}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-lg text-sm font-medium hover:bg-bioaf-700"
              >
                Verify
              </button>
            </div>
          </div>
        )}

        {/* Verifying */}
        {step === "verifying" && (
          <div>
            <p className="text-sm text-gray-600 mb-4 flex items-center gap-2">
              <span className="inline-block h-4 w-4 border-2 border-bioaf-600 border-t-transparent rounded-full animate-spin" />
              Checking for billing export data...
            </p>
          </div>
        )}

        {/* Verified */}
        {step === "verified" && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-green-100 text-green-600">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </span>
              <span className="text-sm font-medium text-green-700">Billing export verified</span>
            </div>
            <p className="text-sm text-gray-600 mb-4">
              Cost data is now flowing from BigQuery. Your cost center will
              display accurate, invoice-matched billing data.
            </p>
            <div className="flex justify-end">
              <button
                onClick={() => { onComplete(); onClose(); }}
                className="px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
              >
                Done
              </button>
            </div>
          </div>
        )}

        {/* Not yet */}
        {step === "not_yet" && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="inline-flex items-center justify-center h-6 w-6 rounded-full bg-amber-100 text-amber-600">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01" />
                </svg>
              </span>
              <span className="text-sm font-medium text-amber-700">Not ready yet</span>
            </div>
            <p className="text-sm text-gray-600 mb-4">
              {verifyMessage || "Billing export table not found. Data may take up to 24 hours to appear after enabling export."}
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
              >
                Close
              </button>
              <button
                onClick={handleVerify}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-lg text-sm font-medium hover:bg-bioaf-700"
              >
                Check Again
              </button>
            </div>
          </div>
        )}

        {/* Error */}
        {step === "error" && (
          <div>
            <p className="text-sm text-red-600 mb-4">{errorMessage}</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
              >
                Close
              </button>
              <button
                onClick={handleEnable}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-lg text-sm font-medium hover:bg-bioaf-700"
              >
                Retry
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
