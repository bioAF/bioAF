"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AWS_REGIONS, DEFAULT_AWS_REGION } from "@/lib/aws-regions";

// AWS settings panel (stage 8d), the structural parallel of GcpSettingsContent.
//
// The AWS account substrate (S3, IAM roles, EC2) is provisioned by
// install-aws.sh and the credentials are the EC2 instance profile, so there is
// no key to paste: the panel saves the account / region / role ARNs / org_slug
// the install runs on and validates that the ambient credentials resolve (STS)
// against /api/v1/settings/aws.

interface AwsConfig {
  aws_account_id: string | null;
  aws_region: string | null;
  aws_app_role_arn: string | null;
  aws_bootstrap_role_arn: string | null;
  org_slug: string | null;
  aws_credential_source: string;
  aws_credentials_configured: boolean;
  aws_validation_status: string | null;
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
  account_id: string | null;
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

interface AwsSettingsContentProps {
  // Optional seed so tests can render the form without a fetch. When omitted the
  // panel reads /api/v1/settings/aws on mount (the normal path).
  initialConfig?: {
    aws_account_id?: string;
    aws_region?: string;
    aws_bootstrap_role_arn?: string;
    aws_app_role_arn?: string;
    org_slug?: string;
  };
}

export function AwsSettingsContent({ initialConfig }: AwsSettingsContentProps = {}) {
  const [accountId, setAccountId] = useState(initialConfig?.aws_account_id ?? "");
  const [region, setRegion] = useState(initialConfig?.aws_region ?? DEFAULT_AWS_REGION);
  const [bootstrapRoleArn, setBootstrapRoleArn] = useState(initialConfig?.aws_bootstrap_role_arn ?? "");
  const [appRoleArn, setAppRoleArn] = useState(initialConfig?.aws_app_role_arn ?? "");
  const [orgSlug, setOrgSlug] = useState(initialConfig?.org_slug ?? "");
  const [credentialSource, setCredentialSource] = useState("instance_profile");

  const [orgSlugError, setOrgSlugError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // initialConfig is the test-injection path; skip the fetch when seeded.
    if (initialConfig) return;
    api
      .get<AwsConfig>("/api/v1/settings/aws")
      .then((cfg) => {
        setAccountId(cfg.aws_account_id ?? "");
        setRegion(cfg.aws_region ?? DEFAULT_AWS_REGION);
        setAppRoleArn(cfg.aws_app_role_arn ?? "");
        setBootstrapRoleArn(cfg.aws_bootstrap_role_arn ?? "");
        setOrgSlug(cfg.org_slug ?? "");
        setCredentialSource(cfg.aws_credential_source ?? "instance_profile");
      })
      .catch(() => {
        // Endpoint may be unreachable on a non-AWS install -- leave defaults.
      });
  }, [initialConfig]);

  const runValidation = async () => {
    setValidating(true);
    try {
      const result = await api.post<ValidationResult>("/api/v1/settings/aws/validate");
      setValidationResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    setError("");
    setMessage("");

    const slugErr = orgSlug ? validateOrgSlug(orgSlug) : null;
    setOrgSlugError(slugErr);
    if (slugErr) return;

    setSaving(true);
    try {
      await api.put("/api/v1/settings/aws", {
        aws_account_id: accountId || undefined,
        aws_region: region,
        aws_app_role_arn: appRoleArn || undefined,
        aws_bootstrap_role_arn: bootstrapRoleArn || undefined,
        org_slug: orgSlug || undefined,
      });
      setMessage("Configuration saved. Validating...");
      setValidationResult(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save AWS configuration");
      setSaving(false);
      return;
    }
    setSaving(false);

    // Auto-validate after save (parallel to the GCP panel).
    await runValidation();
    setMessage("");
  };

  const handleValidate = async () => {
    setError("");
    await runValidation();
  };

  return (
    <div data-testid="aws-settings" className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">AWS Settings</h1>
        <p className="text-sm text-gray-500 mt-1">
          The account, S3 storage, and IAM roles for this install are provisioned by{" "}
          <code className="bg-gray-100 px-1 py-0.5 rounded">install-aws.sh</code>; the app
          authenticates via the EC2 instance profile, so there is no key to enter.
        </p>
      </div>

      {message && (
        <div className="p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm max-w-2xl">
          {message}
        </div>
      )}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm max-w-2xl">
          {error}
        </div>
      )}

      {/* Configuration form */}
      <div className="bg-white rounded-lg shadow p-6 max-w-2xl space-y-5">
        {/* AWS Account ID */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">AWS Account ID</label>
          <input
            data-testid="aws-account-id-input"
            type="text"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="w-full px-3 py-2 border rounded"
            placeholder="123456789012"
          />
        </div>

        {/* Region */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Default Region</label>
          <select
            data-testid="aws-region-select"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="w-full px-3 py-2 border rounded"
          >
            {AWS_REGIONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>

        {/* Bootstrap role ARN */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Bootstrap Role ARN
            <span className="ml-1 text-gray-400 font-normal text-xs">(provisioning / Terraform)</span>
          </label>
          <input
            data-testid="aws-bootstrap-role-arn-input"
            type="text"
            value={bootstrapRoleArn}
            onChange={(e) => setBootstrapRoleArn(e.target.value)}
            className="w-full px-3 py-2 border rounded font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/bioaf-bootstrap"
          />
        </div>

        {/* App role ARN */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            App Role ARN
            <span className="ml-1 text-gray-400 font-normal text-xs">(runtime / EC2 instance profile)</span>
          </label>
          <input
            data-testid="aws-app-role-arn-input"
            type="text"
            value={appRoleArn}
            onChange={(e) => setAppRoleArn(e.target.value)}
            className="w-full px-3 py-2 border rounded font-mono text-xs"
            placeholder="arn:aws:iam::123456789012:role/bioaf-app"
          />
        </div>

        {/* Org Slug */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Organization Slug
            <span className="ml-1 text-gray-400 font-normal text-xs">(used for S3 bucket names)</span>
          </label>
          <input
            data-testid="aws-org-slug-input"
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
            <p data-testid="aws-org-slug-error" className="mt-1 text-sm text-red-600">{orgSlugError}</p>
          )}
        </div>

        <div className="bg-gray-50 border rounded p-3 text-xs text-gray-600">
          Authentication: <code className="bg-white px-1 rounded">{credentialSource}</code>. The app reads the
          EC2 instance profile via IMDS; no access key is stored.
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 pt-2">
          <button
            data-testid="save-aws-config-btn"
            onClick={handleSave}
            disabled={saving || validating}
            className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : validating ? "Validating..." : "Save & Validate"}
          </button>
          <button
            data-testid="validate-aws-btn"
            onClick={handleValidate}
            disabled={validating || saving}
            className="px-4 py-2 border border-gray-300 rounded text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {validating ? "Validating..." : "Re-validate"}
          </button>
        </div>
      </div>

      {/* Validation results */}
      {validationResult && (
        <div data-testid="aws-validation-results" className="bg-white rounded-lg shadow p-6 max-w-2xl">
          <h2 className="text-lg font-semibold mb-4">
            Validation{" "}
            {validationResult.passed ? (
              <span className="text-green-600">Passed</span>
            ) : (
              <span className="text-red-600">Failed</span>
            )}
          </h2>
          <ul className="space-y-2">
            {validationResult.checks.map((check) => (
              <li key={check.name} className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 ${check.passed ? "text-green-600" : check.status === "skipped" ? "text-gray-400" : "text-red-600"}`}>
                  {check.passed ? "✓" : check.status === "skipped" ? "–" : "✗"}
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
    </div>
  );
}
