"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";

interface SlackStatus {
  connected: boolean;
  team_name: string | null;
}

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

export default function SettingsPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [hasExistingPassword, setHasExistingPassword] = useState(false);
  const [smtpFrom, setSmtpFrom] = useState("");
  const [smtpEncryption, setSmtpEncryption] = useState("starttls");
  const [testEmailTo, setTestEmailTo] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  // Slack status
  const [slackStatus, setSlackStatus] = useState<SlackStatus | null>(null);

  // Upgrade system
  const [updateCheck, setUpdateCheck] = useState<UpdateCheck | null>(null);
  const [upgradeHistory, setUpgradeHistory] = useState<UpgradeHistoryItem[]>([]);
  const [checkingUpdate, setCheckingUpdate] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("infrastructure", "configure")) { router.push("/dashboard"); return; }

    const load = async () => {
      try {
        const [slack, version, history, smtp] = await Promise.all([
          api.get<SlackStatus>("/api/notifications/slack/status"),
          api.get<UpdateCheck>("/api/upgrades/check"),
          api.get<{ upgrades: UpgradeHistoryItem[] }>("/api/upgrades/history"),
          api.get<{ host: string; port: number; username: string; password?: string; from_address: string; encryption: string }>("/api/bootstrap/smtp-settings"),
        ]);
        setSlackStatus(slack);
        setUpdateCheck(version);
        setUpgradeHistory(history.upgrades);
        if (smtp.host) {
          setSmtpHost(smtp.host);
          setSmtpPort(String(smtp.port));
          setSmtpUsername(smtp.username);
          setSmtpFrom(smtp.from_address);
          setSmtpEncryption(smtp.encryption);
        }
        if (smtp.password) {
          setHasExistingPassword(true);
        }
      } catch {
        // ignore
      }
    };
    load();
  }, [router, permLoading, canAccess]);

  const handleSaveSmtp = async () => {
    setError("");
    setMessage("");
    try {
      await api.post("/api/bootstrap/configure-smtp", {
        host: smtpHost,
        port: parseInt(smtpPort),
        username: smtpUsername,
        password: smtpPassword,
        from_address: smtpFrom,
        encryption: smtpEncryption,
      });
      setMessage("SMTP configuration saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save SMTP settings");
    }
  };

  const handleTestNotification = async (channel: string) => {
    try {
      await api.post("/api/notifications/test", { channel });
      setMessage(`Test notification sent via ${channel}`);
    } catch {
      setError(`Failed to send test notification via ${channel}`);
    }
  };

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

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Admin Settings</h1>

          {message && <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">{message}</div>}
          {error && <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">{error}</div>}

          {/* SMTP Configuration */}
          <div className="bg-white rounded-lg shadow p-6 mb-6">
            <h2 className="text-lg font-semibold mb-4">SMTP Configuration</h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Host</label>
                <input type="text" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} className="w-full px-3 py-2 border rounded" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Port</label>
                <input type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} className="w-full px-3 py-2 border rounded" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
                <input type="text" value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)} className="w-full px-3 py-2 border rounded" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
                <input type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder={hasExistingPassword ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (saved)" : "Enter password"} />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">From Address</label>
                <input type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} className="w-full px-3 py-2 border rounded" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Encryption</label>
                <select value={smtpEncryption} onChange={(e) => setSmtpEncryption(e.target.value)} className="w-full px-3 py-2 border rounded">
                  <option value="starttls">STARTTLS (port 587)</option>
                  <option value="ssl">SSL/TLS (port 465)</option>
                  <option value="none">None (port 25)</option>
                </select>
              </div>
            </div>
            <button onClick={handleSaveSmtp} className="mt-4 px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700">
              Save SMTP Settings
            </button>
            <div className="mt-4 pt-4 border-t border-gray-200">
              <h3 className="text-sm font-semibold text-gray-700 mb-2">Send Test Email</h3>
              <div className="flex gap-3">
                <input
                  type="email"
                  value={testEmailTo}
                  onChange={(e) => setTestEmailTo(e.target.value)}
                  className="flex-1 px-3 py-2 border rounded text-sm"
                  placeholder="recipient@example.com"
                />
                <button
                  onClick={async () => {
                    setError(""); setMessage("");
                    if (!testEmailTo) { setError("Enter a destination email address for the test"); return; }
                    try {
                      const result = await api.post<{ status: string; to: string; detail: string | null }>(
                        "/api/bootstrap/test-smtp", { to: testEmailTo }
                      );
                      if (result.status === "sent") { setMessage(`Test email sent to ${result.to}`); }
                      else { setError(result.detail || "Failed to send test email"); }
                    } catch { setError("Failed to send test email"); }
                  }}
                  className="px-4 py-2 border border-gray-300 rounded text-sm text-gray-700 hover:bg-gray-50"
                >
                  Send Test
                </button>
              </div>
            </div>
          </div>

          {/* Slack Integration */}
          <div className="bg-white rounded-lg shadow p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Slack Integration</h2>
              <a
                href="/settings/slack"
                className="text-sm text-bioaf-600 hover:text-bioaf-700"
              >
                Manage
              </a>
            </div>
            <div className="flex items-center gap-3">
              <div className={`w-3 h-3 rounded-full ${slackStatus?.connected ? "bg-green-500" : "bg-gray-300"}`} />
              <span className="text-sm text-gray-700">
                {slackStatus?.connected ? `Connected to ${slackStatus.team_name}` : "Not connected"}
              </span>
            </div>
            <div className="mt-3 flex gap-2">
              <button onClick={() => handleTestNotification("in_app")} className="text-xs text-gray-500 hover:text-gray-700">
                Test In-App
              </button>
              <button onClick={() => handleTestNotification("email")} className="text-xs text-gray-500 hover:text-gray-700">
                Test Email
              </button>
              {slackStatus?.connected && (
                <button onClick={() => handleTestNotification("slack")} className="text-xs text-gray-500 hover:text-gray-700">
                  Test Slack
                </button>
              )}
            </div>
          </div>

          {/* Platform Version & Upgrades */}
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

                {updateCheck.update_available && (
                  <div className="p-3 bg-blue-50 border border-blue-200 rounded text-sm text-blue-700">
                    A new version ({updateCheck.latest_version}) is available!
                    {updateCheck.release_url && (
                      <a href={updateCheck.release_url} target="_blank" rel="noopener noreferrer" className="ml-2 underline">
                        View release
                      </a>
                    )}
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
                  disabled={checkingUpdate}
                  className="text-sm text-bioaf-600 hover:text-bioaf-700 disabled:opacity-50"
                >
                  {checkingUpdate ? "Checking..." : "Check for updates"}
                </button>
              </div>
            )}

            {/* Upgrade History */}
            {upgradeHistory.length > 0 && (
              <div className="mt-6">
                <h3 className="text-sm font-semibold mb-2">Upgrade History</h3>
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
          </div>
        </main>
      </div>
    </div>
  );
}
