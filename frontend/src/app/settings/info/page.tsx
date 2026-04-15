"use client";

import { useEffect, useMemo, useState, useRef, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";

interface UpdateCheck {
  current_version: string;
  latest_version: string;
  update_available: boolean;
  changelog: string | null;
  release_url: string | null;
}

interface UpgradeHistoryItem {
  id: number;
  from_version: string;
  to_version: string;
  status: string;
  started_at: string;
  completed_at: string | null;
}

interface UpdateStatus {
  status: string;
  from_version?: string;
  to_version?: string;
  step?: string;
  started_at?: string;
  error?: string;
}

const STEP_LABELS: Record<string, string> = {
  backup: "Backing up database",
  checkout: "Fetching new version",
  build: "Rebuilding containers",
  warn: "Preparing to restart",
  restart: "Restarting services",
  migrate: "Running database migrations",
};

const WARN_DURATION_SECONDS = 60;

export default function SettingsInfoPage() {
  const { canAccess } = usePermissions();
  const [updateCheck, setUpdateCheck] = useState<UpdateCheck | null>(null);
  const [upgradeHistory, setUpgradeHistory] = useState<UpgradeHistoryItem[]>([]);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);
  const [updateError, setUpdateError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const canDeploy = canAccess("infrastructure", "deploy");

  const pollUpdateStatus = useCallback(async () => {
    try {
      const status = await api.get<UpdateStatus>("/api/upgrades/status");
      setUpdateStatus(status);

      if (status.status === "completed" || status.status === "failed") {
        // Stop polling
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setUpdating(false);

        if (status.status === "failed") {
          setUpdateError(status.error || "Update failed");
        }

        // Refresh version info and history
        try {
          const [version, history] = await Promise.all([
            api.get<UpdateCheck>("/api/upgrades/check"),
            api.get<{ upgrades: UpgradeHistoryItem[] }>("/api/upgrades/history"),
          ]);
          setUpdateCheck(version);
          setUpgradeHistory(history.upgrades);
        } catch {
          // Backend may still be restarting
        }
      }
    } catch {
      // Backend may be down during update -- keep polling
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const [version, history, status] = await Promise.all([
          api.get<UpdateCheck>("/api/upgrades/check"),
          api.get<{ upgrades: UpgradeHistoryItem[] }>("/api/upgrades/history"),
          api.get<UpdateStatus>("/api/upgrades/status"),
        ]);
        setUpdateCheck(version);
        setUpgradeHistory(history.upgrades);

        if (status.status === "in_progress") {
          setUpdateStatus(status);
          setUpdating(true);
          if (!pollRef.current) {
            pollRef.current = setInterval(pollUpdateStatus, 3000);
          }
        }
      } catch {
        // ignore
      }
    };
    load();

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, [pollUpdateStatus]);

  // Tick once per second while showing the reboot countdown
  useEffect(() => {
    if (updateStatus?.step !== "warn") return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [updateStatus?.step]);

  const rebootCountdown = useMemo(() => {
    if (updateStatus?.step !== "warn" || !updateStatus.started_at) return null;
    const started = new Date(updateStatus.started_at).getTime();
    const elapsed = Math.floor((now - started) / 1000);
    return Math.max(0, WARN_DURATION_SECONDS - elapsed);
  }, [updateStatus?.step, updateStatus?.started_at, now]);

  const handleCheckUpdate = async () => {
    setCheckingUpdate(true);
    try {
      const data = await api.get<UpdateCheck>("/api/upgrades/check");
      setUpdateCheck(data);
    } catch {
      // ignore
    } finally {
      setCheckingUpdate(false);
    }
  };

  const handleInstallUpdate = async () => {
    if (!updateCheck?.latest_version) return;

    setUpdating(true);
    setUpdateError("");
    setUpdateStatus({ status: "in_progress", step: "starting" });

    try {
      await api.post("/api/upgrades/execute", {
        target_version: updateCheck.latest_version,
      });

      // Start polling for status
      pollRef.current = setInterval(pollUpdateStatus, 3000);
    } catch (e) {
      setUpdating(false);
      setUpdateError(e instanceof Error ? e.message : "Failed to start update");
      setUpdateStatus(null);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Platform Info</h1>

          <div className="bg-white rounded-lg shadow p-6 mb-6">
            <h2 className="text-lg font-semibold mb-4">Platform Version</h2>

            {updateCheck && (
              <div className="space-y-3">
                <div className="flex items-center gap-4">
                  <span className="text-sm text-gray-600">Current Version:</span>
                  <span className="font-mono font-bold">{updateCheck.current_version}</span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-sm text-gray-600">Latest Version:</span>
                  <span className="font-mono">{updateCheck.latest_version}</span>
                </div>

                {updateCheck.update_available && !updating && (
                  <div className="p-3 bg-blue-50 border border-blue-200 rounded text-sm text-blue-700">
                    <div className="flex items-center justify-between">
                      <div>
                        A new version ({updateCheck.latest_version}) is available!
                        {updateCheck.release_url && (
                          <a href={updateCheck.release_url} target="_blank" rel="noopener noreferrer" className="ml-2 underline">
                            View release
                          </a>
                        )}
                      </div>
                      {canDeploy && (
                        <button
                          onClick={handleInstallUpdate}
                          className="ml-4 px-4 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 whitespace-nowrap"
                        >
                          Install Update
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* Update progress */}
                {updating && updateStatus && (
                  <div className="p-4 bg-amber-50 border border-amber-200 rounded">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="h-4 w-4 border-2 border-amber-600 border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm font-medium text-amber-800">
                        Updating to {updateStatus.to_version || updateCheck.latest_version}
                      </span>
                    </div>
                    {updateStatus.step && (
                      <p className="text-sm text-amber-700 ml-7">
                        {STEP_LABELS[updateStatus.step] || updateStatus.step}...
                      </p>
                    )}
                    {updateStatus.step === "warn" && rebootCountdown !== null ? (
                      <p className="text-sm font-semibold text-amber-900 ml-7 mt-1">
                        {`Restarting in ${rebootCountdown}s -- the application will be briefly unavailable.`}
                      </p>
                    ) : (
                      <p className="text-xs text-amber-600 ml-7 mt-1">
                        The application will restart during this process.
                      </p>
                    )}
                  </div>
                )}

                {/* Update error */}
                {updateError && (
                  <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
                    Update failed: {updateError}
                  </div>
                )}

                {updateCheck.changelog && (
                  <div className="p-3 bg-gray-50 rounded">
                    <h3 className="text-sm font-medium mb-1">Changelog</h3>
                    <p className="text-sm text-gray-600 whitespace-pre-wrap">{updateCheck.changelog}</p>
                  </div>
                )}

                <button
                  onClick={handleCheckUpdate}
                  disabled={checkingUpdate || updating}
                  className="text-sm text-bioaf-600 hover:text-bioaf-700 disabled:opacity-50"
                >
                  {checkingUpdate ? "Checking..." : "Check for updates"}
                </button>
              </div>
            )}

            {!updateCheck && (
              <p className="text-gray-500 text-sm">Loading version information...</p>
            )}
          </div>

          {upgradeHistory.length > 0 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Upgrade History</h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-gray-50">
                    <th className="text-left px-3 py-2 font-medium text-gray-700">From</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-700">To</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-700">Status</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-700">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {upgradeHistory.map((u) => (
                    <tr key={u.id} className="border-b">
                      <td className="px-3 py-2 font-mono">{u.from_version}</td>
                      <td className="px-3 py-2 font-mono">{u.to_version}</td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          u.status === "completed" ? "bg-green-100 text-green-700" :
                          u.status === "failed" ? "bg-red-100 text-red-700" :
                          u.status === "rolled_back" ? "bg-yellow-100 text-yellow-700" :
                          "bg-gray-100 text-gray-700"
                        }`}>
                          {u.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-gray-500">{new Date(u.started_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
