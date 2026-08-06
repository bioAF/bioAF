"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { statusBadgeClass } from "@/lib/statusStyles";
import { ErrorState } from "@/components/shared/ErrorState";

interface BackupTier {
  tier: string;
  name: string;
  last_backup: string | null;
  size_bytes: number | null;
  next_scheduled: string | null;
  retention_days: number | null;
  status: string;
  versioning_enabled: boolean | null;
  backup_count: number | null;
}

interface ConfigSnapshot {
  date: string;
  size_bytes: number | null;
  tier: string;
}

interface PostgresSnapshot {
  filename: string;
  date: string;
  size_bytes: number | null;
}

interface TfstateFile {
  name: string;
  size_bytes: number;
  updated: string | null;
}

interface BackupSettings {
  postgres_retention_days: number;
  postgres_schedule_hours: number;
  postgres_schedule_enabled: boolean;
  postgres_next_run: string | null;
  config_retention_days: number;
  config_schedule_hours: number;
  config_schedule_enabled: boolean;
  config_next_run: string | null;
}

interface RestoreStatus {
  active: boolean;
  backup_filename?: string;
  started_at?: string;
  expires_at?: string;
  seconds_remaining?: number;
}

function formatBytes(bytes: number | null): string {
  if (!bytes) return "N/A";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

function formatMinutes(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs}s`;
}

export default function InfraBackupPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [tiers, setTiers] = useState<BackupTier[]>([]);
  const [overallStatus, setOverallStatus] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [pgSnapshots, setPgSnapshots] = useState<PostgresSnapshot[]>([]);
  const [tfstateFiles, setTfstateFiles] = useState<TfstateFile[]>([]);
  const [settings, setSettings] = useState<BackupSettings | null>(null);
  const [restoreStatus, setRestoreStatus] = useState<RestoreStatus>({ active: false });
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState("");
  const [runningAction, setRunningAction] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [pgFirstRun, setPgFirstRun] = useState<string>("now");
  const [cfgFirstRun, setCfgFirstRun] = useState<string>("now");
  const [restoringFile, setRestoringFile] = useState("");
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean;
    title: string;
    message: string;
    confirmLabel: string;
    variant: "danger" | "default";
    onConfirm: () => void;
  }>({ open: false, title: "", message: "", confirmLabel: "Confirm", variant: "default", onConfirm: () => {} });
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [status, snaps, pgSnaps, tfFiles, backupSettings, rStatus] = await Promise.all([
        api.get<{ tiers: BackupTier[]; overall_status: string }>("/api/backups/status"),
        api.get<{ snapshots: ConfigSnapshot[] }>("/api/backups/config-snapshots"),
        api.get<{ snapshots: PostgresSnapshot[] }>("/api/backups/postgres-snapshots"),
        api.get<{ files: TfstateFile[] }>("/api/backups/tfstate-files"),
        api.get<BackupSettings>("/api/backups/settings"),
        api.get<RestoreStatus>("/api/backups/restore/status"),
      ]);
      setTiers(status.tiers);
      setOverallStatus(status.overall_status);
      setSnapshots(snaps.snapshots);
      setPgSnapshots(pgSnaps.snapshots);
      setTfstateFiles(tfFiles.files);
      setSettings(backupSettings);
      setRestoreStatus(rStatus);
      setLoadError(null);
    } catch (e) {
      // Falling through to the empty states told an admin there were NO backups,
      // which during an outage is the most alarming possible wrong answer.
      logError("loading backup status", e);
      setLoadError(loadFailureMessage("Backup status"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("backups", "view")) { router.push("/dashboard"); return; }
    loadData();
  }, [router, permLoading, canAccess, loadData]);

  // Poll restore status while active
  useEffect(() => {
    if (restoreStatus.active) {
      pollRef.current = setInterval(async () => {
        try {
          const rStatus = await api.get<RestoreStatus>("/api/backups/restore/status");
          setRestoreStatus(rStatus);
          if (!rStatus.active) {
            if (pollRef.current) clearInterval(pollRef.current);
            setActionMessage("Restore review expired. Reverted to original database.");
            await loadData();
          }
        } catch { /* ignore */ }
      }, 30000);
      return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }
  }, [restoreStatus.active, loadData]);

  const handleConfigRestore = () => {
    setConfirmDialog({
      open: true,
      title: "Restore Configuration",
      message: "Are you sure you want to initiate a config restore?",
      confirmLabel: "Restore",
      variant: "danger",
      onConfirm: async () => {
        setConfirmDialog((prev) => ({ ...prev, open: false }));
        try {
          const data = await api.post<{ status: string; message: string }>(
            "/api/backups/restore/config",
            { confirmation_token: "CONFIRM" }
          );
          setActionMessage(data.message);
        } catch (e) {
          setActionMessage(e instanceof Error ? e.message : "Restore failed");
        }
      },
    });
  };

  const handleTriggerBackup = async (type: "postgres" | "config") => {
    setRunningAction(type);
    setActionMessage("");
    try {
      const data = await api.post<{ status: string; filename: string; size_bytes: number }>(
        `/api/backups/trigger/${type}`,
        {}
      );
      setActionMessage(`Backup completed: ${data.filename} (${formatBytes(data.size_bytes)})`);
      await loadData();
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Backup failed");
    } finally {
      setRunningAction("");
    }
  };

  const handleStartRestore = (filename: string) => {
    setConfirmDialog({
      open: true,
      title: "Restore Database",
      message: `This will restore the database from "${filename}". The current database will remain available as a fallback. You will have 1 hour to review the restored data before accepting or rejecting.`,
      confirmLabel: "Restore",
      variant: "danger",
      onConfirm: async () => {
        setConfirmDialog((prev) => ({ ...prev, open: false }));
        setRestoringFile(filename);
        setActionMessage("");
        try {
          const data = await api.post<{ status: string; message: string }>(
            "/api/backups/restore/postgres",
            { filename }
          );
          setActionMessage(data.message);
          await loadData();
        } catch (e) {
          setActionMessage(e instanceof Error ? e.message : "Restore failed");
        } finally {
          setRestoringFile("");
        }
      },
    });
  };

  const handleAcceptRestore = () => {
    setConfirmDialog({
      open: true,
      title: "Accept Restored Database",
      message: "This will permanently replace the previous database. This cannot be undone.",
      confirmLabel: "Accept",
      variant: "danger",
      onConfirm: async () => {
        setConfirmDialog((prev) => ({ ...prev, open: false }));
        setActionMessage("");
        try {
          const data = await api.post<{ status: string; message: string }>("/api/backups/restore/accept", {});
          setActionMessage(data.message);
          setRestoreStatus({ active: false });
          await loadData();
        } catch (e) {
          setActionMessage(e instanceof Error ? e.message : "Accept failed");
        }
      },
    });
  };

  const handleRejectRestore = () => {
    setConfirmDialog({
      open: true,
      title: "Reject Restore",
      message: "Reject this restore and revert to the original database?",
      confirmLabel: "Reject",
      variant: "default",
      onConfirm: async () => {
        setConfirmDialog((prev) => ({ ...prev, open: false }));
        setActionMessage("");
        try {
          const data = await api.post<{ status: string; message: string }>("/api/backups/restore/reject", {});
          setActionMessage(data.message);
          setRestoreStatus({ active: false });
          await loadData();
        } catch (e) {
          setActionMessage(e instanceof Error ? e.message : "Reject failed");
        }
      },
    });
  };

  const handleDownloadTfstate = (filename: string) => {
    window.open(`/api/backups/tfstate-download/${encodeURIComponent(filename)}`, "_blank");
  };

  const handleSaveSettings = async () => {
    if (!settings) return;
    setSavingSettings(true);
    setActionMessage("");
    try {
      const payload: Record<string, unknown> = {
        postgres_retention_days: settings.postgres_retention_days,
        postgres_schedule_hours: settings.postgres_schedule_hours,
        postgres_schedule_enabled: settings.postgres_schedule_enabled,
        config_retention_days: settings.config_retention_days,
        config_schedule_hours: settings.config_schedule_hours,
        config_schedule_enabled: settings.config_schedule_enabled,
      };
      // Only send first_run when enabling a schedule that has no next_run yet
      if (settings.postgres_schedule_enabled && !settings.postgres_next_run) {
        payload.postgres_first_run = pgFirstRun === "now"
          ? "now"
          : new Date(pgFirstRun).toISOString();
      }
      if (settings.config_schedule_enabled && !settings.config_next_run) {
        payload.config_first_run = cfgFirstRun === "now"
          ? "now"
          : new Date(cfgFirstRun).toISOString();
      }
      const result = await api.put<{ status: string; settings: BackupSettings }>(
        "/api/backups/settings", payload
      );
      setSettings(result.settings);
      setActionMessage("Settings saved");
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">Backup & Recovery</h1>
        {loadError && (
          <div className="mb-6">
            <ErrorState
              message={loadError}
              onRetry={() => loadData()}
            />
          </div>
        )}

        {/* Restore review banner */}
        {restoreStatus.active && (
          <div className="mb-4 p-4 rounded-lg bg-amber-50 border border-amber-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold text-amber-900">
                  Reviewing restored database
                </p>
                <p className="text-sm text-amber-700 mt-1">
                  Restored from <span className="font-mono">{restoreStatus.backup_filename}</span>.
                  {restoreStatus.seconds_remaining !== undefined && (
                    <> Auto-reverts in <span className="font-semibold">{formatMinutes(restoreStatus.seconds_remaining)}</span>.</>
                  )}
                </p>
                <p className="text-xs text-amber-700 mt-1">
                  Browse the application to verify data. Accept to make permanent, or reject to revert.
                </p>
              </div>
              {canAccess("backups", "restore") && (
                <div className="flex gap-2 ml-4">
                  <button
                    onClick={handleRejectRestore}
                    className="text-sm px-4 py-2 rounded border border-gray-300 text-gray-700 bg-white hover:bg-gray-50"
                  >
                    Reject
                  </button>
                  <button
                    onClick={handleAcceptRestore}
                    className="text-sm px-4 py-2 rounded bg-green-600 text-white hover:bg-green-700"
                  >
                    Accept
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {actionMessage && (
          <div className="mb-4 p-3 rounded bg-blue-50 text-blue-700 text-sm">
            {actionMessage}
          </div>
        )}

        {loading ? (
          <div className="text-gray-500">Loading backup status...</div>
        ) : (
          <>
            <div className="mb-4 flex items-center gap-2">
              <span className="text-sm text-gray-600">Overall Status:</span>
              <span className={`text-xs px-2 py-1 rounded font-medium ${statusBadgeClass("backupTier", overallStatus)}`}>
                {overallStatus}
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
              {tiers.map((tier) => (
                <div key={tier.tier} className="bg-white rounded-lg border border-gray-200 p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-semibold text-gray-900">{tier.name}</h3>
                    <span className={`text-xs px-2 py-0.5 rounded ${statusBadgeClass("backupTier", tier.status)}`}>
                      {tier.status}
                    </span>
                  </div>
                  <div className="space-y-2 text-sm text-gray-600">
                    <div className="flex justify-between">
                      <span>Last Backup:</span>
                      <span className="text-gray-900">
                        {tier.last_backup ? new Date(tier.last_backup).toLocaleString() : "N/A"}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>Size:</span>
                      <span className="text-gray-900">{formatBytes(tier.size_bytes)}</span>
                    </div>
                    {tier.retention_days && (
                      <div className="flex justify-between">
                        <span>Retention:</span>
                        <span className="text-gray-900">{tier.retention_days} days</span>
                      </div>
                    )}
                    {tier.backup_count !== null && (
                      <div className="flex justify-between">
                        <span>Backups:</span>
                        <span className="text-gray-900">{tier.backup_count}</span>
                      </div>
                    )}
                    {tier.versioning_enabled !== null && (
                      <div className="flex justify-between">
                        <span>Versioning:</span>
                        <span className="text-gray-900">{tier.versioning_enabled ? "Enabled" : "Disabled"}</span>
                      </div>
                    )}
                    {tier.next_scheduled && (
                      <div className="flex justify-between">
                        <span>Next:</span>
                        <span className="text-gray-900">{new Date(tier.next_scheduled).toLocaleString()}</span>
                      </div>
                    )}
                  </div>
                  {tier.tier === "postgres" && canAccess("backups", "create") && (
                    <button
                      onClick={() => handleTriggerBackup("postgres")}
                      disabled={runningAction !== "" || restoreStatus.active}
                      className="mt-3 w-full text-sm bg-blue-50 text-blue-700 px-3 py-1.5 rounded hover:bg-blue-100 disabled:opacity-50"
                    >
                      {runningAction === "postgres" ? "Running..." : "Run Backup Now"}
                    </button>
                  )}
                  {tier.tier === "platform_config" && canAccess("backups", "create") && (
                    <button
                      onClick={() => handleTriggerBackup("config")}
                      disabled={runningAction !== ""}
                      className="mt-3 w-full text-sm bg-blue-50 text-blue-700 px-3 py-1.5 rounded hover:bg-blue-100 disabled:opacity-50"
                    >
                      {runningAction === "config" ? "Running..." : "Run Backup Now"}
                    </button>
                  )}
                  {tier.tier === "platform_config" && canAccess("backups", "restore") && (
                    <button
                      onClick={handleConfigRestore}
                      className="mt-1 w-full text-sm bg-gray-100 text-gray-700 px-3 py-1.5 rounded hover:bg-gray-200"
                    >
                      Restore
                    </button>
                  )}
                </div>
              ))}
            </div>

            {/* Backup Schedule */}
            {settings && canAccess("backups", "create") && (
              <>
                <h2 className="text-lg font-semibold text-gray-900 mb-4">Backup Schedule</h2>
                <div className="bg-white rounded-lg border border-gray-200 p-4 mb-8">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* PostgreSQL Schedule */}
                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-900">PostgreSQL</h3>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            checked={settings.postgres_schedule_enabled}
                            onChange={(e) => setSettings({ ...settings, postgres_schedule_enabled: e.target.checked })}
                            className="sr-only peer"
                          />
                          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bioaf-600"></div>
                        </label>
                      </div>
                      {settings.postgres_schedule_enabled && (
                        <div className="space-y-3">
                          {!settings.postgres_next_run && (
                            <div>
                              <label htmlFor="first-backup" className="block text-xs text-gray-500 mb-1">First backup</label>
                              <div className="flex gap-2">
                                <select id="first-backup"
                                  value={pgFirstRun === "now" ? "now" : "scheduled"}
                                  onChange={(e) => {
                                    if (e.target.value === "now") {
                                      setPgFirstRun("now");
                                    } else {
                                      const d = new Date();
                                      d.setHours(d.getHours() + 1, 0, 0, 0);
                                      setPgFirstRun(d.toISOString().slice(0, 16));
                                    }
                                  }}
                                  className="border border-gray-300 rounded px-2 py-1.5 text-sm"
                                >
                                  <option value="now">Now</option>
                                  <option value="scheduled">Pick a time</option>
                                </select>
                                {pgFirstRun !== "now" && (
                                  <input aria-label="First run date and time"
                                    type="datetime-local"
                                    value={pgFirstRun}
                                    onChange={(e) => setPgFirstRun(e.target.value)}
                                    className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-sm"
                                  />
                                )}
                              </div>
                            </div>
                          )}
                          {settings.postgres_next_run && (
                            <div className="text-xs text-gray-600 bg-blue-50 rounded p-2">
                              Next backup: {new Date(settings.postgres_next_run).toLocaleString()}
                            </div>
                          )}
                          <div>
                            <label htmlFor="run-every-hours" className="block text-xs text-gray-500 mb-1">Run every (hours)</label>
                            <input id="run-every-hours"
                              type="number"
                              min={1}
                              value={settings.postgres_schedule_hours}
                              onChange={(e) => setSettings({ ...settings, postgres_schedule_hours: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                          <div>
                            <label htmlFor="keep-backups-for-days" className="block text-xs text-gray-500 mb-1">Keep backups for (days)</label>
                            <input id="keep-backups-for-days"
                              type="number"
                              min={1}
                              value={settings.postgres_retention_days}
                              onChange={(e) => setSettings({ ...settings, postgres_retention_days: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                        </div>
                      )}
                      {!settings.postgres_schedule_enabled && (
                        <p className="text-xs text-gray-500">Automatic backups disabled. Use &quot;Run Backup Now&quot; for manual backups.</p>
                      )}
                    </div>

                    {/* Config Schedule */}
                    <div>
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-sm font-medium text-gray-900">Platform Config</h3>
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            checked={settings.config_schedule_enabled}
                            onChange={(e) => setSettings({ ...settings, config_schedule_enabled: e.target.checked })}
                            className="sr-only peer"
                          />
                          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bioaf-600"></div>
                        </label>
                      </div>
                      {settings.config_schedule_enabled && (
                        <div className="space-y-3">
                          {!settings.config_next_run && (
                            <div>
                              <label htmlFor="first-backup-2" className="block text-xs text-gray-500 mb-1">First backup</label>
                              <div className="flex gap-2">
                                <select id="first-backup-2"
                                  value={cfgFirstRun === "now" ? "now" : "scheduled"}
                                  onChange={(e) => {
                                    if (e.target.value === "now") {
                                      setCfgFirstRun("now");
                                    } else {
                                      const d = new Date();
                                      d.setHours(d.getHours() + 1, 0, 0, 0);
                                      setCfgFirstRun(d.toISOString().slice(0, 16));
                                    }
                                  }}
                                  className="border border-gray-300 rounded px-2 py-1.5 text-sm"
                                >
                                  <option value="now">Now</option>
                                  <option value="scheduled">Pick a time</option>
                                </select>
                                {cfgFirstRun !== "now" && (
                                  <input aria-label="First run date and time"
                                    type="datetime-local"
                                    value={cfgFirstRun}
                                    onChange={(e) => setCfgFirstRun(e.target.value)}
                                    className="flex-1 border border-gray-300 rounded px-2 py-1.5 text-sm"
                                  />
                                )}
                              </div>
                            </div>
                          )}
                          {settings.config_next_run && (
                            <div className="text-xs text-gray-600 bg-blue-50 rounded p-2">
                              Next backup: {new Date(settings.config_next_run).toLocaleString()}
                            </div>
                          )}
                          <div>
                            <label htmlFor="run-every-hours-2" className="block text-xs text-gray-500 mb-1">Run every (hours)</label>
                            <input id="run-every-hours-2"
                              type="number"
                              min={1}
                              value={settings.config_schedule_hours}
                              onChange={(e) => setSettings({ ...settings, config_schedule_hours: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                          <div>
                            <label htmlFor="keep-backups-for-days-2" className="block text-xs text-gray-500 mb-1">Keep backups for (days)</label>
                            <input id="keep-backups-for-days-2"
                              type="number"
                              min={1}
                              value={settings.config_retention_days}
                              onChange={(e) => setSettings({ ...settings, config_retention_days: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                        </div>
                      )}
                      {!settings.config_schedule_enabled && (
                        <p className="text-xs text-gray-500">Automatic backups disabled. Use &quot;Run Backup Now&quot; for manual backups.</p>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={handleSaveSettings}
                    disabled={savingSettings}
                    className="mt-4 text-sm bg-bioaf-600 text-white px-4 py-1.5 rounded hover:bg-bioaf-700 disabled:opacity-50"
                  >
                    {savingSettings ? "Saving..." : "Save Settings"}
                  </button>
                </div>
              </>
            )}

            <h2 className="text-lg font-semibold text-gray-900 mb-4">PostgreSQL Snapshots</h2>
            <div className="bg-white rounded-lg border border-gray-200 mb-8">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50">
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Filename</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Date</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                    {canAccess("backups", "restore") && (
                      <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700"></th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {loadError ? null : pgSnapshots.length === 0 ? (
                    <tr>
                      <td colSpan={canAccess("backups", "restore") ? 4 : 3} className="px-4 py-8 text-center text-gray-500">
                        No snapshots available
                      </td>
                    </tr>
                  ) : (
                    pgSnapshots.map((s) => (
                      <tr key={s.filename} className="border-b hover:bg-gray-50">
                        <td className="px-4 py-2.5 text-gray-900 font-mono text-xs">{s.filename}</td>
                        <td className="px-4 py-2.5 text-gray-600">{new Date(s.date).toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-gray-600">{formatBytes(s.size_bytes)}</td>
                        {canAccess("backups", "restore") && (
                          <td className="px-4 py-2.5">
                            <button
                              onClick={() => handleStartRestore(s.filename)}
                              disabled={restoreStatus.active || restoringFile !== ""}
                              className="text-xs text-amber-700 hover:text-amber-900 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {restoringFile === s.filename ? "Restoring..." : "Restore"}
                            </button>
                          </td>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <h2 className="text-lg font-semibold text-gray-900 mb-4">Config Snapshots</h2>
            <div className="bg-white rounded-lg border border-gray-200 mb-8">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50">
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Date</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Tier</th>
                  </tr>
                </thead>
                <tbody>
                  {loadError ? null : snapshots.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="px-4 py-8 text-center text-gray-500">
                        No snapshots available
                      </td>
                    </tr>
                  ) : (
                    snapshots.map((s) => (
                      <tr key={s.date} className="border-b hover:bg-gray-50">
                        <td className="px-4 py-2.5 text-gray-900">{new Date(s.date).toLocaleString()}</td>
                        <td className="px-4 py-2.5 text-gray-600">{formatBytes(s.size_bytes)}</td>
                        <td className="px-4 py-2.5 text-gray-600">{s.tier}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <h2 className="text-lg font-semibold text-gray-900 mb-4">Terraform State Files</h2>
            <div className="bg-white rounded-lg border border-gray-200">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50">
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Name</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700">Last Updated</th>
                    <th scope="col" className="text-left px-4 py-3 font-medium text-gray-700"></th>
                  </tr>
                </thead>
                <tbody>
                  {loadError ? null : tfstateFiles.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center text-gray-500">
                        No state files available
                      </td>
                    </tr>
                  ) : (
                    tfstateFiles.map((f) => (
                      <tr key={f.name} className="border-b hover:bg-gray-50">
                        <td className="px-4 py-2.5 text-gray-900 font-mono text-xs">{f.name}</td>
                        <td className="px-4 py-2.5 text-gray-600">{formatBytes(f.size_bytes)}</td>
                        <td className="px-4 py-2.5 text-gray-600">
                          {f.updated ? new Date(f.updated).toLocaleString() : "N/A"}
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            onClick={() => handleDownloadTfstate(f.name)}
                            className="text-xs text-bioaf-600 hover:text-bioaf-700"
                          >
                            Download
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
      <ConfirmDialog
        open={confirmDialog.open}
        title={confirmDialog.title}
        message={confirmDialog.message}
        confirmLabel={confirmDialog.confirmLabel}
        variant={confirmDialog.variant}
        onConfirm={confirmDialog.onConfirm}
        onCancel={() => setConfirmDialog((prev) => ({ ...prev, open: false }))}
      />
    </>
  );
}
