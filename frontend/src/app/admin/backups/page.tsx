"use client";

import { useEffect, useState } from "react";
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
  pitr_window_hours: number | null;
  versioning_enabled: boolean | null;
}

interface ConfigSnapshot {
  date: string;
  size_bytes: number | null;
  tier: string;
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

export default function BackupsPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [tiers, setTiers] = useState<BackupTier[]>([]);
  const [overallStatus, setOverallStatus] = useState("");
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [restoreMessage, setRestoreMessage] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("backups", "view")) { router.push("/dashboard"); return; }

    const load = async () => {
      try {
        const [status, snaps] = await Promise.all([
          api.get<{ tiers: BackupTier[]; overall_status: string }>("/api/backups/status"),
          api.get<{ snapshots: ConfigSnapshot[] }>("/api/backups/config-snapshots"),
        ]);
        setTiers(status.tiers);
        setOverallStatus(status.overall_status);
        setSnapshots(snaps.snapshots);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [router, permLoading, canAccess]);

  const handleRestore = async (type: string) => {
    if (!confirm(`Are you sure you want to initiate a ${type} restore?`)) return;
    try {
      const data = await api.post<{ status: string; message: string }>(
        `/api/backups/restore/${type}`,
        { confirmation_token: "CONFIRM" }
      );
      setRestoreMessage(data.message);
    } catch (e) {
      setRestoreMessage(e instanceof Error ? e.message : "Restore failed");
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Backup & Recovery</h1>

          {restoreMessage && (
            <div className="mb-4 p-3 rounded bg-blue-50 text-blue-700 text-sm">
              {restoreMessage}
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

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
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
                      {tier.pitr_window_hours && (
                        <div className="flex justify-between">
                          <span>PITR Window:</span>
                          <span className="text-gray-900">{tier.pitr_window_hours}h</span>
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
                    {(tier.tier === "cloudsql" || tier.tier === "filestore" || tier.tier === "config") && (
                      <button
                        onClick={() => handleRestore(tier.tier === "config" ? "config" : tier.tier)}
                        className="mt-3 w-full text-sm bg-gray-100 text-gray-700 px-3 py-1.5 rounded hover:bg-gray-200"
                      >
                        Restore
                      </button>
                    )}
                  </div>
                ))}
              </div>

              <h2 className="text-lg font-semibold text-gray-900 mb-4">Config Snapshots</h2>
              <div className="bg-white rounded-lg border border-gray-200">
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
            </>
          )}
        </main>
      </div>
    </div>
  );
}
