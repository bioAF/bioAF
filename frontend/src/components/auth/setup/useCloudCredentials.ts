"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { DEFAULT_AWS_REGION } from "@/lib/aws-regions";

/**
 * The cloud credentials step's fields, the installer's prefill, and save-and-validate, for each
 * provider. Owned by the wizard root so the fields (a service-account key among them) live as long
 * as the wizard and no longer, in memory only; the step view only renders them.
 *
 * The prefill is read on each step change from the credentials step on (as before), and fills a
 * user-entered field only while it is still empty: it reads the field's current value, not the
 * value it had when the read started.
 */

/**
 * The saved validation outcome, as persisted: a JSON array of checks. Anything else (empty, legacy,
 * malformed) means the outcome is unknown, not a crash and not a pass.
 */
function savedChecks(raw: string | null | undefined): ProbeCheck[] | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter(
      (c): c is ProbeCheck => !!c && typeof c === "object" && "name" in c && "passed" in c,
    );
  } catch {
    return null;
  }
}

/** A stable comparison key for a set of field values. */
function valuesKey(values: Record<string, string>): string {
  return JSON.stringify(Object.keys(values).sort().map((k) => [k, values[k] ?? ""]));
}

interface ProbeCheck {
  name: string;
  passed: boolean;
  message: string;
}

interface PermissionDetail {
  permission: string;
  granted: boolean;
  recommended_role: string;
}

interface Probe {
  sa_email: string | null;
  passed: boolean;
  checks: ProbeCheck[];
  permission_details: PermissionDetail[];
}

export interface GcpValidation {
  passed: boolean;
  checks: ProbeCheck[];
  permission_details: PermissionDetail[];
  app_probe: Probe | null;
  bootstrap_probe: Probe | null;
}

interface GcpSettings {
  gcp_credentials_configured?: boolean;
  gcp_validation_status?: string | null;
  gcp_project_id: string | null;
  gcp_region: string | null;
  gcp_zone: string | null;
  org_slug: string | null;
  gcp_credential_source: string;
  gcp_service_account_email: string | null;
  gcp_bootstrap_sa_email: string | null;
}

