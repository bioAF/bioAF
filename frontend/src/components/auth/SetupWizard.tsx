"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { setToken } from "@/lib/auth";
import { InviteForm } from "./InviteForm";

const STEPS = [
  "Create Admin Account",
  "Verify Email",
  "Organization Name",
  "SMTP Configuration",
  "GCP Configuration",
  "Compute Stack",
  "Invite Team",
  "Confirmation",
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
  return GCP_ZONES[region] ?? [`${region}-a`, `${region}-b`, `${region}-c`];
}

interface SetupWizardProps {
  onComplete: () => void;
}

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [name, setName] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [fallbackCode, setFallbackCode] = useState("");
  const [orgName, setOrgName] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpFrom, setSmtpFrom] = useState("");

  // GCP configuration state
  const [gcpProjectId, setGcpProjectId] = useState("");
  const [gcpRegion, setGcpRegion] = useState("us-central1");
  const [gcpZone, setGcpZone] = useState("us-central1-a");
  const [gcpOrgSlug, setGcpOrgSlug] = useState("");
  const [gcpCredentialSource, setGcpCredentialSource] = useState<"vm_default" | "service_account_key">("vm_default");
  const [gcpServiceAccountKey, setGcpServiceAccountKey] = useState("");
  const [gcpServiceAccountEmail, setGcpServiceAccountEmail] = useState("");
  const [gcpSaving, setGcpSaving] = useState(false);

  const [computeStack, setComputeStack] = useState("kubernetes");

  const handleCreateAdmin = async () => {
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setError("");
    try {
      const response = await api.post<{
        access_token: string;
        email_sent: boolean;
        verification_code?: string;
      }>("/api/bootstrap/create-admin", { email, password, name: name || undefined });
      setToken(response.access_token);
      if (response.verification_code) {
        setFallbackCode(response.verification_code);
      }
      setStep(1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create admin");
    }
  };

  const handleVerifyEmail = async () => {
    setError("");
    try {
      await api.post("/api/auth/verify-email", { email, code: verificationCode });
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verification failed");
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
      setStep(4);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure SMTP");
    }
  };

  const handleSaveGcp = async () => {
    setError("");
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

      // Validate after saving
      await api.post("/api/v1/settings/gcp/validate");
      setStep(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save GCP configuration");
    } finally {
      setGcpSaving(false);
    }
  };

  const handleSelectComputeStack = async () => {
    setError("");
    try {
      // Save the stack selection (non-critical if endpoint doesn't exist)
      try {
        await api.post("/api/bootstrap/configure-compute-stack", {
          compute_stack: computeStack,
        });
      } catch {
        // Non-critical
      }

      // Start deployment in the background -- the DeploymentBanner will
      // track progress and show a toast when complete.
      try {
        await api.post("/api/v1/infrastructure/stack/deploy-background", {
          stack_type: computeStack,
        });
      } catch {
        // Deployment may fail if bootstrap hasn't run yet or preconditions
        // aren't met. The user can trigger deployment later from the
        // Infrastructure page.
      }

      setStep(6);
    } catch (e) {
      setStep(6);
    }
  };

  const handleComplete = async () => {
    try {
      await api.post("/api/bootstrap/complete");
      onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to complete setup");
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

      {/* Step 1: Create Admin */}
      {step === 0 && (
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

      {/* Step 2: Verify Email */}
      {step === 1 && (
        <div className="space-y-4">
          {fallbackCode && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 text-yellow-800 rounded text-sm">
              SMTP not configured. Your verification code is: <strong>{fallbackCode}</strong>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Verification Code</label>
            <input type="text" value={verificationCode} onChange={(e) => setVerificationCode(e.target.value)}
              placeholder="Enter 6-digit code"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>
          <button onClick={handleVerifyEmail} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Verify Email
          </button>
          <button onClick={() => setStep(2)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Skip for now
          </button>
        </div>
      )}

      {/* Step 3: Org Name */}
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

      {/* Step 4: SMTP */}
      {step === 3 && (
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
          <button onClick={() => setStep(4)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Skip for now
          </button>
        </div>
      )}

      {/* Step 5: GCP Configuration */}
      {step === 4 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-2">
            Configure your Google Cloud Platform project. Credentials are required before
            deploying a compute stack.
          </p>

          <div>
            <label htmlFor="gcp-project-id" className="block text-sm font-medium text-gray-700 mb-1">GCP Project ID</label>
            <input
              id="gcp-project-id"
              type="text"
              value={gcpProjectId}
              onChange={(e) => setGcpProjectId(e.target.value)}
              placeholder="my-gcp-project"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="gcp-region" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
              <select
                id="gcp-region"
                value={gcpRegion}
                onChange={(e) => {
                  setGcpRegion(e.target.value);
                  setGcpZone(zonesForRegion(e.target.value)[0]);
                }}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
              >
                {GCP_REGIONS.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="gcp-zone" className="block text-sm font-medium text-gray-700 mb-1">Zone</label>
              <select
                id="gcp-zone"
                value={gcpZone}
                onChange={(e) => setGcpZone(e.target.value)}
                className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
              >
                {zonesForRegion(gcpRegion).map((z) => (
                  <option key={z} value={z}>{z}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="gcp-org-slug" className="block text-sm font-medium text-gray-700 mb-1">
              Organization Slug
              <span className="ml-1 text-gray-400 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
            </label>
            <input
              id="gcp-org-slug"
              type="text"
              value={gcpOrgSlug}
              onChange={(e) => setGcpOrgSlug(e.target.value)}
              placeholder="my-bioaf-org"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Authentication</label>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="gcp_credential_source"
                  value="vm_default"
                  checked={gcpCredentialSource === "vm_default"}
                  onChange={() => setGcpCredentialSource("vm_default")}
                />
                <span className="text-sm">VM default credentials</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="gcp_credential_source"
                  value="service_account_key"
                  checked={gcpCredentialSource === "service_account_key"}
                  onChange={() => setGcpCredentialSource("service_account_key")}
                />
                <span className="text-sm">Service account key (JSON)</span>
              </label>
            </div>

            {gcpCredentialSource === "service_account_key" && (
              <div className="mt-3">
                <label htmlFor="gcp-sa-key" className="block text-sm font-medium text-gray-700 mb-1">Service Account Key (JSON)</label>
                <textarea
                  id="gcp-sa-key"
                  value={gcpServiceAccountKey}
                  onChange={(e) => setGcpServiceAccountKey(e.target.value)}
                  rows={4}
                  className="w-full px-3 py-2 border rounded font-mono text-xs focus:ring-2 focus:ring-bioaf-500"
                  placeholder='{"type": "service_account", ...}'
                />
              </div>
            )}

            {gcpCredentialSource === "vm_default" && (
              <div className="mt-3">
                <label htmlFor="gcp-sa-email" className="block text-sm font-medium text-gray-700 mb-1">
                  Service Account Email
                  <span className="ml-1 text-gray-400 font-normal text-xs">(optional)</span>
                </label>
                <input
                  id="gcp-sa-email"
                  type="email"
                  value={gcpServiceAccountEmail}
                  onChange={(e) => setGcpServiceAccountEmail(e.target.value)}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
                  placeholder="bioaf-sa@my-project.iam.gserviceaccount.com"
                />
              </div>
            )}
          </div>

          <button
            onClick={handleSaveGcp}
            disabled={gcpSaving}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {gcpSaving ? "Saving..." : "Save & Validate"}
          </button>
          <button onClick={() => setStep(5)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Skip for now
          </button>
        </div>
      )}

      {/* Step 6: Compute Stack */}
      {step === 5 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-4">
            Choose the compute infrastructure for running pipelines and notebooks.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Kubernetes option */}
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
              <p className="text-sm text-gray-600 mb-2">
                Cloud-native autoscaling with Google Kubernetes Engine and Cloud Storage.
                Pay only for what you use.
              </p>
              <div className="text-xs text-gray-500">
                Estimated: $50-200/month depending on workload
              </div>
              <div className="mt-2 text-xs text-gray-400" title="Portable across GCP, AWS, and Azure. Autoscales to zero when idle. Docker container ecosystem.">
                Hover for details
              </div>
            </div>

            {/* SLURM option - disabled */}
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
                Traditional HPC cluster with shared filesystem. Familiar to academic environments.
              </p>
              <div className="text-xs text-gray-400">
                Minimum: ~$250/month (SLURM controller + NFS)
              </div>
            </div>
          </div>

          <button
            onClick={handleSelectComputeStack}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700"
          >
            Continue with {computeStack === "kubernetes" ? "Kubernetes + GCS" : "SLURM + NFS"}
          </button>
        </div>
      )}

      {/* Step 7: Invite Team */}
      {step === 6 && (
        <div className="space-y-4">
          <InviteForm />
          <button onClick={() => setStep(7)} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Continue
          </button>
          <button onClick={() => setStep(7)} className="w-full text-gray-500 text-sm hover:text-gray-700">
            Skip for now
          </button>
        </div>
      )}

      {/* Step 8: Confirmation */}
      {step === 7 && (
        <div className="space-y-4">
          <div className="p-4 bg-green-50 border border-green-200 rounded">
            <h3 className="font-semibold text-green-800">Setup Summary</h3>
            <ul className="mt-2 text-sm text-green-700 space-y-1">
              <li>Admin account created</li>
              {orgName && <li>Organization: {orgName}</li>}
              {gcpProjectId && <li>GCP Project: {gcpProjectId}</li>}
              <li>Compute stack: {computeStack === "kubernetes" ? "Kubernetes + GCS" : "SLURM + NFS"}</li>
              <li>Platform ready to use</li>
            </ul>
          </div>
          <button onClick={handleComplete} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Launch bioAF
          </button>
        </div>
      )}
    </div>
  );
}
