"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface AutoIngestStatus {
  enabled: boolean;
  cleanup_policy: string;
  default_delay_minutes: number;
  manifest_filename: string;
  manifest_format: string;
  manifest_retry_interval_minutes: number;
  manifest_max_retries: number;
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
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [showCleanupInfo, setShowCleanupInfo] = useState(false);

  // Local form state (decoupled from server state)
  const [form, setForm] = useState({
    cleanup_policy: "delete_after_copy",
    manifest_filename: "md5.txt",
    manifest_format: "txt",
    manifest_retry_interval_minutes: 15,
    manifest_max_retries: 48,
  });
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!storageDeployed || !pubsubConfigured) return;
    api
      .get<AutoIngestStatus>("/api/v1/settings/auto-ingest")
      .then((data) => {
        setStatus(data);
        setForm({
          cleanup_policy: data.cleanup_policy,
          manifest_filename: data.manifest_filename,
          manifest_format: data.manifest_format,
          manifest_retry_interval_minutes: data.manifest_retry_interval_minutes,
          manifest_max_retries: data.manifest_max_retries,
        });
      })
      .catch(() => {});
  }, [storageDeployed, pubsubConfigured]);

  if (!storageDeployed) return null;

  const handleUpdateStorage = async () => {
    setUpdating(true);
    setUpdateError("");
    try {
      await api.post("/api/v1/infrastructure/storage/update");
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
        cleanup_policy: form.cleanup_policy,
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

  const updateForm = (updates: Partial<typeof form>) => {
    setForm((prev) => ({ ...prev, ...updates }));
    setDirty(true);
    setSaveSuccess(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveSuccess(false);
    try {
      await api.post("/api/v1/settings/auto-ingest", {
        enabled: status?.enabled ?? false,
        cleanup_policy: form.cleanup_policy,
        manifest_filename: form.manifest_filename,
        manifest_format: form.manifest_format,
        manifest_retry_interval_minutes: form.manifest_retry_interval_minutes,
        manifest_max_retries: form.manifest_max_retries,
      });
      const updated = await api.get<AutoIngestStatus>("/api/v1/settings/auto-ingest");
      setStatus(updated);
      setDirty(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
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
            <div className="flex items-center gap-1 mb-1">
              <label
                htmlFor="cleanup-policy"
                className="text-xs text-gray-600"
              >
                Ingest bucket cleanup
              </label>
              <button
                type="button"
                onClick={() => setShowCleanupInfo(!showCleanupInfo)}
                className="w-4 h-4 rounded-full bg-gray-200 text-gray-500 hover:bg-gray-300 text-xs font-medium flex items-center justify-center"
                title="What does this do?"
              >
                i
              </button>
            </div>
            {showCleanupInfo && (
              <div className="mb-2 p-2 bg-blue-50 border border-blue-200 rounded text-xs text-blue-800">
                <p className="font-medium mb-1">How file cleanup works</p>
                <p>
                  When files arrive in the ingest bucket, they are copied into the correct
                  storage bucket based on their project and experiment. This setting controls
                  what happens to the original file in the ingest bucket after the copy completes.
                </p>
                <ul className="mt-1 ml-3 list-disc space-y-0.5">
                  <li><strong>Delete after copy</strong> -- removes the original from the ingest bucket immediately after a successful copy. Your data is safe in the destination bucket.</li>
                  <li><strong>Retain for 7/30 days</strong> -- keeps the original in the ingest bucket for the specified period before automatic deletion. Useful if you want a temporary backup in case of issues.</li>
                </ul>
              </div>
            )}
            <select
              id="cleanup-policy"
              data-testid="cleanup-policy-select"
              value={form.cleanup_policy}
              onChange={(e) => updateForm({ cleanup_policy: e.target.value })}
              className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
            >
              <option value="delete_after_copy">
                Delete after copy
              </option>
              <option value="retain_7d">Retain for 7 days</option>
              <option value="retain_30d">Retain for 30 days</option>
            </select>
          </div>

          <div className="border-t border-gray-200 pt-3">
            <p className="text-xs font-medium text-gray-700 uppercase tracking-wide mb-2">Manifest Configuration</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="manifest-filename" className="text-xs text-gray-600 block mb-1">Manifest filename</label>
                <input
                  id="manifest-filename"
                  type="text"
                  value={form.manifest_filename}
                  onChange={(e) => updateForm({ manifest_filename: e.target.value })}
                  className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
                />
              </div>
              <div>
                <label htmlFor="manifest-format" className="text-xs text-gray-600 block mb-1">File format</label>
                <select
                  id="manifest-format"
                  value={form.manifest_format}
                  onChange={(e) => updateForm({ manifest_format: e.target.value })}
                  className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
                >
                  <option value="txt">Text (.txt)</option>
                  <option value="csv">CSV (.csv)</option>
                </select>
              </div>
              <div>
                <label htmlFor="retry-interval" className="text-xs text-gray-600 block mb-1">Retry interval (min)</label>
                <input
                  id="retry-interval"
                  type="number"
                  min="1"
                  max="1440"
                  value={form.manifest_retry_interval_minutes}
                  onChange={(e) => {
                    const val = parseInt(e.target.value, 10);
                    if (!isNaN(val) && val > 0) updateForm({ manifest_retry_interval_minutes: val });
                  }}
                  className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
                />
              </div>
              <div>
                <label htmlFor="max-retries" className="text-xs text-gray-600 block mb-1">Max retries</label>
                <input
                  id="max-retries"
                  type="number"
                  min="1"
                  max="500"
                  value={form.manifest_max_retries}
                  onChange={(e) => {
                    const val = parseInt(e.target.value, 10);
                    if (!isNaN(val) && val > 0) updateForm({ manifest_max_retries: val });
                  }}
                  className="text-xs border border-gray-300 rounded px-2 py-1 w-full"
                />
              </div>
            </div>
          </div>

          {dirty && (
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-3 py-1.5 bg-bioaf-600 text-white rounded text-xs hover:bg-bioaf-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save changes"}
              </button>
              {saveSuccess && (
                <span className="text-xs text-green-600">Saved</span>
              )}
            </div>
          )}

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