/** `prefillKey` is null when the prefill should not be read (before the step, or another cloud). */
export function useGcpCredentials(prefillKey: string | null) {
  const [gcpProjectId, setGcpProjectId] = useState("");
  const [gcpRegion, setGcpRegion] = useState("us-central1");
  const [gcpZone, setGcpZone] = useState("us-central1-a");
  const [gcpOrgSlug, setGcpOrgSlug] = useState("");
  const [gcpCredentialSource, setGcpCredentialSource] = useState<"vm_default" | "service_account_key">("vm_default");
  const [gcpServiceAccountKey, setGcpServiceAccountKey] = useState("");
  const [gcpServiceAccountEmail, setGcpServiceAccountEmail] = useState("");
  // bioaf-bootstrap email; populated by either install-gcp.sh prefill or VM metadata read on
  // backend startup. Display-only: the wizard never overrides it.
  const [gcpBootstrapSaEmail, setGcpBootstrapSaEmail] = useState("");
  const [gcpPrefilled, setGcpPrefilled] = useState(false);
  const [gcpSaving, setGcpSaving] = useState(false);
  const [gcpValidation, setGcpValidation] = useState<GcpValidation | null>(null);
  // The values the server has saved (null until read), and the values a passed validation is for.
  // Validation belongs to exact values: editing any of them, or typing a replacement key, clears it.
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const [validatedKey, setValidatedKey] = useState<string | null>(null);

  const typed = useRef({ gcpProjectId, gcpOrgSlug, gcpServiceAccountEmail });
  typed.current = { gcpProjectId, gcpOrgSlug, gcpServiceAccountEmail } as typeof typed.current;

  const committedValues = {
    gcpProjectId,
    gcpRegion,
    gcpZone,
    gcpOrgSlug,
    gcpCredentialSource,
    gcpServiceAccountEmail,
  };
  const currentKey = valuesKey(committedValues);
  const keyTyped = gcpCredentialSource === "service_account_key" && gcpServiceAccountKey !== "";
  const gcpConfigured = !keyTyped && validatedKey === currentKey;
  const unchangedSinceSaved = !keyTyped && savedKey === currentKey;

  // install-gcp.sh's prefill path writes project/region/zone/credential-source/bootstrap-email to
  // platform_config, and the metadata-server fallback writes the bootstrap email on first backend
  // startup. Before auth the read fails, which is expected and ignored.
  useResource<GcpSettings>(prefillKey, (signal) => api.get<GcpSettings>("/api/v1/settings/gcp", { signal }), {
    onSuccess: (cfg) => {
      let prefilled = false;
      if (cfg.gcp_project_id && !typed.current.gcpProjectId) {
        setGcpProjectId(cfg.gcp_project_id);
        prefilled = true;
      }
      if (cfg.gcp_region) setGcpRegion(cfg.gcp_region);
      if (cfg.gcp_zone) setGcpZone(cfg.gcp_zone);
      if (cfg.org_slug && !typed.current.gcpOrgSlug) setGcpOrgSlug(cfg.org_slug);
      if (cfg.gcp_credential_source === "service_account_key" || cfg.gcp_credential_source === "vm_default") {
        setGcpCredentialSource(cfg.gcp_credential_source);
      }
      if (cfg.gcp_bootstrap_sa_email) {
        setGcpBootstrapSaEmail(cfg.gcp_bootstrap_sa_email);
        prefilled = true;
      }
      if (cfg.gcp_service_account_email && !typed.current.gcpServiceAccountEmail) {
        setGcpServiceAccountEmail(cfg.gcp_service_account_email);
      }
      if (prefilled) setGcpPrefilled(true);
      // What is saved, and whether those saved values passed validation. Installer prefill alone is
      // never a pass.
      const saved = valuesKey({
        gcpProjectId: cfg.gcp_project_id ?? "",
        gcpRegion: cfg.gcp_region ?? "us-central1",
        gcpZone: cfg.gcp_zone ?? "us-central1-a",
        gcpOrgSlug: cfg.org_slug ?? "",
        gcpCredentialSource:
          cfg.gcp_credential_source === "service_account_key" ? "service_account_key" : "vm_default",
        gcpServiceAccountEmail: cfg.gcp_service_account_email ?? "",
      });
      setSavedKey(saved);
      const checks = savedChecks(cfg.gcp_validation_status);
      if (cfg.gcp_credentials_configured) setValidatedKey(saved);
      if (checks && !cfg.gcp_credentials_configured) {
        setGcpValidation({ passed: false, checks, permission_details: [], app_probe: null, bootstrap_probe: null });
      }
    },
  });

  /**
   * Resolves true when the current values are validated; a failure is thrown. Values that already
   * passed make no request; unchanged saved values are only validated (a save would reset the
   * saved outcome); changed values or a typed replacement key are saved, then validated.
   */
  async function saveAndValidate(): Promise<boolean> {
    if (gcpConfigured) return true;
    setGcpValidation(null);
    setGcpSaving(true);
    try {
      if (!unchangedSinceSaved) await saveGcp();
      const result = await api.post<GcpValidation | null>("/api/v1/settings/gcp/validate");
      setGcpValidation(result);
      if (result?.passed) setValidatedKey(currentKey);
      return !!result?.passed;
    } finally {
      setGcpSaving(false);
    }
  }

  async function saveGcp(): Promise<void> {
    await api.put("/api/v1/settings/gcp", {
      gcp_project_id: gcpProjectId || undefined,
      gcp_region: gcpRegion,
      gcp_zone: gcpZone,
      org_slug: gcpOrgSlug || undefined,
      gcp_credential_source: gcpCredentialSource,
      service_account_key:
        gcpCredentialSource === "service_account_key" && gcpServiceAccountKey ? gcpServiceAccountKey : undefined,
      gcp_service_account_email: gcpServiceAccountEmail || undefined,
    });
    // Saved: these are now the server's values, and the key is no longer pending.
    setSavedKey(currentKey);
    setGcpServiceAccountKey("");
  }

  return {
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
    gcpConfigured,
    gcpValidation,
    /** What the step commits, for the unchanged-resubmit comparison. */
    committedValues,
    saveAndValidate,
  };
}

export interface AwsValidation {
  passed: boolean;
  checks: (ProbeCheck & { status: string })[];
  account_id: string | null;
}

interface AwsSettings {
  aws_credentials_configured?: boolean;
  aws_validation_status?: string | null;
  aws_account_id: string | null;
  aws_region: string | null;
  aws_app_role_arn: string | null;
  aws_bootstrap_role_arn: string | null;
  org_slug: string | null;
  aws_credential_source: string;
}

/**
 * The app authenticates through the EC2 instance profile, so there is no key to enter; the step
 * confirms the account / region / role / org_slug the installer persisted and validates that the
 * ambient credentials resolve (STS).
 */
