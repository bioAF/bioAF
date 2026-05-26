"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface NetworkingConfig {
  hostname: string;
  domain: string;
  fqdn: string;
  reachability_status: string;
  reachability_checked_at: string | null;
  cert_status: string;
  https_enforced: boolean;
}

interface ReachabilityResult {
  fqdn: string;
  status: string;
  detail: string;
  checked_at: string;
}

interface CertificateStatus {
  fqdn: string;
  status: string;
}

const HOSTNAME_LABEL_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;
const DOMAIN_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;

function validateHostname(v: string): string | null {
  if (!v) return "Required";
  if (v.length > 63) return "Max 63 characters";
  if (!HOSTNAME_LABEL_RE.test(v)) {
    return "Lowercase letters, digits, hyphens; no leading or trailing hyphen";
  }
  return null;
}

function validateDomain(v: string): string | null {
  if (!v) return "Required";
  if (v.length > 253) return "Max 253 characters";
  if (!DOMAIN_RE.test(v)) {
    return "Two or more lowercase labels joined by dots (e.g. acme.com)";
  }
  return null;
}

function StatusPill({ value, tone }: { value: string; tone: "ok" | "warn" | "bad" | "muted" }) {
  const colors: Record<string, string> = {
    ok: "bg-green-100 text-green-800",
    warn: "bg-yellow-100 text-yellow-800",
    bad: "bg-red-100 text-red-800",
    muted: "bg-gray-100 text-gray-700",
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colors[tone]}`}>
      {value}
    </span>
  );
}

const REACHABILITY_LABELS: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  reachable: { label: "Reachable", tone: "ok" },
  dns_failed: { label: "DNS resolution failed", tone: "bad" },
  connection_refused: { label: "Connection refused", tone: "bad" },
  timeout: { label: "Timed out", tone: "bad" },
  tls_error: { label: "TLS error", tone: "warn" },
  bad_response: { label: "Unexpected response", tone: "bad" },
  wrong_instance: { label: "Wrong instance", tone: "bad" },
  unreachable: { label: "Unreachable", tone: "bad" },
};

function describeReachability(status: string): { label: string; tone: "ok" | "warn" | "bad" } {
  return REACHABILITY_LABELS[status] ?? { label: status, tone: "bad" };
}

export function NetworkingSettingsContent() {
  const [config, setConfig] = useState<NetworkingConfig | null>(null);
  const [hostname, setHostname] = useState("");
  const [domain, setDomain] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [pollingCert, setPollingCert] = useState(false);
  const [certCheckedAt, setCertCheckedAt] = useState<Date | null>(null);
  const [certFlash, setCertFlash] = useState(false);
  const [applyingHttps, setApplyingHttps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reachabilityResult, setReachabilityResult] = useState<ReachabilityResult | null>(null);

  useEffect(() => {
    api
      .get<NetworkingConfig>("/api/v1/settings/networking")
      .then((cfg) => {
        setConfig(cfg);
        setHostname(cfg.hostname);
        setDomain(cfg.domain);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const hostnameError = hostname ? validateHostname(hostname) : null;
  const domainError = domain ? validateDomain(domain) : null;
  const previewFqdn = hostname && domain ? `${hostname}.${domain}` : "";
  const canSave = !!hostname && !!domain && !hostnameError && !domainError && !saving;

  const reachable = config?.reachability_status === "reachable";
  const certActive = config?.cert_status === "active";
  const certProvisioning = config?.cert_status === "provisioning";

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.put<NetworkingConfig>("/api/v1/settings/networking", {
        hostname,
        domain,
      });
      setConfig(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function runReachabilityTest() {
    setTesting(true);
    setError(null);
    try {
      const result = await api.post<ReachabilityResult>(
        "/api/v1/settings/networking/reachability-test",
      );
      setReachabilityResult(result);
      const refreshed = await api.get<NetworkingConfig>("/api/v1/settings/networking");
      setConfig(refreshed);
    } catch (e) {
      setError(String(e));
    } finally {
      setTesting(false);
    }
  }

  async function requestCertificate() {
    setRequesting(true);
    setError(null);
    try {
      const result = await api.post<CertificateStatus>(
        "/api/v1/settings/networking/certificate",
      );
      const refreshed = await api.get<NetworkingConfig>("/api/v1/settings/networking");
      setConfig({ ...refreshed, cert_status: result.status });
    } catch (e) {
      setError(String(e));
    } finally {
      setRequesting(false);
    }
  }

  async function pollCertStatus() {
    setPollingCert(true);
    setCertFlash(false);
    setError(null);
    try {
      const result = await api.get<CertificateStatus>(
        "/api/v1/settings/networking/certificate/status",
      );
      const refreshed = await api.get<NetworkingConfig>("/api/v1/settings/networking");
      setConfig({ ...refreshed, cert_status: result.status });
      setCertCheckedAt(new Date());
      setCertFlash(true);
      window.setTimeout(() => setCertFlash(false), 900);
    } catch (e) {
      setError(String(e));
    } finally {
      setPollingCert(false);
    }
  }

  async function applyHttps() {
    setApplyingHttps(true);
    setError(null);
    try {
      await api.post("/api/v1/settings/networking/enforce-https", { enabled: true });
      const refreshed = await api.get<NetworkingConfig>("/api/v1/settings/networking");
      setConfig(refreshed);
    } catch (e) {
      setError(String(e));
    } finally {
      setApplyingHttps(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-1">Networking</h1>
        <p className="text-sm text-gray-600">
          Configure the public hostname, verify it routes to this bioAF instance, request a
          TLS certificate, then enforce HTTPS. DNS is managed at your provider, outside bioAF.
        </p>
      </div>

      {error && (
        <div className="p-3 rounded border border-red-300 bg-red-50 text-red-800 text-sm">
          {error}
        </div>
      )}

      <section
        data-testid="networking-hostname-card"
        className="border rounded-lg p-4 bg-white"
      >
        <h2 className="font-semibold mb-3">1. Hostname and domain</h2>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm text-gray-700">Hostname</span>
            <input
              data-testid="hostname-input"
              className="mt-1 w-full border rounded px-2 py-1.5 font-mono"
              value={hostname}
              placeholder="app"
              onChange={(e) => setHostname(e.target.value)}
            />
            {hostnameError && (
              <span className="text-xs text-red-700">{hostnameError}</span>
            )}
          </label>
          <label className="block">
            <span className="text-sm text-gray-700">Domain</span>
            <input
              data-testid="domain-input"
              className="mt-1 w-full border rounded px-2 py-1.5 font-mono"
              value={domain}
              placeholder="acme.com"
              onChange={(e) => setDomain(e.target.value)}
            />
            {domainError && <span className="text-xs text-red-700">{domainError}</span>}
          </label>
        </div>
        <div className="mt-3 text-sm">
          <span className="text-gray-600">FQDN preview: </span>
          <code data-testid="fqdn-preview" className="font-mono">
            {previewFqdn || "-"}
          </code>
        </div>
        <div className="mt-4">
          <button
            data-testid="save-hostname-button"
            disabled={!canSave}
            onClick={save}
            className="px-3 py-1.5 bg-bioaf-700 text-white rounded disabled:bg-gray-300"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </section>

      <section
        data-testid="networking-reachability-card"
        className="border rounded-lg p-4 bg-white"
      >
        <h2 className="font-semibold mb-2">2. Reachability test</h2>
        <p className="text-sm text-gray-600 mb-3">
          Point a DNS A record for{" "}
          <code className="font-mono">{config?.fqdn || previewFqdn || "<fqdn>"}</code> at this
          instance&apos;s public IP, then run the test. bioAF will write a one-time nonce and call
          itself at the public FQDN to confirm the request comes back here.
        </p>
        <div className="flex items-center gap-3">
          <button
            data-testid="test-reachability-button"
            disabled={!config?.fqdn || testing}
            onClick={runReachabilityTest}
            className="px-3 py-1.5 bg-bioaf-700 text-white rounded disabled:bg-gray-300"
          >
            {testing ? "Testing..." : "Test reachability"}
          </button>
          {config?.reachability_status && (
            <div data-testid="reachability-status">
              {(() => {
                const d = describeReachability(config.reachability_status);
                return <StatusPill value={d.label} tone={d.tone} />;
              })()}
            </div>
          )}
        </div>
        {reachabilityResult?.detail && (
          <p data-testid="reachability-detail" className="mt-3 text-sm text-gray-800 bg-gray-50 border border-gray-200 rounded px-3 py-2">
            {reachabilityResult.detail}
          </p>
        )}
      </section>

      <section data-testid="networking-tls-card" className="border rounded-lg p-4 bg-white">
        <h2 className="font-semibold mb-2">3. TLS certificate and HTTPS</h2>
        <p className="text-sm text-gray-600 mb-3">
          Request a Google-managed certificate for the verified FQDN, then enforce HTTPS once
          it goes Active. Provisioning typically takes 15-60 minutes after the first request.
        </p>

        <div className="flex items-center gap-3 mb-4">
          <button
            data-testid="request-certificate-button"
            disabled={!reachable || requesting}
            onClick={requestCertificate}
            className="px-3 py-1.5 bg-bioaf-700 text-white rounded disabled:bg-gray-300"
          >
            {requesting ? "Requesting..." : "Request certificate"}
          </button>
          {config?.cert_status && (
            <div
              data-testid="cert-status"
              className={`inline-flex transition-shadow duration-300 rounded ${
                certFlash ? "ring-2 ring-bioaf-400" : "ring-0"
              }`}
            >
              {certActive && <StatusPill value="active" tone="ok" />}
              {certProvisioning && <StatusPill value="provisioning" tone="warn" />}
              {config.cert_status === "failed" && (
                <StatusPill value="failed" tone="bad" />
              )}
              {config.cert_status === "not_requested" && (
                <StatusPill value="not requested" tone="muted" />
              )}
            </div>
          )}
          {certProvisioning && (
            <button
              data-testid="refresh-cert-status-button"
              onClick={pollCertStatus}
              disabled={pollingCert}
              className="text-sm text-bioaf-700 underline disabled:text-gray-400 disabled:no-underline"
            >
              {pollingCert ? "Refreshing..." : "Refresh status"}
            </button>
          )}
          {certCheckedAt && (
            <span
              data-testid="cert-last-checked"
              className="text-xs text-gray-500"
            >
              Last checked {certCheckedAt.toLocaleTimeString()}
            </span>
          )}
        </div>

        <div className="border-t pt-3">
          <p
            data-testid="https-warning"
            className="text-sm text-yellow-900 bg-yellow-50 border border-yellow-200 rounded px-3 py-2 mb-2"
          >
            Applying HTTPS enforcement will redirect all HTTP traffic to HTTPS and restart the
            backend and frontend pods. All users will be logged out. Confirm that any external
            OAuth or SSO clients have the new <code>https://&lt;fqdn&gt;/auth/callback</code>{" "}
            URL configured before applying.
          </p>
          <button
            data-testid="apply-https-button"
            disabled={!certActive || applyingHttps}
            onClick={applyHttps}
            className="px-3 py-1.5 bg-bioaf-700 text-white rounded disabled:bg-gray-300"
          >
            {applyingHttps ? "Applying..." : "Apply HTTPS and restart"}
          </button>
          {config?.https_enforced && (
            <span data-testid="https-enforced-indicator" className="ml-3 text-sm text-green-800">
              HTTPS is enforced.
            </span>
          )}
        </div>
      </section>
    </div>
  );
}
