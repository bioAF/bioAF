"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { setToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STEPS = [
  "Setup Code",
  "Create Admin Account",
  "Organization Name",
  "GCP Credentials",
  "SMTP Settings",
  "Infrastructure",
  "Select Stack",
  "Deploying",
  "Getting Started",
];

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
  return GCP_ZONES[region] ?? [`${region}-b`, `${region}-c`, `${region}-d`];
}

const SETUP_RECOMMENDED_ROLES = [
  { role: "roles/storage.admin", description: "Storage Admin" },
  { role: "roles/pubsub.admin", description: "Pub/Sub Admin" },
  { role: "roles/container.admin", description: "Kubernetes Engine Admin" },
  { role: "roles/iam.serviceAccountUser", description: "Service Account User" },
  { role: "roles/compute.admin", description: "Compute Admin" },
  { role: "roles/resourcemanager.projectIamAdmin", description: "Project IAM Admin" },
  { role: "roles/bigquery.dataEditor", description: "BigQuery Data Editor" },
  { role: "roles/artifactregistry.admin", description: "Artifact Registry Admin" },
  { role: "roles/cloudbuild.builds.editor", description: "Cloud Build Editor" },
  { role: "roles/serviceusage.serviceUsageViewer", description: "Service Usage Viewer" },
  { role: "roles/viewer", description: "Viewer" },
];

const SETUP_REQUIRED_APIS = [
  "cloudresourcemanager.googleapis.com",
  "compute.googleapis.com",
  "container.googleapis.com",
  "iam.googleapis.com",
  "secretmanager.googleapis.com",
  "servicenetworking.googleapis.com",
  "serviceusage.googleapis.com",
  "pubsub.googleapis.com",
  "storage.googleapis.com",
  "sqladmin.googleapis.com",
  "cloudbilling.googleapis.com",
  "bigquery.googleapis.com",
  "artifactregistry.googleapis.com",
  "cloudbuild.googleapis.com",
];

