"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

interface SmtpSettings {
  host: string;
  port: number;
  username: string;
  password: string;
  from_address: string;
  encryption: string;
  configured: boolean;
}

export function SmtpSettingsContent() {
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
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    const loadSettings = async () => {
      try {
        const data = await api.get<SmtpSettings>("/api/bootstrap/smtp-settings");
        setSmtpHost(data.host);
        setSmtpPort(String(data.port));
        setSmtpUsername(data.username);
        // Don't populate the masked password into the field
        setSmtpPassword("");
        setHasExistingPassword(!!data.password);
        setSmtpFrom(data.from_address);
        setSmtpEncryption(data.encryption);
      } catch (e) {
        // The old body here was `// Settings not configured yet`, which is true of a
        // 404 and of nothing else. Every other failure -- a 500, a dropped connection
        // -- also landed here, and the form then rendered blank with port 587 and
        // starttls, which are this component's defaults rather than the org's. Save
        // posts host, username and from_address unconditionally, so one click wrote
        // empty strings over a working mail configuration and silently broke invites,
        // password resets and notification email. That is the same defect the password
        // field above already carries a fix for; the other four fields did not have one.
        //
        // The status is read off the error rather than through `instanceof ApiError`,
        // because the class identity does not survive a jest module mock and the status
        // is the only part of it this decision needs.
        if ((e as { status?: number } | null)?.status === 404) {
          // Genuinely nothing stored yet: a first-run instance keeps an editable form.
        } else {
          logError("loading the SMTP settings", e);
          setLoadFailed(true);
        }
      } finally {
        setLoading(false);
      }
    };
    loadSettings();
  }, []);

  const handleSaveSmtp = async () => {
    if (loadFailed) return;
    setError("");
    setMessage("");
    try {
      await api.post("/api/bootstrap/configure-smtp", {
        host: smtpHost,
        port: parseInt(smtpPort),
        username: smtpUsername,
        // Omit the password unless the admin actually typed a new one. The load
        // path deliberately leaves this field empty (the API returns the password
        // masked, so there is nothing real to populate it with), and sending that
        // empty string overwrote the stored credential: a save that only changed
        // the host silently broke invites, password resets, and notification email.
        ...(smtpPassword ? { password: smtpPassword } : {}),
        from_address: smtpFrom,
        encryption: smtpEncryption,
      });
      setMessage("SMTP configuration saved");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save SMTP settings");
    }
  };

  const handleTestEmail = async () => {
    setError("");
    setMessage("");
    if (!testEmailTo) {
      setError("Enter a destination email address for the test");
      return;
    }
    try {
      const result = await api.post<{ status: string; to: string; detail: string | null }>(
        "/api/bootstrap/test-smtp",
        { to: testEmailTo }
      );
      if (result.status === "sent") {
        setMessage(`Test email sent to ${result.to}`);
      } else {
        setError(result.detail || "Failed to send test email");
      }
    } catch {
      setError("Failed to send test email");
    }
  };

  if (loading) {
    return <p className="text-gray-500">Loading...</p>;
  }

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">SMTP Configuration</h1>

      {loadFailed && (
        <div
          data-testid="smtp-load-failed"
          role="status"
          className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm"
        >
          {loadFailureMessage("The stored SMTP settings")} Saving is disabled until they load,
          because saving would write these fields over settings that were never read.
        </div>
      )}
      {message && <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">{message}</div>}
      {error && <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">{error}</div>}

      <Card className="max-w-2xl">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="host" className="block text-sm font-medium text-gray-700 mb-1">Host</label>
            <input id="host" type="text" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder="smtp.example.com" />
          </div>
          <div>
            <label htmlFor="port" className="block text-sm font-medium text-gray-700 mb-1">Port</label>
            <input id="port" type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} className="w-full px-3 py-2 border rounded" />
          </div>
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">Username</label>
            <input id="username" type="text" value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)} className="w-full px-3 py-2 border rounded" />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input id="password" type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder={hasExistingPassword ? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (saved)" : "Enter password"} />
            {hasExistingPassword && (
              <p className="text-xs text-gray-500 mt-1">
                A password is saved. Leave blank to keep it, or type a new one to replace it.
              </p>
            )}
          </div>
          <div>
            <label htmlFor="from-address" className="block text-sm font-medium text-gray-700 mb-1">From Address</label>
            <input id="from-address" type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder="noreply@example.com" />
          </div>
          <div>
            <label htmlFor="encryption" className="block text-sm font-medium text-gray-700 mb-1">Encryption</label>
            <select id="encryption" value={smtpEncryption} onChange={(e) => setSmtpEncryption(e.target.value)} className="w-full px-3 py-2 border rounded">
              <option value="starttls">STARTTLS (port 587)</option>
              <option value="ssl">SSL/TLS (port 465)</option>
              <option value="none">None (port 25)</option>
            </select>
          </div>
        </div>
        <div className="mt-6">
          <Button
            onClick={handleSaveSmtp}
            disabled={loadFailed}>
            Save SMTP Settings
          </Button>
        </div>

        {/* Test Email */}
        <div className="mt-6 pt-6 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">Send Test Email</h3>
          <div className="flex gap-3">
            <input aria-label="Test email recipient"
              type="email"
              value={testEmailTo}
              onChange={(e) => setTestEmailTo(e.target.value)}
              className="flex-1 px-3 py-2 border rounded"
              placeholder="recipient@example.com"
            />
            <button onClick={handleTestEmail} className="px-4 py-2 border border-gray-300 rounded text-gray-700 hover:bg-gray-50">
              Send Test Email
            </button>
          </div>
        </div>
      </Card>
    </>
  );
}
