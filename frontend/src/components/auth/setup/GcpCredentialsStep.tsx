"use client";

import { Button } from "@/components/ui/Button";
import { GCP_REGIONS, SETUP_APP_ROLES, SETUP_BOOTSTRAP_ROLES, SETUP_REQUIRED_APIS, zonesForRegion } from "./setupConstants";
import type { GcpCredentials } from "./useCloudCredentials";

/** The GCP credentials step's view; the fields and save-and-validate are the wizard's. */
export function GcpCredentialsStep({
  gcp,
  onSave,
  onSkip,
}: {
  gcp: GcpCredentials;
  onSave: () => void;
  onSkip: () => void;
}) {
  const {
    gcpProjectId,
    setGcpProjectId,
    gcpRegion,
    setGcpRegion,
    gcpZone,
    setGcpZone,
    gcpOrgSlug,
    setGcpOrgSlug,
    gcpCredentialSource,
    setGcpCredentialSource,
    gcpServiceAccountKey,
    setGcpServiceAccountKey,
    gcpServiceAccountEmail,
    setGcpServiceAccountEmail,
    gcpBootstrapSaEmail,
    gcpPrefilled,
    gcpSaving,
    gcpValidation,
  } = gcp;
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600 mb-2">
        Configure your Google Cloud Platform project. Credentials are required before
        deploying a compute stack.
      </p>

      <details data-testid="gcp-prerequisites" className="bg-gray-50 border rounded p-4">
        <summary className="cursor-pointer text-sm font-semibold text-gray-700 select-none">
          Prerequisites: IAM Roles &amp; APIs
          <span className="ml-1 text-xs font-normal text-gray-500">
            (bioaf-bootstrap: {SETUP_BOOTSTRAP_ROLES.length} roles, bioaf-app: {SETUP_APP_ROLES.length}, {SETUP_REQUIRED_APIS.length} APIs)
          </span>
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-xs text-gray-500">
            The installer applies all of this automatically. Listed for transparency
            and self-host overrides.
          </p>
          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">bioaf-bootstrap (impersonated for IAM/Terraform/Cloud Build):</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
              {SETUP_BOOTSTRAP_ROLES.map(({ role, description }) => (
                <div key={role} className="flex items-center gap-1.5 text-xs">
                  <code className="bg-white px-1 py-0.5 rounded text-gray-800 border">{role}</code>
                  <span className="text-gray-500">{description}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">bioaf-app (attached to the VM, scoped):</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
              {SETUP_APP_ROLES.map(({ role, description }) => (
                <div key={role} className="flex items-center gap-1.5 text-xs">
                  <code className="bg-white px-1 py-0.5 rounded text-gray-800 border">{role}</code>
                  <span className="text-gray-500">{description}</span>
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

      {gcpPrefilled && (
        <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
          <p className="font-medium mb-1">Pre-populated from install-gcp.sh</p>
          <p>
            We&apos;ve filled in the values from the GCP installer you just ran.
            You can change anything here, but the defaults match the project,
            region, and service accounts we just created.
          </p>
        </div>
      )}

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
          <span className="ml-1 text-gray-500 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
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
          <div className="mt-3 space-y-3">
            {gcpBootstrapSaEmail ? (
              <div className="bg-gray-50 border rounded p-3 text-xs">
                <p className="font-medium text-gray-700 mb-1">Service accounts (auto-detected)</p>
                <p className="text-gray-600 mb-2">
                  bioAF uses two service accounts: <code className="bg-white px-1 rounded">bioaf-app</code> for
                  runtime calls (attached to this VM) and <code className="bg-white px-1 rounded">bioaf-bootstrap</code> for
                  privileged operations (impersonated by the backend, never attached to a VM). Both were created by
                  install-gcp.sh; nothing to enter here.
                </p>
                <p className="text-gray-600">
                  Bootstrap SA: <code className="bg-white px-1 rounded">{gcpBootstrapSaEmail}</code>
                </p>
              </div>
            ) : (
              <div>
                <label htmlFor="gcp-sa-email" className="block text-sm font-medium text-gray-700 mb-1">
                  Bootstrap SA Email <span className="ml-1 text-gray-500 font-normal text-xs">(optional)</span>
                </label>
                <p className="text-xs text-gray-500 mb-2">
                  The email of the bioaf-bootstrap service account. install-gcp.sh sets this automatically;
                  only fill this in if you set GCP up manually.
                </p>
                <input id="gcp-sa-email" type="email" value={gcpServiceAccountEmail}
                  onChange={(e) => setGcpServiceAccountEmail(e.target.value)}
                  className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
                  placeholder="bioaf-bootstrap@my-project.iam.gserviceaccount.com" />
              </div>
            )}
          </div>
        )}
      </div>

      <Button className="w-full" onClick={onSave} disabled={gcpSaving}>
        {gcpSaving ? "Validating..." : "Save & Validate"}
      </Button>

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
          {gcpValidation.app_probe && gcpValidation.bootstrap_probe && (
            <div className="p-3 space-y-3">
              <div>
                <p className="text-xs font-medium text-gray-600">
                  bioaf-app probe ({gcpValidation.app_probe.sa_email || "VM default"}):{" "}
                  <span className={gcpValidation.app_probe.passed ? "text-green-600" : "text-red-600"}>
                    {gcpValidation.app_probe.passed ? "\u2713 passed" : "\u2717 failed"}
                  </span>
                </p>
                {gcpValidation.app_probe.permission_details.filter((p) => !p.granted).map((p) => (
                  <div key={p.permission} className="flex items-center gap-2 text-xs ml-4 mt-1">
                    <span className="text-red-600">{"\u2717"}</span>
                    <code className="bg-red-50 px-1 rounded">{p.permission}</code>
                    <span className="text-gray-500">(needs {p.recommended_role})</span>
                  </div>
                ))}
              </div>
              <div>
                <p className="text-xs font-medium text-gray-600">
                  bioaf-bootstrap probe ({gcpValidation.bootstrap_probe.sa_email}):{" "}
                  <span className={gcpValidation.bootstrap_probe.passed ? "text-green-600" : "text-red-600"}>
                    {gcpValidation.bootstrap_probe.passed ? "\u2713 passed" : "\u2717 failed"}
                  </span>
                </p>
                {gcpValidation.bootstrap_probe.permission_details.filter((p) => !p.granted).map((p) => (
                  <div key={p.permission} className="flex items-center gap-2 text-xs ml-4 mt-1">
                    <span className="text-red-600">{"\u2717"}</span>
                    <code className="bg-red-50 px-1 rounded">{p.permission}</code>
                    <span className="text-gray-500">(needs {p.recommended_role})</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!(gcpValidation.app_probe && gcpValidation.bootstrap_probe) &&
            gcpValidation.permission_details.some((p) => !p.granted) && (
            <div className="p-3 space-y-1.5">
              <p className="text-xs font-medium text-gray-600">Missing Permissions</p>
              {gcpValidation.permission_details
                .filter((p) => !p.granted)
                .map((p) => (
                  <div key={p.permission} className="flex items-center gap-2 text-xs">
                    <span className="text-red-600">{"\u2717"}</span>
                    <code className="bg-red-50 px-1 rounded">{p.permission}</code>
                    <span className="text-gray-500">(needs {p.recommended_role})</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      <button onClick={onSkip} className="w-full text-gray-500 text-sm hover:text-gray-700">
        Do this later
      </button>
    </div>
  );
}