interface SetupWizardProps {
  onComplete: () => void;
}

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");

  // Step 0: Setup code
  const [setupCode, setSetupCode] = useState("");
  const [setupToken, setSetupToken] = useState("");

  // Step 1: Admin creation
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [name, setName] = useState("");

  // Step 2: Org name
  const [orgName, setOrgName] = useState("");

  // Step 3: GCP
  const [gcpProjectId, setGcpProjectId] = useState("");
  const [gcpRegion, setGcpRegion] = useState("us-central1");
  const [gcpZone, setGcpZone] = useState("us-central1-a");
  const [gcpOrgSlug, setGcpOrgSlug] = useState("");
  const [gcpCredentialSource, setGcpCredentialSource] = useState<"vm_default" | "service_account_key">("vm_default");
  const [gcpServiceAccountKey, setGcpServiceAccountKey] = useState("");
  const [gcpServiceAccountEmail, setGcpServiceAccountEmail] = useState("");
  const [gcpSaving, setGcpSaving] = useState(false);
  const [gcpConfigured, setGcpConfigured] = useState(false);
  const [gcpValidation, setGcpValidation] = useState<{
    passed: boolean;
    checks: { name: string; passed: boolean; message: string }[];
    permission_details: { permission: string; granted: boolean; recommended_role: string }[];
  } | null>(null);

  // Step 4: SMTP
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpFrom, setSmtpFrom] = useState("");

  // Step 6: Compute stack
  const [computeStack, setComputeStack] = useState("kubernetes");
  const [stackDeploying, setStackDeploying] = useState(false);

  // --- Handlers ---

  const handleVerifyCode = async () => {
    setError("");
    try {
      // Use raw fetch since the api module auto-redirects on 401
      const resp = await fetch(`${API_URL}/api/bootstrap/verify-setup-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: setupCode }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: "Verification failed" }));
        setError(data.detail || "Invalid or expired setup code");
        return;
      }
      const data = await resp.json();
      setSetupToken(data.setup_token);
      setStep(1);
    } catch {
      setError("Failed to verify setup code");
    }
  };

  const handleCreateAdmin = async () => {
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setError("");
    try {
      // Use raw fetch with setup token (not the stored auth token)
      const resp = await fetch(`${API_URL}/api/bootstrap/create-admin`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${setupToken}`,
        },
        body: JSON.stringify({ email, password, name: name || undefined }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({ detail: "Failed to create admin" }));
        setError(data.detail || "Failed to create admin");
        return;
      }
      const data = await resp.json();
      setToken(data.access_token);
      setStep(2);
    } catch {
      setError("Failed to create admin");
    }
  };

  const handleConfigureOrg = async () => {
    setError("");
    try {
      await api.post("/api/bootstrap/configure-org", { org_name: orgName });
      setStep(3);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure org");
    }
  };

  const handleSaveGcp = async () => {
    setError("");
    setGcpValidation(null);
    setGcpSaving(true);
    try {
      await api.put("/api/v1/settings/gcp", {
        gcp_project_id: gcpProjectId || undefined,
        gcp_region: gcpRegion,
        gcp_zone: gcpZone,
        org_slug: gcpOrgSlug || undefined,
        gcp_credential_source: gcpCredentialSource,
        service_account_key:
          gcpCredentialSource === "service_account_key" && gcpServiceAccountKey
            ? gcpServiceAccountKey
            : undefined,
        gcp_service_account_email: gcpServiceAccountEmail || undefined,
      });
      const result = await api.post<typeof gcpValidation>("/api/v1/settings/gcp/validate");
      setGcpValidation(result);
      if (result?.passed) {
        setGcpConfigured(true);
        setStep(4);
      } else {
        setError("Validation failed. Fix the issues below and try again.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save GCP configuration");
    } finally {
      setGcpSaving(false);
    }
  };

  const handleConfigureSmtp = async () => {
    setError("");
    try {
      await api.post("/api/bootstrap/configure-smtp", {
        host: smtpHost,
        port: parseInt(smtpPort),
        username: smtpUsername,
        password: smtpPassword,
        from_address: smtpFrom,
      });
      setStep(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure SMTP");
    }
  };

  const handleSetupInfrastructure = () => {
    setStep(6);
  };

  const handleDoInfraLater = async () => {
    try {
      await api.post("/api/bootstrap/complete");
    } catch {
      // Non-critical
    }
    setStep(8);
  };

  const handleSelectStack = async () => {
    setError("");
    setStackDeploying(true);
    try {
      await api.post("/api/v1/infrastructure/terraform/init");
      try {
        await api.post("/api/v1/infrastructure/stack/deploy-background", {
          stack_type: computeStack,
        });
      } catch {
        // Deployment may fail; user can retry from Infrastructure page
      }
      try {
        await api.post("/api/bootstrap/complete");
      } catch {
        // Non-critical
      }
      setStep(7);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to initialize infrastructure");
    } finally {
      setStackDeploying(false);
    }
  };

  return (
    <div className="bg-white shadow rounded-lg p-8">
      {/* Step indicator */}
      <div className="flex items-center justify-between mb-8">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                i === step
                  ? "bg-bioaf-600 text-white"
                  : i < step
                    ? "bg-green-500 text-white"
                    : "bg-gray-200 text-gray-500"
              }`}
            >
              {i < step ? "\u2713" : i + 1}
            </div>
            {i < STEPS.length - 1 && (
              <div className={`w-8 h-0.5 ${i < step ? "bg-green-500" : "bg-gray-200"}`} />
            )}
          </div>
        ))}
      </div>

      <h2 className="text-xl font-semibold mb-4">{STEPS[step]}</h2>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {/* Step 0: Setup Code */}
      {step === 0 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Enter the 6-character setup code shown in your terminal after running{" "}
            <code className="bg-gray-100 px-1 rounded">./bioaf setup</code>.
          </p>
          <div>
            <label htmlFor="setup-code" className="block text-sm font-medium text-gray-700 mb-1">
              Setup Code
            </label>
            <input
              id="setup-code"
              type="text"
              value={setupCode}
              onChange={(e) => setSetupCode(e.target.value.toUpperCase())}
              placeholder="Enter 6-character code"
              maxLength={6}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500 font-mono text-lg tracking-widest text-center"
            />
          </div>
          <button
            onClick={handleVerifyCode}
            disabled={setupCode.length !== 6}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            Verify
          </button>
        </div>
      )}

      {/* Step 1: Create Admin Account */}
      {step === 1 && (
        <div className="space-y-4">
          <div>
            <label htmlFor="setup-name" className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input id="setup-name" type="text" value={name} onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>
          <div>
            <label htmlFor="setup-email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input id="setup-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
          </div>
          <div>
            <label htmlFor="setup-password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input id="setup-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
          </div>
          <div>
            <label htmlFor="setup-confirm-password" className="block text-sm font-medium text-gray-700 mb-1">Confirm Password</label>
            <input id="setup-confirm-password" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
          </div>
          <button onClick={handleCreateAdmin} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Create Admin Account
          </button>
        </div>
      )}

      {/* Step 2: Organization Name */}
      {step === 2 && (
        <div className="space-y-4">
          <div>
            <label htmlFor="setup-org-name" className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
            <input id="setup-org-name" type="text" value={orgName} onChange={(e) => setOrgName(e.target.value)}
              placeholder="e.g., Acme Biotech"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" required />
          </div>
          <button onClick={handleConfigureOrg} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Save Organization Name
          </button>
        </div>
      )}

      {/* Step 3: GCP Credentials */}
      {step === 3 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-2">
            Configure your Google Cloud Platform project. Credentials are required before
            deploying a compute stack.
          </p>

          <details data-testid="gcp-prerequisites" className="bg-gray-50 border rounded p-4">
            <summary className="cursor-pointer text-sm font-semibold text-gray-700 select-none">
              Prerequisites: IAM Roles &amp; APIs
              <span className="ml-1 text-xs font-normal text-gray-400">
                ({SETUP_RECOMMENDED_ROLES.length} roles, {SETUP_REQUIRED_APIS.length} APIs)
              </span>
            </summary>
            <div className="mt-3 space-y-3">
              <div>
                <p className="text-xs font-medium text-gray-600 mb-1">Required IAM roles for your service account:</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                  {SETUP_RECOMMENDED_ROLES.map(({ role, description }) => (
                    <div key={role} className="flex items-center gap-1.5 text-xs">
                      <code className="bg-white px-1 py-0.5 rounded text-gray-800 border">{role}</code>
                      <span className="text-gray-400">{description}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-xs font-medium text-gray-600 mb-1">Required GCP APIs to enable:</p>
                <pre className="bg-white border rounded p-2 text-xs overflow-x-auto">
{`gcloud services enable \\
  ${SETUP_REQUIRED_APIS.join(" \\\n  ")} \\
  --project=YOUR_PROJECT_ID`}
                </pre>
              </div>
            </div>
          </details>

          <div>
            <label htmlFor="gcp-project-id" className="block text-sm font-medium text-gray-700 mb-1">GCP Project ID</label>
            <input id="gcp-project-id" type="text" value={gcpProjectId}
              onChange={(e) => setGcpProjectId(e.target.value)} placeholder="my-gcp-project"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="gcp-region" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
              <select id="gcp-region" value={gcpRegion}
                onChange={(e) => { setGcpRegion(e.target.value); setGcpZone(zonesForRegion(e.target.value)[0]); }}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500">
                {GCP_REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="gcp-zone" className="block text-sm font-medium text-gray-700 mb-1">Zone</label>
              <select id="gcp-zone" value={gcpZone} onChange={(e) => setGcpZone(e.target.value)}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500">
                {zonesForRegion(gcpRegion).map((z) => <option key={z} value={z}>{z}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="gcp-org-slug" className="block text-sm font-medium text-gray-700 mb-1">
              Organization Slug
              <span className="ml-1 text-gray-400 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
            </label>
            <input id="gcp-org-slug" type="text" value={gcpOrgSlug} onChange={(e) => setGcpOrgSlug(e.target.value)}
              placeholder="my-bioaf-org" className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Authentication</label>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="gcp_credential_source" value="vm_default"
                  checked={gcpCredentialSource === "vm_default"} onChange={() => setGcpCredentialSource("vm_default")} />
                <span className="text-sm">VM default credentials</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="radio" name="gcp_credential_source" value="service_account_key"
                  checked={gcpCredentialSource === "service_account_key"} onChange={() => setGcpCredentialSource("service_account_key")} />
                <span className="text-sm">Service account key (JSON)</span>
              </label>
            </div>

            {gcpCredentialSource === "service_account_key" && (
              <div className="mt-3">
                <label htmlFor="gcp-sa-key" className="block text-sm font-medium text-gray-700 mb-1">Service Account Key (JSON)</label>
                <textarea id="gcp-sa-key" value={gcpServiceAccountKey} onChange={(e) => setGcpServiceAccountKey(e.target.value)}
                  rows={4} className="w-full px-3 py-2 border rounded font-mono text-xs focus:ring-2 focus:ring-bioaf-500"
                  placeholder='{"type": "service_account", ...}' />
              </div>
            )}

            {gcpCredentialSource === "vm_default" && (
              <div className="mt-3">
                <label htmlFor="gcp-sa-email" className="block text-sm font-medium text-gray-700 mb-1">
                  Service Account Email <span className="ml-1 text-gray-400 font-normal text-xs">(optional)</span>
                </label>
                <input id="gcp-sa-email" type="email" value={gcpServiceAccountEmail}
                  onChange={(e) => setGcpServiceAccountEmail(e.target.value)}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
                  placeholder="bioaf-sa@my-project.iam.gserviceaccount.com" />
              </div>
            )}
          </div>

          <button onClick={handleSaveGcp} disabled={gcpSaving}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50">
            {gcpSaving ? "Validating..." : "Save & Validate"}
          </button>

          {gcpValidation && !gcpValidation.passed && (
            <div className="border rounded divide-y text-sm">
              <div className="p-3 bg-red-50">
                <h4 className="font-semibold text-red-800">Validation Failed</h4>
              </div>
              <div className="p-3 space-y-1.5">
                <p className="text-xs font-medium text-gray-600">System Checks</p>
                {gcpValidation.checks.map((c) => (
                  <div key={c.name} className="flex items-start gap-2 text-xs">
                    <span className={c.passed ? "text-green-600" : "text-red-600"}>
                      {c.passed ? "\u2713" : "\u2717"}
                    </span>
                    <span>
                      <span className="font-medium">{c.name}</span>{" "}
                      <span className="text-gray-500">{c.message}</span>
                    </span>
                  </div>
                ))}
              </div>
              {gcpValidation.permission_details.some((p) => !p.granted) && (
                <div className="p-3 space-y-1.5">
                  <p className="text-xs font-medium text-gray-600">Missing Permissions</p>
                  {gcpValidation.permission_details
                    .filter((p) => !p.granted)
                    .map((p) => (
                      <div key={p.permission} className="flex items-center gap-2 text-xs">
                        <span className="text-red-600">{"\u2717"}</span>
                        <code className="bg-red-50 px-1 rounded">{p.permission}</code>
                        <span className="text-gray-400">(needs {p.recommended_role})</span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          )}

          <button onClick={() => setStep(4)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Do this later
          </button>
        </div>
      )}

      {/* Step 4: SMTP Settings */}
      {step === 4 && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Host</label>
              <input type="text" value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Port</label>
              <input type="number" value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
            <input type="text" value={smtpUsername} onChange={(e) => setSmtpUsername(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input type="password" value={smtpPassword} onChange={(e) => setSmtpPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">From Address</label>
            <input type="email" value={smtpFrom} onChange={(e) => setSmtpFrom(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>
          <button onClick={handleConfigureSmtp} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Save SMTP Configuration
          </button>
          <button onClick={() => setStep(5)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Do this later
          </button>
        </div>
      )}

      {/* Step 5: Infrastructure Decision */}
      {step === 5 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Would you like to set up cloud infrastructure now? This deploys a Kubernetes
            cluster, storage buckets, and supporting resources on GCP.
          </p>
          {!gcpConfigured && (
            <p className="text-sm text-amber-600">
              GCP credentials are required to set up infrastructure. You can configure them
              later in Settings.
            </p>
          )}
          <button
            onClick={handleSetupInfrastructure}
            disabled={!gcpConfigured}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            Set up infrastructure
          </button>
          <button onClick={handleDoInfraLater} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Do this later
          </button>
        </div>
      )}

      {/* Step 6: Select Stack */}
      {step === 6 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-4">
            Choose the compute infrastructure for running pipelines and notebooks.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div
              data-testid="compute-stack-kubernetes"
              onClick={() => setComputeStack("kubernetes")}
              className={`p-4 border-2 rounded-lg cursor-pointer transition-colors ${
                computeStack === "kubernetes"
                  ? "border-bioaf-600 bg-bioaf-50"
                  : "border-gray-200 hover:border-gray-300"
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold text-gray-900">Kubernetes + GCS</h3>
                <span className="text-xs bg-bioaf-100 text-bioaf-700 px-2 py-0.5 rounded-full font-medium">
                  Recommended
                </span>
              </div>
              <p className="text-sm text-gray-600">
                Cloud-native autoscaling with Google Kubernetes Engine and Cloud Storage.
              </p>
            </div>

            <div
              data-testid="compute-stack-slurm"
              className="p-4 border-2 border-gray-200 rounded-lg opacity-60 cursor-not-allowed"
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold text-gray-400">SLURM + NFS</h3>
                <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                  Coming Soon
                </span>
              </div>
              <p className="text-sm text-gray-400 mb-2">
                Traditional HPC cluster with shared filesystem.
              </p>
            </div>
          </div>

          <button onClick={handleSelectStack}
            disabled={stackDeploying}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50">
            {stackDeploying ? "Initializing infrastructure..." : `Continue with ${computeStack === "kubernetes" ? "Kubernetes + GCS" : "SLURM + NFS"}`}
          </button>
        </div>
      )}

      {/* Step 7: Deploying */}
      {step === 7 && (
        <div className="space-y-4">
          <div className="p-4 bg-blue-50 border border-blue-200 rounded">
            <p className="text-sm text-blue-800">
              Infrastructure deployment has started. This usually takes 10-15 minutes.
              You can monitor progress on the Infrastructure page after setup.
            </p>
          </div>
          <button onClick={() => setStep(8)} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Continue to Getting Started
          </button>
        </div>
      )}

      {/* Step 8: Getting Started */}
      {step === 8 && (
        <div className="space-y-4">
          <div className="p-4 bg-green-50 border border-green-200 rounded">
            <h3 className="font-semibold text-green-800">Setup Complete</h3>
            <p className="text-sm text-green-700 mt-1">
              Your bioAF platform is configured. You can explore the Getting Started guide
              anytime from your profile page.
            </p>
          </div>
          <button onClick={onComplete} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Go to Dashboard
          </button>
        </div>
      )}
    </div>
  );
}
