"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { isAbortError } from "@/lib/abort";
import { logError } from "@/lib/errorReporting";
import { usePermissions } from "@/hooks/usePermissions";
import { useCapabilities } from "@/hooks/useCapabilities";
import { Card } from "@/components/ui/Card";

interface ConnectButtonProps {
  targetType: "pipeline_run" | "session";
  targetId: number;
  /** The page's record is a supported workload in an allowed state (running; or idle for sessions). */
  eligible: boolean;
  /** Called after the server refuses because the record's state changed, so the page re-reads it. */
  onStateRefused?: () => void;
  /** Render on the shared Card panel (for a page section) rather than inline (inside a dialog). */
  card?: boolean;
  /** Container spacing, applied only when the control is shown. */
  className?: string;
}

interface ConnectionResponse {
  command: string;
  setup_guide: string;
  warning: string;
  target_type: string;
  target_id: string;
  namespace: string | null;
  pod_name: string;
  container: string;
  context: string;
}

const STATE_REFUSAL =
  "This analysis is no longer running. Refresh its details before requesting new instructions.";
const NOT_STARTED = "This analysis has not started running in the cluster yet. Try again in a moment.";
const COPY_FAILURE = "We couldn't copy the command. Select it and copy it manually.";
const PROVIDER_OUTAGE = "bioAF could not check this analysis with the cluster right now. Try again in a moment.";
const PROVIDER_DENIED =
  "The cluster refused bioAF's check of this analysis. Ask an administrator to review bioAF's cluster access.";
const SETUP_INCOMPLETE =
  "Terminal access is not set up for this installation yet. Ask an administrator to finish cluster setup.";
const SIMULATED = "Terminal instructions are not available while bioAF runs in simulated compute mode.";
const UNIDENTIFIED =
  "bioAF cannot tell which workload this analysis is running in, so it cannot give terminal instructions. Ask an administrator for help.";
const UNKNOWN_FAILURE =
  "Terminal instructions could not be prepared. Try again in a moment. The technical detail is in the application logs.";

/** The exact target, for diagnosis, with a plain definition of each Kubernetes term. */
function targetDetails(c: ConnectionResponse): { label: string; value: string; definition: string }[] {
  const rows = [
    { label: "Workload", value: c.target_id, definition: "The Kubernetes Job or Pod bioAF started for this analysis." },
    { label: "Pod", value: c.pod_name, definition: "The running copy of that workload the command opens a shell in." },
    { label: "Container", value: c.container, definition: "The main program inside the pod (not a helper)." },
    { label: "Namespace", value: c.namespace ?? "", definition: "The section of the cluster this workload runs in." },
    {
      label: "Cluster context",
      value: c.context,
      definition: "The name kubectl uses for this cluster after the first-time setup.",
    },
  ];
  return rows.filter((row) => row.value);
}

/** A plain sentence for a refusal, and whether it means the record's state changed. */
function describeFailure(err: unknown): { message: string; stateChanged: boolean } {
  if (err instanceof ApiError) {
    const reason = typeof err.details?.reason === "string" ? err.details.reason : "";
    switch (err.code) {
      case "connection_unavailable":
        if (reason === "ambiguous_target") return { message: UNIDENTIFIED, stateChanged: false };
        // The workload exists but its main container is still being scheduled or created.
        if (reason === "container_starting") return { message: NOT_STARTED, stateChanged: false };
        return { message: STATE_REFUSAL, stateChanged: true };
      case "connection_provider_unavailable":
        return { message: reason === "provider_denied" ? PROVIDER_DENIED : PROVIDER_OUTAGE, stateChanged: false };
      case "connection_provider_timeout":
        return { message: PROVIDER_OUTAGE, stateChanged: false };
      case "connection_target_invalid":
        if (reason === "cluster_setup_incomplete") return { message: SETUP_INCOMPLETE, stateChanged: false };
        if (reason === "simulated_mode") return { message: SIMULATED, stateChanged: false };
        return { message: UNIDENTIFIED, stateChanged: false };
    }
  }
  logError("preparing terminal instructions", err);
  return { message: UNKNOWN_FAILURE, stateChanged: false };
}

/**
 * Terminal instructions for a running pipeline run or notebook session.
 *
 * Shown only when the user's effective permissions include notebooks:launch and the backend
 * supports ssh_exec; the API checks both again and is authoritative. Instructions belong to one
 * target: they are discarded, and any request in flight is abandoned, when the target changes or
 * stops being eligible.
 */
