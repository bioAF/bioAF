"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";

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
  config_retention_days: number;
  config_schedule_hours: number;
}

interface RestoreStatus {
  active: boolean;
  backup_filename?: string;
  started_at?: string;
  expires_at?: string;
  seconds_remaining?: number;
}

const statusColors: Record<string, string> = {
  healthy: "bg-green-100 text-green-700",
  warning: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
  unknown: "bg-gray-100 text-gray-600",
};

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
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [pgSnapshots, setPgSnapshots] = useState<PostgresSnapshot[]>([]);
  const [tfstateFiles, setTfstateFiles] = useState<TfstateFile[]>([]);
  const [settings, setSettings] = useState<BackupSettings | null>(null);
  const [restoreStatus, setRestoreStatus] = useState<RestoreStatus>({ active: false });
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState("");
  const [runningAction, setRunningAction] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [restoringFile, setRestoringFile] = useState("");
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
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
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

  const handleConfigRestore = async () => {
    if (!confirm("Are you sure you want to initiate a config restore?")) return;
    try {
      const data = await api.post<{ status: string; message: string }>(
        "/api/backups/restore/config",
        { confirmation_token: "CONFIRM" }
      );
      setActionMessage(data.message);
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Restore failed");
    }
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

  const handleStartRestore = async (filename: string) => {
    const msg = `This will restore the database from "${filename}". The current database will remain available as a fallback. You will have 1 hour to review the restored data before accepting or rejecting.\n\nProceed?`;
    if (!confirm(msg)) return;
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
  };

  const handleAcceptRestore = async () => {
    if (!confirm("Accept this restored database? This will permanently replace the previous database. This cannot be undone.")) return;
    setActionMessage("");
    try {
      const data = await api.post<{ status: string; message: string }>("/api/backups/restore/accept", {});
      setActionMessage(data.message);
      setRestoreStatus({ active: false });
      await loadData();
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Accept failed");
    }
  };

  const handleRejectRestore = async () => {
    if (!confirm("Reject this restore and revert to the original database?")) return;
    setActionMessage("");
    try {
      const data = await api.post<{ status: string; message: string }>("/api/backups/restore/reject", {});
      setActionMessage(data.message);
      setRestoreStatus({ active: false });
      await loadData();
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Reject failed");
    }
  };

  const handleDownloadTfstate = (filename: string) => {
    window.open(`/api/backups/tfstate-download/${encodeURIComponent(filename)}`, "_blank");
  };

  const handleSaveSettings = async () => {
    if (!settings) return;
    setSavingSettings(true);
    setActionMessage("");
    try {
      await api.put("/api/backups/settings", settings);
      setActionMessage("Settings saved");
    } catch (e) {
      setActionMessage(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSavingSettings(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Backup & Recovery</h1>

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
                  <p className="text-xs text-amber-600 mt-1">
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
                <span className={`text-xs px-2 py-1 rounded font-medium ${statusColors[overallStatus] || statusColors.unknown}`}>
                  {overallStatus}
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {tiers.map((tier) => (
                  <div key={tier.tier} className="bg-white rounded-lg border border-gray-200 p-4">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="font-semibold text-gray-900">{tier.name}</h3>
                      <span className={`text-xs px-2 py-0.5 rounded ${statusColors[tier.status] || statusColors.unknown}`}>
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

              {/* Backup Settings */}
              {settings && canAccess("backups", "create") && (
                <>
                  <h2 className="text-lg font-semibold text-gray-900 mb-4">Backup Settings</h2>
                  <div className="bg-white rounded-lg border border-gray-200 p-4 mb-8">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      <div>
                        <h3 className="text-sm font-medium text-gray-900 mb-3">PostgreSQL</h3>
                        <div className="space-y-3">
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Schedule (hours)</label>
                            <input
                              type="number"
                              min={1}
                              value={settings.postgres_schedule_hours}
                              onChange={(e) => setSettings({ ...settings, postgres_schedule_hours: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Retention (days)</label>
                            <input
                              type="number"
                              min={1}
                              value={settings.postgres_retention_days}
                              onChange={(e) => setSettings({ ...settings, postgres_retention_days: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                        </div>
                      </div>
                      <div>
                        <h3 className="text-sm font-medium text-gray-900 mb-3">Platform Config</h3>
                        <div className="space-y-3">
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Schedule (hours)</label>
                            <input
                              type="number"
                              min={1}
                              value={settings.config_schedule_hours}
                              onChange={(e) => setSettings({ ...settings, config_schedule_hours: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">Retention (days)</label>
                            <input
                              type="number"
                              min={1}
                              value={settings.config_retention_days}
                              onChange={(e) => setSettings({ ...settings, config_retention_days: parseInt(e.target.value) || 1 })}
                              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={handleSaveSettings}
                      disabled={savingSettings}
                      className="mt-4 text-sm bg-blue-600 text-white px-4 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50"
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
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Filename</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Date</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                      {canAccess("backups", "restore") && (
                        <th className="text-left px-4 py-3 font-medium text-gray-700"></th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {pgSnapshots.length === 0 ? (
                      <tr>
                        <td colSpan={canAccess("backups", "restore") ? 4 : 3} className="px-4 py-8 text-center text-gray-500">
                          No snapshots available
                        </td>
                      </tr>
                    ) : (
                      pgSnapshots.map((s) => (
                        <tr key={s.filename} className="border-b hover:bg-gray-50">
                          <td className="px-4 py-2.5 text-gray-900 font-mono text-xs">{s.filename}</td>
                          <td className="px-4 py-2.5 text-gray-600">{s.date}</td>
                          <td className="px-4 py-2.5 text-gray-600">{formatBytes(s.size_bytes)}</td>
                          {canAccess("backups", "restore") && (
                            <td className="px-4 py-2.5">
                              <button
                                onClick={() => handleStartRestore(s.filename)}
                                disabled={restoreStatus.active || restoringFile !== ""}
                                className="text-xs text-amber-600 hover:text-amber-800 disabled:opacity-50 disabled:cursor-not-allowed"
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
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Date</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Tier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshots.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="px-4 py-8 text-center text-gray-500">
                          No snapshots available
                        </td>
                      </tr>
                    ) : (
                      snapshots.map((s) => (
                        <tr key={s.date} className="border-b hover:bg-gray-50">
                          <td className="px-4 py-2.5 text-gray-900">{s.date}</td>
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
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Name</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Size</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700">Last Updated</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-700"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {tfstateFiles.length === 0 ? (
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
                              className="text-xs text-blue-600 hover:text-blue-800"
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
      </div>
    </div>
  );
}
