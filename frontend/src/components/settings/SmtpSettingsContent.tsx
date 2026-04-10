"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

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
      } catch {
        // Settings not configured yet
      } finally {
        setLoading(false);
      }
    };
    loadSettings();
  }, []);

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

      {message && <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">{message}</div>}
      {error && <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">{error}</div>}

      <div className="bg-white rounded-lg shadow p-6 max-w-2xl">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Host</label>
            <input type="text" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder="smtp.example.com" />
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
            <input type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)} className="w-full px-3 py-2 border rounded" placeholder="noreply@example.com" />
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
        <div className="mt-6">
          <button onClick={handleSaveSmtp} className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700">
            Save SMTP Settings
          </button>
        </div>

        {/* Test Email */}
        <div className="mt-6 pt-6 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">Send Test Email</h3>
          <div className="flex gap-3">
            <input
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
      </div>
    </>
  );
}
