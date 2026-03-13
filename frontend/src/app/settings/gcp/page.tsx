"use client";

import { useEffect, useState } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";

interface GCPConfig {
  gcp_project_id: string | null;
  gcp_region: string | null;
  gcp_zone: string | null;
  org_slug: string | null;
  gcp_credentials_configured: boolean;
  gcp_validation_status: string | null;
  gcp_credential_source: string;
  gcp_service_account_email: string | null;
}

interface ValidationCheck {
  name: string;
  passed: boolean;
  message: string;
  status: string;
}

interface ValidationResult {
  passed: boolean;
  checks: ValidationCheck[];
}

const ORG_SLUG_RE = /^[a-z0-9][a-z0-9-]*[a-z0-9]$/;

function validateOrgSlug(slug: string): string | null {
  if (!slug) return null;
  if (slug.length < 3) return "Must be at least 3 characters";
  if (slug.length > 30) return "Must be at most 30 characters";
  if (slug.startsWith("-") || slug.endsWith("-")) return "Must not start or end with a hyphen";
  if (slug.includes("--")) return "Must not contain consecutive hyphens";
  if (!ORG_SLUG_RE.test(slug)) return "Must contain only lowercase letters, digits, and hyphens";
  return null;
}

const GCP_REGIONS = [
  "us-central1", "us-east1", "us-east4", "us-west1", "us-west2", "us-west3", "us-west4",
  "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6",
  "asia-east1", "asia-east2", "asia-northeast1", "asia-south1", "asia-southeast1",
];

const GCP_ZONES: Record<string, string[]> = {
  "us-central1": ["us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"],
  "us-east1": ["us-east1-b", "us-east1-c", "us-east1-d"],
  "us-east4": ["us-east4-a", "us-east4-b", "us-east4-c"],
  "us-west1": ["us-west1-a", "us-west1-b", "us-west1-c"],
};

function zonesForRegion(region: string): string[] {
  return GCP_ZONES[region] ?? [`${region}-a`, `${region}-b`, `${region}-c`];
}

const REQUIRED_APIS = [
  { name: "cloudresourcemanager.googleapis.com", description: "Cloud Resource Manager" },
  { name: "compute.googleapis.com", description: "Compute Engine" },
  { name: "container.googleapis.com", description: "Kubernetes Engine" },
  { name: "iam.googleapis.com", description: "Identity and Access Management" },
  { name: "secretmanager.googleapis.com", description: "Secret Manager" },
  { name: "servicenetworking.googleapis.com", description: "Service Networking" },
  { name: "serviceusage.googleapis.com", description: "Service Usage" },
  { name: "storage.googleapis.com", description: "Cloud Storage" },
  { name: "sqladmin.googleapis.com", description: "Cloud SQL Admin" },
  { name: "cloudbilling.googleapis.com", description: "Cloud Billing" },
  { name: "file.googleapis.com", description: "Filestore" },
];

const REQUIRED_ROLES = [
  "roles/container.admin",
  "roles/compute.admin",
  "roles/iam.serviceAccountUser",
  "roles/storage.admin",
  "roles/secretmanager.admin",
  "roles/cloudsql.admin",
  "roles/serviceusage.serviceUsageViewer",
];

