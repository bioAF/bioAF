"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ReaderSAStatus, ReaderSACreateResponse } from "@/lib/types";

export function SheetsReaderSACard() {
  const [status, setStatus] = useState<ReaderSAStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [copied, setCopied] = useState(false);
  const [showInfo, setShowInfo] = useState(false);

  async function loadStatus() {
    try {
      const data = await api.get<ReaderSAStatus>("/api/v1/sheets/reader-sa");
      setStatus(data);
    } catch {
      // Non-fatal -- admin may not have infra view permission
    }
  }

  useEffect(() => {
    loadStatus();
  }, []);

  async function handleCreate() {
    setLoading(true);
    setError("");
    try {
      const data = await api.post<ReaderSACreateResponse>("/api/v1/sheets/reader-sa", {});
      setStatus({ exists: true, email: data.email });
      if (data.warning) setWarning(data.warning);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create reader service account");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Delete the Google Sheets reader service account? Users will no longer be able to import columns from Google Sheets.")) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      await api.delete("/api/v1/sheets/reader-sa");
      setStatus({ exists: false, email: null });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete reader service account");
    } finally {
      setLoading(false);
    }
  }

  function copyEmail() {
    if (status?.email) {
      navigator.clipboard.writeText(status.email);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  if (status === null) return null;

  return (
    <div className="border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">Google Sheets Reader</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            A dedicated reader account for importing column headers from shared Google Sheets.
          </p>
        </div>
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
            status.exists
              ? "bg-green-100 text-green-800"
              : "bg-gray-100 text-gray-600"
          }`}
        >
          {status.exists ? "Active" : "Not created"}
        </span>
      </div>

      {status.exists && status.email && (
        <div className="bg-blue-50 border border-blue-200 rounded-md p-3">
          <p className="text-xs text-blue-800 mb-1.5">
            Share your Google Sheet with this email to allow bioAF to read column headers:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-white border border-blue-200 rounded px-2 py-1 font-mono break-all">
              {status.email}
            </code>
            <button
              onClick={copyEmail}
              className="text-xs text-blue-600 hover:text-blue-800 whitespace-nowrap"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-2">
          <p className="text-xs text-red-700">{error}</p>
        </div>
      )}

      {warning && (
        <div className="bg-amber-50 border border-amber-200 rounded-md p-2">
          <p className="text-xs text-amber-700">{warning}</p>
        </div>
      )}

      <div className="flex items-center gap-2">
        {!status.exists ? (
          <button
            onClick={handleCreate}
            disabled={loading}
            className="text-sm px-3 py-1.5 bg-bioaf-600 text-white rounded-md hover:bg-bioaf-700 disabled:opacity-50"
          >
            {loading ? "Creating..." : "Create Reader Account"}
          </button>
        ) : (
          <button
            onClick={handleDelete}
            disabled={loading}
            className="text-sm px-3 py-1.5 border border-red-300 text-red-600 rounded-md hover:bg-red-50 disabled:opacity-50"
          >
            {loading ? "Deleting..." : "Delete Reader Account"}
          </button>
        )}
        <button
          onClick={() => setShowInfo(!showInfo)}
          className="text-gray-400 hover:text-gray-600 w-5 h-5 flex items-center justify-center rounded-full border border-gray-300 text-xs font-medium"
          title="What is a reader account?"
        >
          ?
        </button>
      </div>

      {showInfo && (
        <div className="bg-gray-50 border border-gray-200 rounded-md p-3 text-xs text-gray-600">
          <p>
            A reader account lets bioAF securely access your Google Sheets without creating a
            new Google Workspace user. You share your spreadsheet with this account the same way
            you would share with a colleague. It can only read data, never edit or delete.
          </p>
        </div>
      )}
    </div>
  );
}