export function useAwsCredentials(prefillKey: string | null) {
  const [awsAccountId, setAwsAccountId] = useState("");
  const [awsRegion, setAwsRegion] = useState(DEFAULT_AWS_REGION);
  const [awsAppRoleArn, setAwsAppRoleArn] = useState("");
  const [awsBootstrapRoleArn, setAwsBootstrapRoleArn] = useState("");
  const [awsOrgSlug, setAwsOrgSlug] = useState("");
  const [awsCredentialSource, setAwsCredentialSource] = useState("instance_profile");
  const [awsPrefilled, setAwsPrefilled] = useState(false);
  const [awsSaving, setAwsSaving] = useState(false);
  const [awsValidation, setAwsValidation] = useState<AwsValidation | null>(null);
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const [validatedKey, setValidatedKey] = useState<string | null>(null);
  const committedValues = { awsAccountId, awsRegion, awsAppRoleArn, awsBootstrapRoleArn, awsOrgSlug };
  const currentKey = valuesKey(committedValues);
  const awsConfigured = validatedKey === currentKey;
  const unchangedSinceSaved = savedKey === currentKey;

  const typed = useRef({ awsAccountId, awsAppRoleArn, awsBootstrapRoleArn, awsOrgSlug });
  typed.current = { awsAccountId, awsAppRoleArn, awsBootstrapRoleArn, awsOrgSlug };

  useResource<AwsSettings>(prefillKey, (signal) => api.get<AwsSettings>("/api/v1/settings/aws", { signal }), {
    onSuccess: (cfg) => {
      let prefilled = false;
      if (cfg.aws_account_id && !typed.current.awsAccountId) {
        setAwsAccountId(cfg.aws_account_id);
        prefilled = true;
      }
      if (cfg.aws_region) setAwsRegion(cfg.aws_region);
      if (cfg.aws_app_role_arn && !typed.current.awsAppRoleArn) {
        setAwsAppRoleArn(cfg.aws_app_role_arn);
        prefilled = true;
      }
      if (cfg.aws_bootstrap_role_arn && !typed.current.awsBootstrapRoleArn) {
        setAwsBootstrapRoleArn(cfg.aws_bootstrap_role_arn);
      }
      if (cfg.org_slug && !typed.current.awsOrgSlug) setAwsOrgSlug(cfg.org_slug);
      if (cfg.aws_credential_source) setAwsCredentialSource(cfg.aws_credential_source);
      if (prefilled) setAwsPrefilled(true);
      const saved = valuesKey({
        awsAccountId: cfg.aws_account_id ?? "",
        awsRegion: cfg.aws_region ?? DEFAULT_AWS_REGION,
        awsAppRoleArn: cfg.aws_app_role_arn ?? "",
        awsBootstrapRoleArn: cfg.aws_bootstrap_role_arn ?? "",
        awsOrgSlug: cfg.org_slug ?? "",
      });
      setSavedKey(saved);
      if (cfg.aws_credentials_configured) setValidatedKey(saved);
      const checks = savedChecks(cfg.aws_validation_status);
      if (checks && !cfg.aws_credentials_configured) {
        setAwsValidation({
          passed: false,
          checks: checks.map((c) => ({ ...c, status: c.passed ? "passed" : "failed" })),
          account_id: cfg.aws_account_id,
        });
      }
    },
  });

  /** As for GCP: no request for validated values, validate-only for unchanged saved values. */
  async function saveAndValidate(): Promise<boolean> {
    if (awsConfigured) return true;
    setAwsValidation(null);
    setAwsSaving(true);
    try {
      if (!unchangedSinceSaved) {
        await api.put("/api/v1/settings/aws", {
          aws_account_id: awsAccountId || undefined,
          aws_region: awsRegion,
          aws_app_role_arn: awsAppRoleArn || undefined,
          aws_bootstrap_role_arn: awsBootstrapRoleArn || undefined,
          org_slug: awsOrgSlug || undefined,
        });
        setSavedKey(currentKey);
      }
      const result = await api.post<AwsValidation | null>("/api/v1/settings/aws/validate");
      setAwsValidation(result);
      if (result?.passed) setValidatedKey(currentKey);
      return !!result?.passed;
    } finally {
      setAwsSaving(false);
    }
  }

  return {
    awsAccountId,
    setAwsAccountId,
    awsRegion,
    setAwsRegion,
    awsAppRoleArn,
    setAwsAppRoleArn,
    awsOrgSlug,
    setAwsOrgSlug,
    awsCredentialSource,
    awsPrefilled,
    awsSaving,
    awsConfigured,
    awsValidation,
    committedValues,
    saveAndValidate,
  };
}

export type GcpCredentials = ReturnType<typeof useGcpCredentials>;
export type AwsCredentials = ReturnType<typeof useAwsCredentials>;
