"use client";

/**
 * Upload a certificate from the organization's own certificate authority (plan 03.5A), for private
 * installations that no public CA can issue for. The backend checks the pair and the host installs
 * it for both bioAF and notebooks. Proposed wording, pending owner sign-off.
 */
import { useState } from "react";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

interface UploadResult {
  status: string;
  names: string[];
  not_after: string;
  issuer: string;
}

export function OrganizationCertificateUpload() {
  const [certificate, setCertificate] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  async function upload() {
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const uploaded = await api.post<UploadResult>("/api/v1/settings/networking/certificate/upload", {
        certificate_pem: certificate,
        private_key_pem: privateKey,
      });
      setResult(uploaded);
      setPrivateKey("");
    } catch (e) {
      logError("uploading the organization certificate", e);
      setError(e instanceof Error ? e.message : "The certificate could not be uploaded. Try again.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card title="Certificate from your organization" padding="sm">
      <p className="text-sm text-gray-600 mb-3">
        For a private installation, upload a server certificate from your organization&apos;s certificate
        authority. It must name the address you use for bioAF. It is used for bioAF and for notebooks.
      </p>
      <label className="block text-sm font-medium mb-1" htmlFor="org-cert-pem">
        Server certificate (PEM)
      </label>
      <textarea
        id="org-cert-pem"
        className="w-full border rounded p-2 font-mono text-xs mb-3"
        rows={5}
        value={certificate}
        onChange={(e) => setCertificate(e.target.value)}
      />
      <label className="block text-sm font-medium mb-1" htmlFor="org-key-pem">
        Private key (PEM)
      </label>
      <textarea
        id="org-key-pem"
        className="w-full border rounded p-2 font-mono text-xs mb-3"
        rows={5}
        autoComplete="off"
        spellCheck={false}
        value={privateKey}
        onChange={(e) => setPrivateKey(e.target.value)}
      />
      <Button
        size="sm"
        onClick={upload}
        disabled={!certificate.trim() || !privateKey.trim()}
        busy={uploading}
        busyLabel="Uploading..."
      >
        Upload certificate
      </Button>
      {error && <p className="text-sm text-red-700 mt-2">{error}</p>}
      {result && (
        <p className="text-sm text-gray-700 mt-2">
          Installing the certificate for {result.names.join(", ")}. Reload this page in a minute to use it.
        </p>
      )}
    </Card>
  );
}