export function ConnectButton({
  targetType,
  targetId,
  eligible,
  onStateRefused,
  card = false,
  className,
}: ConnectButtonProps) {
  const { canAccess, loading: permissionsLoading, failed: permissionsFailed } = usePermissions();
  const { has, loading: capabilitiesLoading } = useCapabilities();
  const [loading, setLoading] = useState(false);
  const [connection, setConnection] = useState<ConnectionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const inFlight = useRef<AbortController | null>(null);

  const eligibilityLoading = permissionsLoading || capabilitiesLoading;
  const permitted = !permissionsFailed && canAccess("notebooks", "launch");
  const capable = has("ssh_exec");
  // While loading, the control renders inactive; once known, a denial hides it.
  const visible = eligible && (eligibilityLoading || (permitted && capable));
  const active = eligible && !eligibilityLoading && permitted && capable;
  const owner = `${targetType}:${targetId}:${active}`;

  useEffect(() => {
    // A new target, or losing eligibility, discards everything that belonged to the old one.
    setConnection(null);
    setError(null);
    setCopied(false);
    setCopyError(false);
    setShowGuide(false);
    setLoading(false);
    return () => {
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [owner]);

  if (!visible) return null;

  const handleShow = async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setLoading(true);
    setError(null);
    try {
      const endpoint =
        targetType === "pipeline_run" ? `/api/pipeline-runs/${targetId}/connect` : `/api/sessions/${targetId}/connect`;
      const data = await api.post<ConnectionResponse>(endpoint, undefined, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setConnection(data);
      setCopied(false);
      setCopyError(false);
    } catch (err) {
      if (controller.signal.aborted || isAbortError(err)) return;
      const { message, stateChanged } = describeFailure(err);
      setConnection(null);
      setError(message);
      if (stateChanged) onStateRefused?.();
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setLoading(false);
      }
    }
  };

  const handleCopy = async () => {
    if (!connection) return;
    setCopyError(false);
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(connection.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      logError("copying the terminal command", err);
      setCopied(false);
      setCopyError(true);
    }
  };

  const body = (
    <section className={`space-y-3 ${card ? "" : (className ?? "")}`} data-testid="connect-section">
      <h3 className="text-sm font-semibold text-gray-700">Terminal instructions</h3>

      {!connection && (
        <div data-testid="connect-button-wrapper">
          <button
            type="button"
            onClick={handleShow}
            disabled={!active || loading}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors ${
              !active || loading ? "bg-gray-100 text-gray-600 cursor-not-allowed" : "bg-gray-800 text-white hover:bg-gray-700"
            }`}
            data-testid="connect-button"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
              />
            </svg>
            {loading ? "Preparing instructions..." : "Show instructions"}
          </button>
          {error && (
            <p className="text-sm text-red-600 mt-1" role="alert" data-testid="connect-error">
              {error}
            </p>
          )}
        </div>
      )}

      {connection && (
        <div className="border rounded-lg p-4 bg-gray-50 space-y-3" data-testid="connect-expanded">
          <div
            className="bg-amber-50 border border-amber-200 rounded px-3 py-2 text-sm text-amber-800"
            data-testid="connect-warning"
          >
            {connection.warning}
          </div>


          <div className="flex items-start gap-2">
            <code
              className="flex-1 bg-gray-900 text-green-400 px-3 py-2 rounded font-mono text-sm whitespace-pre-wrap break-all select-all"
              data-testid="connect-command"
            >
              {connection.command}
            </code>
            <button
              type="button"
              onClick={handleCopy}
              className="px-3 py-2 bg-white border rounded text-sm hover:bg-gray-50"
              data-testid="copy-button"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          {copyError && (
            <p className="text-sm text-red-600" role="alert" data-testid="copy-error">
              {COPY_FAILURE}
            </p>
          )}

          <p className="text-sm text-gray-600" data-testid="connect-guidance">
            Showing or copying these instructions does not open a terminal. Run the command on your own computer;
            you need kubectl and your own access to the cluster (see the first-time setup). If the command later
            fails because the analysis has ended, refresh its details and request new instructions.
          </p>

          <div>
            <button
              type="button"
              onClick={() => setShowGuide(!showGuide)}
              className="text-sm text-bioaf-600 hover:underline"
              data-testid="setup-guide-toggle"
            >
              {showGuide ? "Hide" : "Show"} first-time setup guide
            </button>
            {showGuide && (
              <pre
                className="mt-2 bg-white border rounded p-3 text-sm text-gray-700 whitespace-pre-wrap"
                data-testid="setup-guide-content"
              >
                {connection.setup_guide}
              </pre>
            )}
          </div>

          <details className="text-sm">
            <summary className="cursor-pointer text-bioaf-600">Connection details</summary>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2" data-testid="connect-target">
              {targetDetails(connection).map(({ label, value, definition }) => (
                <div key={label} className="contents">
                  <dt className="text-gray-500">{label}</dt>
                  <dd>
                    <div className="font-mono break-all">{value}</div>
                    <p className="text-xs text-gray-500">{definition}</p>
                  </dd>
                </div>
              ))}
            </dl>
          </details>

          <button
            type="button"
            onClick={() => {
              setConnection(null);
              setCopied(false);
              setCopyError(false);
              setShowGuide(false);
            }}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            Collapse
          </button>
        </div>
      )}
    </section>
  );

  return card ? (
    <Card padding="sm" className={className}>
      {body}
    </Card>
  ) : (
    body
  );
}
