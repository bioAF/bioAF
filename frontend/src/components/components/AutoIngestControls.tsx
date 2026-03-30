"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AutoIngestStatus {
  enabled: boolean;
  cleanup_policy: string;
  default_delay_minutes: number;
  listener_running: boolean;
  pubsub_topic: string | null;
  pubsub_subscription: string | null;
  messages_processed_24h: number;
  messages_failed_24h: number;
}

interface AutoIngestControlsProps {
  storageDeployed: boolean;
  pubsubConfigured: boolean;
  onUpdateStorage?: () => void;
}

export function AutoIngestControls({
  storageDeployed,
  pubsubConfigured,
  onUpdateStorage,
}: AutoIngestControlsProps) {
  const [status, setStatus] = useState<AutoIngestStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState("");

  useEffect(() => {
    if (!storageDeployed || !pubsubConfigured) return;
    api
      .get<AutoIngestStatus>("/api/v1/settings/auto-ingest")
      .then(setStatus)
      .catch(() => {});
  }, [storageDeployed, pubsubConfigured]);

  if (!storageDeployed) return null;

  const handleUpdateStorage = async () => {
    setUpdating(true);
    setUpdateError("");
    try {
      await api.post("/api/v1/infrastructure/storage/update");
      // Trigger parent refresh so pubsubConfigured updates
      if (onUpdateStorage) onUpdateStorage();
    } catch (err) {
      setUpdateError(
        err instanceof Error ? err.message : "Storage update failed",
      );
    } finally {
      setUpdating(false);
    }
  };

  if (!pubsubConfigured) {
    return (
      <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded text-sm">
        <p className="text-amber-800 font-medium text-xs">
          Auto-ingest requires Pub/Sub notification infrastructure. This runs a
          Terraform apply to add the notification resources to your existing
          storage deployment.
        </p>
        {updateError && (
          <p className="text-xs text-red-600 mt-1">{updateError}</p>
        )}
        <button
          onClick={handleUpdateStorage}
          disabled={updating}
          className="mt-2 px-3 py-1.5 bg-amber-600 text-white rounded text-xs hover:bg-amber-700 disabled:opacity-50"
        >
          {updating ? "Updating infrastructure..." : "Update Storage Infrastructure"}
        </button>
      </div>
    );
  }

  const handleToggle = async () => {
    const newEnabled = !status?.enabled;
    setLoading(true);
    try {
      await api.post("/api/v1/settings/auto-ingest", {
        enabled: newEnabled,
        cleanup_policy: status?.cleanup_policy || "delete_after_copy",
      });
      const updated = await api.get<AutoIngestStatus>(
        "/api/v1/settings/auto-ingest",
      );
      setStatus(updated);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const handleDelayChange = async (minutes: number) => {
    setLoading(true);
    try {
      await api.post("/api/v1/settings/auto-ingest", {
        enabled: status?.enabled ?? false,
        cleanup_policy: status?.cleanup_policy || "delete_after_copy",
        default_delay_minutes: minutes,
      });
      const updated = await api.get<AutoIngestStatus>(
        "/api/v1/settings/auto-ingest",
      );
      setStatus(updated);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const handlePolicyChange = async (policy: string) => {
    setLoading(true);
    try {
      await api.post("/api/v1/settings/auto-ingest", {
        enabled: status?.enabled ?? false,
        cleanup_policy: policy,
      });
      const updated = await api.get<AutoIngestStatus>(
        "/api/v1/settings/auto-ingest",
      );
      setStatus(updated);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const dotColor = status?.enabled
    ? status.listener_running
      ? "bg-green-500"
      : "bg-amber-500"
    : "bg-gray-400";

  const statusText = status?.enabled
    ? status.listener_running
      ? "Active"
      : "Enabled but not running"
    : "Disabled";

  return (
    <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded text-sm space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-700 uppercase tracking-wide">
            Auto-Ingest
          </span>
          <span className="text-xs font-medium bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full">Beta</span>
          <span
            data-testid="status-dot"
            className={`inline-block w-2 h-2 rounded-full ${dotColor}`}
          />
          <span className="text-xs text-gray-500">{statusText}</span>
        </div>
        <button
          data-testid="auto-ingest-toggle"
          aria-label="toggle"
          onClick={handleToggle}
          disabled={loading}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            status?.enabled ? "bg-bioaf-600" : "bg-gray-300"
          } ${loading ? "opacity-50" : ""}`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
              status?.enabled ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </button>
      </div>

      {status?.enabled && (
        <>
          <div>
            <label
              htmlFor="cleanup-policy"
              className="text-xs text-gray-600 block mb-1"
            >
              Cleanup policy
            </label>
            <select
              id="cleanup-policy"
              data-testid="cleanup-policy-select"
              value={status.cleanup_policy}
              onChange={(e) => handlePolicyChange(e.target.value)}
              disabled={loading}
              className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
            >
              <option value="delete_after_copy">
                Delete after cataloging
              </option>
              <option value="retain_7d">Retain for 7 days</option>
              <option value="retain_30d">Retain for 30 days</option>
            </select>
          </div>

          <div>
            <label
              htmlFor="delay-minutes"
              className="text-xs text-gray-600 block mb-1"
            >
              Pipeline delay after last file upload
            </label>
            <div className="flex items-center gap-2">
              <input
                id="delay-minutes"
                type="number"
                min="0"
                max="1440"
                value={status.default_delay_minutes}
                onChange={(e) => {
                  const val = parseInt(e.target.value, 10);
                  if (!isNaN(val) && val >= 0) handleDelayChange(val);
                }}
                disabled={loading}
                className="text-xs border border-gray-300 rounded px-2 py-1 w-20"
              />
              <span className="text-xs text-gray-500">minutes</span>
            </div>
            <p className="text-xs text-gray-400 mt-0.5">
              How long to wait after the last file arrives for an experiment
              before triggering pipelines. Increase if pipelines run with
              incomplete datasets.
            </p>
          </div>

          <div className="flex gap-4 text-xs text-gray-500">
            <span>
              Files ingested (24h):{" "}
              <span className="font-medium text-gray-700">
                {status.messages_processed_24h}
              </span>
            </span>
            <span>
              Failed (24h):{" "}
              <span className="font-medium text-red-600">
                {status.messages_failed_24h}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