export default function GcpSettingsPage() {
  const [projectId, setProjectId] = useState("");
  const [region, setRegion] = useState("us-central1");
  const [zone, setZone] = useState("us-central1-a");
  const [orgSlug, setOrgSlug] = useState("");
  const [credentialSource, setCredentialSource] = useState<"vm_default" | "service_account_key">("vm_default");
  const [serviceAccountKey, setServiceAccountKey] = useState("");
  const [serviceAccountEmail, setServiceAccountEmail] = useState("");

  const [orgSlugError, setOrgSlugError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showRequirements, setShowRequirements] = useState(false);

  useEffect(() => {
    api.get<GCPConfig>("/api/v1/settings/gcp").then((cfg) => {
      setProjectId(cfg.gcp_project_id ?? "");
      setRegion(cfg.gcp_region ?? "us-central1");
      setZone(cfg.gcp_zone ?? "us-central1-a");
      setOrgSlug(cfg.org_slug ?? "");
      setCredentialSource((cfg.gcp_credential_source as "vm_default" | "service_account_key") ?? "vm_default");
      setServiceAccountEmail(cfg.gcp_service_account_email ?? "");
    });
  }, []);

  const handleSave = async () => {
    setError("");
    setMessage("");

    const slugErr = orgSlug ? validateOrgSlug(orgSlug) : null;
    setOrgSlugError(slugErr);
    if (slugErr) return;

    setSaving(true);
    try {
      await api.put("/api/v1/settings/gcp", {
        gcp_project_id: projectId || undefined,
        gcp_region: region,
        gcp_zone: zone,
        org_slug: orgSlug || undefined,
        gcp_credential_source: credentialSource,
        service_account_key: credentialSource === "service_account_key" && serviceAccountKey
          ? serviceAccountKey
          : undefined,
        gcp_service_account_email: serviceAccountEmail || undefined,
      });
      setMessage("GCP configuration saved");
      setValidationResult(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save GCP configuration");
    } finally {
      setSaving(false);
    }
  };

  const handleValidate = async () => {
    setError("");
    setValidating(true);
    try {
      const result = await api.post<ValidationResult>("/api/v1/settings/gcp/validate");
      setValidationResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">GCP Configuration</h1>

          {message && (
            <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">
              {message}
            </div>
          )}
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
              {error}
            </div>
          )}

          <div className="bg-white rounded-lg shadow p-6 max-w-2xl space-y-5">
            {/* GCP Project ID */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">GCP Project ID</label>
              <input
                data-testid="gcp-project-id-input"
                type="text"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="w-full px-3 py-2 border rounded"
                placeholder="my-gcp-project"
              />
            </div>

            {/* Region */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Region</label>
              <select
                data-testid="gcp-region-select"
                value={region}
                onChange={(e) => {
                  setRegion(e.target.value);
                  setZone(zonesForRegion(e.target.value)[0]);
                }}
                className="w-full px-3 py-2 border rounded"
              >
                {GCP_REGIONS.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>

            {/* Zone */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Zone</label>
              <select
                data-testid="gcp-zone-select"
                value={zone}
                onChange={(e) => setZone(e.target.value)}
                className="w-full px-3 py-2 border rounded"
              >
                {zonesForRegion(region).map((z) => (
                  <option key={z} value={z}>{z}</option>
                ))}
              </select>
            </div>

            {/* Org Slug */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Organization Slug
                <span className="ml-1 text-gray-400 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
              </label>
              <input
                data-testid="org-slug-input"
                type="text"
                value={orgSlug}
                onChange={(e) => {
                  setOrgSlug(e.target.value);
                  setOrgSlugError(null);
                }}
                className={`w-full px-3 py-2 border rounded ${orgSlugError ? "border-red-400" : ""}`}
                placeholder="my-bioaf-org"
              />
              {orgSlugError && (
                <p data-testid="org-slug-error" className="mt-1 text-sm text-red-600">{orgSlugError}</p>
              )}
            </div>

            {/* Credential Source */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Authentication</label>
              <div className="space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="credential_source"
                    value="vm_default"
                    checked={credentialSource === "vm_default"}
                    onChange={() => setCredentialSource("vm_default")}
                  />
                  <span className="text-sm">VM default credentials (recommended for GCP-hosted deployments)</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="credential_source"
                    value="service_account_key"
                    checked={credentialSource === "service_account_key"}
                    onChange={() => setCredentialSource("service_account_key")}
                  />
                  <span className="text-sm">Service account key (JSON)</span>
                </label>
              </div>

              {credentialSource === "service_account_key" && (
                <div className="mt-3">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Service Account Key (JSON)</label>
                  <textarea
                    data-testid="service-account-key-input"
                    value={serviceAccountKey}
                    onChange={(e) => setServiceAccountKey(e.target.value)}
                    rows={6}
                    className="w-full px-3 py-2 border rounded font-mono text-xs"
                    placeholder='{"type": "service_account", ...}'
                  />
                </div>
              )}

              {credentialSource === "vm_default" && (
                <div className="mt-3">
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Service Account Email
                    <span className="ml-1 text-gray-400 font-normal text-xs">(optional -- for impersonation)</span>
                  </label>
                  <input
                    data-testid="service-account-email-input"
                    type="email"
                    value={serviceAccountEmail}
                    onChange={(e) => setServiceAccountEmail(e.target.value)}
                    className="w-full px-3 py-2 border rounded"
                    placeholder="bioaf-sa@my-project.iam.gserviceaccount.com"
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    If set, the VM default credentials will impersonate this service account.
                    Useful when the VM default SA lacks required scopes. The VM SA must have
                    the <code className="bg-gray-100 px-1 rounded">roles/iam.serviceAccountTokenCreator</code> role
                    on the target account.
                  </p>
                </div>
              )}
            </div>

            {/* Action buttons */}
            <div className="flex gap-3 pt-2">
              <button
                data-testid="save-gcp-config-btn"
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save Configuration"}
              </button>
              <button
                data-testid="validate-gcp-btn"
                onClick={handleValidate}
                disabled={validating}
                className="px-4 py-2 border border-gray-300 rounded text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {validating ? "Validating..." : "Validate Connection"}
              </button>
            </div>
          </div>

          {/* Validation results */}
          {validationResult && (
            <div
              data-testid="validation-results"
              className="mt-6 bg-white rounded-lg shadow p-6 max-w-2xl"
            >
              <h2 className="text-lg font-semibold mb-4">
                Validation {validationResult.passed ? (
                  <span className="text-green-600">Passed</span>
                ) : (
                  <span className="text-red-600">Failed</span>
                )}
              </h2>
              <ul className="space-y-2">
                {validationResult.checks.map((check) => (
                  <li key={check.name} className="flex items-start gap-2 text-sm">
                    <span className={`mt-0.5 ${check.passed ? "text-green-600" : check.status === "skipped" ? "text-gray-400" : "text-red-600"}`}>
                      {check.passed ? "\u2713" : check.status === "skipped" ? "\u2013" : "\u2717"}
                    </span>
                    <div>
                      <span className="font-medium">{check.name}</span>
                      {check.message && <span className="ml-2 text-gray-500">{check.message}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Requirements guidance */}
          <div className="mt-6 max-w-2xl">
            <button
              data-testid="toggle-requirements-btn"
              onClick={() => setShowRequirements(!showRequirements)}
              className="text-sm text-bioaf-600 hover:text-bioaf-700 font-medium"
            >
              {showRequirements ? "Hide" : "Show"} GCP project requirements
            </button>

            {showRequirements && (
              <div data-testid="requirements-section" className="mt-3 bg-white rounded-lg shadow p-6 space-y-4">
                <h2 className="text-lg font-semibold">GCP Project Requirements</h2>

                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">Required APIs</h3>
                  <p className="text-xs text-gray-500 mb-2">
                    Enable these APIs in the GCP Console under APIs &amp; Services, or run:
                  </p>
                  <pre className="bg-gray-50 border rounded p-3 text-xs overflow-x-auto mb-2">
{`gcloud services enable \\
  ${REQUIRED_APIS.map(a => a.name).join(" \\\n  ")} \\
  --project=YOUR_PROJECT_ID`}
                  </pre>
                  <ul className="text-xs text-gray-600 space-y-1">
                    {REQUIRED_APIS.map((a) => (
                      <li key={a.name}>
                        <code className="bg-gray-100 px-1 rounded">{a.name}</code>
                        <span className="ml-1 text-gray-400">-- {a.description}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">Required IAM Roles</h3>
                  <p className="text-xs text-gray-500 mb-2">
                    The service account used by bioAF needs the following roles. Grant them with:
                  </p>
                  <pre className="bg-gray-50 border rounded p-3 text-xs overflow-x-auto mb-2">
{`SA_EMAIL="your-sa@your-project.iam.gserviceaccount.com"
PROJECT_ID="your-project-id"

${REQUIRED_ROLES.map(r => `gcloud projects add-iam-policy-binding $PROJECT_ID \\
  --member="serviceAccount:$SA_EMAIL" \\
  --role="${r}"`).join("\n\n")}`}
                  </pre>
                  <ul className="text-xs text-gray-600 space-y-1">
                    {REQUIRED_ROLES.map((r) => (
                      <li key={r}>
                        <code className="bg-gray-100 px-1 rounded">{r}</code>
                      </li>
                    ))}
                  </ul>
                </div>

                <div>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2">VM Default Credentials with Impersonation</h3>
                  <p className="text-xs text-gray-500">
                    If your VM default service account does not have the required scopes or roles,
                    create a dedicated service account with the roles above, then set its email in
                    the &quot;Service Account Email&quot; field. The VM default SA will impersonate it.
                    The VM SA needs the <code className="bg-gray-100 px-1 rounded">roles/iam.serviceAccountTokenCreator</code> role
                    on the target service account.
                  </p>
                  <pre className="bg-gray-50 border rounded p-3 text-xs overflow-x-auto mt-2">
{`# Create dedicated SA
gcloud iam service-accounts create bioaf-sa \\
  --display-name="bioAF Service Account" \\
  --project=YOUR_PROJECT_ID

# Grant required roles to the new SA
# (use the IAM role commands above)

# Allow VM default SA to impersonate it
gcloud iam service-accounts add-iam-policy-binding \\
  bioaf-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \\
  --member="serviceAccount:VM_DEFAULT_SA@YOUR_PROJECT_ID.iam.gserviceaccount.com" \\
  --role="roles/iam.serviceAccountTokenCreator"`}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
