"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";

interface ConnectButtonProps {
  targetType: "pipeline_run" | "session";
  targetId: number;
  disabled?: boolean;
}

interface ConnectionResponse {
  command: string;
  setup_guide: string;
  warning: string;
  target_type: string;
  target_id: string;
  namespace: string | null;
}

export function ConnectButton({ targetType, targetId, disabled = false }: ConnectButtonProps) {
  const user = getCurrentUser();
  const role = user?.role_name as string;
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [connection, setConnection] = useState<ConnectionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showGuide, setShowGuide] = useState(false);

  // Only render for comp_bio and admin roles
  if (role !== "admin" && role !== "comp_bio") {
    return null;
  }

  const handleConnect = async () => {
    setLoading(true);
    setError(null);
    try {
      const endpoint =
        targetType === "pipeline_run"
          ? `/api/pipeline-runs/${targetId}/connect`
          : `/api/sessions/${targetId}/connect`;
      const data = await api.post<ConnectionResponse>(endpoint);
      setConnection(data);
      setExpanded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to get connection command");
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    if (connection) {
      await navigator.clipboard.writeText(connection.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!expanded) {
    return (
      <div data-testid="connect-button-wrapper">
        <button
          onClick={handleConnect}
          disabled={disabled || loading}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors ${
            disabled
              ? "bg-gray-100 text-gray-400 cursor-not-allowed"
              : "bg-gray-800 text-white hover:bg-gray-700"
          }`}
          title={disabled ? "Target is not running" : "Open terminal connection"}
          data-testid="connect-button"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          {loading ? "Connecting..." : "Connect"}
        </button>
        {error && (
          <p className="text-sm text-red-600 mt-1" data-testid="connect-error">{error}</p>
        )}
      </div>
    );
  }

  return (
    <div className="border rounded-lg p-4 bg-gray-50 space-y-3" data-testid="connect-expanded">
      {/* Warning */}
      <div className="bg-amber-50 border border-amber-200 rounded px-3 py-2 text-sm text-amber-800" data-testid="connect-warning">
        {connection?.warning}
      </div>

      {/* Command */}
      <div className="flex items-center gap-2">
        <code className="flex-1 bg-gray-900 text-green-400 px-3 py-2 rounded font-mono text-sm overflow-x-auto" data-testid="connect-command">
          {connection?.command}
        </code>
        <button
          onClick={handleCopy}
          className="px-3 py-2 bg-white border rounded text-sm hover:bg-gray-50"
          data-testid="copy-button"
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>

      {/* Setup guide */}
      <div>
        <button
          onClick={() => setShowGuide(!showGuide)}
          className="text-sm text-bioaf-600 hover:underline"
          data-testid="setup-guide-toggle"
        >
          {showGuide ? "Hide" : "Show"} first-time setup guide
        </button>
        {showGuide && (
          <pre className="mt-2 bg-white border rounded p-3 text-sm text-gray-700 whitespace-pre-wrap" data-testid="setup-guide-content">
            {connection?.setup_guide}
          </pre>
        )}
      </div>

      <button
        onClick={() => { setExpanded(false); setConnection(null); }}
        className="text-xs text-gray-500 hover:text-gray-700"
      >
        Collapse
      </button>
    </div>
  );
}
