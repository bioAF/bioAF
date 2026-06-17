"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { setToken } from "@/lib/auth";
import { ComponentPicker, type PickerComponent } from "@/components/components/ComponentPicker";
import { AWS_REGIONS, DEFAULT_AWS_REGION } from "@/lib/aws-regions";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STEPS = [
  "Setup Code",
  "Create Admin Account",
  "Organization Name",
  "GCP Credentials",
  "SMTP Settings",
  "Infrastructure",
  "Select Stack",
  "Select Components",
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

// SA hardening: bioaf-bootstrap holds the broad project-level roles;
// bioaf-app holds a small set of scoped roles. The installer applies all
// of this automatically. These lists exist for transparency.
const SETUP_BOOTSTRAP_ROLES = [
  { role: "roles/storage.admin", description: "Storage Admin" },
  { role: "roles/pubsub.admin", description: "Pub/Sub Admin" },
  { role: "roles/container.admin", description: "Kubernetes Engine Admin" },
  { role: "roles/iam.serviceAccountUser", description: "Service Account User" },
  { role: "roles/iam.serviceAccountAdmin", description: "Service Account Admin" },
  { role: "roles/compute.admin", description: "Compute Admin" },
  { role: "roles/resourcemanager.projectIamAdmin", description: "Project IAM Admin" },
  { role: "roles/bigquery.dataEditor", description: "BigQuery Data Editor" },
  { role: "roles/artifactregistry.admin", description: "Artifact Registry Admin" },
  { role: "roles/cloudbuild.builds.editor", description: "Cloud Build Editor" },
  { role: "roles/logging.logWriter", description: "Logs Writer" },
  { role: "roles/serviceusage.serviceUsageAdmin", description: "Service Usage Admin" },
  { role: "roles/viewer", description: "Viewer" },
];

const SETUP_APP_ROLES = [
  { role: "roles/storage.admin", description: "Storage Admin (scoped to bioaf-* buckets)" },
  { role: "projects/<PROJECT>/roles/bioafSaManager", description: "Custom: list/delete bioaf-* SAs" },
  { role: "roles/compute.instanceAdmin.v1", description: "Compute (scoped to bioaf-* VMs)" },
  { role: "roles/container.admin", description: "Kubernetes Engine (scoped to bioaf-* clusters)" },
  { role: "roles/logging.logWriter", description: "Logs Writer" },
  { role: "roles/monitoring.metricWriter", description: "Monitoring Metric Writer (Ops Agent)" },
  { role: "roles/browser", description: "Project metadata read" },
  { role: "roles/serviceusage.serviceUsageViewer", description: "Service Usage Viewer" },
  { role: "roles/secretmanager.viewer", description: "Secret Manager metadata viewer" },
  { role: "roles/bigquery.jobUser", description: "BigQuery Job User" },
  { role: "roles/iam.serviceAccountTokenCreator", description: "Token Creator on bioaf-bootstrap only" },
];

// Legacy alias for any callers that still reference the single-list name.
const SETUP_RECOMMENDED_ROLES = SETUP_BOOTSTRAP_ROLES;

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
  // The install's cloud (gcp | aws), read from /api/bootstrap/status. We do NOT
  // use useStackOptions here: that endpoint needs an authenticated, permissioned
  // session and only fetches once at mount, but during first-run setup there is
  // no admin yet. bootstrap/status is the unauthenticated gate the /setup page
  // already hits, so it is available before the wizard creates an admin. Fails
  // safe to "gcp", so a GCP install renders exactly as before.
  const [cloudProvider, setCloudProvider] = useState("gcp");
  // The credentials step (index 3) is the only cloud-specific step; everything
  // else is shared.
  const isAws = cloudProvider === "aws";
  const cloudLabel = isAws ? "AWS" : "GCP";
  const credentialsStepLabel = `${cloudLabel} Credentials`;
  const displaySteps = STEPS.map((label, i) => (i === 3 ? credentialsStepLabel : label));
  // Provider-appropriate stack labels (GCP -> GKE+GCS, AWS -> EKS+S3).
  const k8sStackLabel = isAws ? "Kubernetes + S3" : "Kubernetes + GCS";
  const k8sComputeLabel = isAws ? "Kubernetes (EKS)" : "Kubernetes (GKE)";
  const k8sStorageLabel = isAws ? "S3" : "GCS";

  // Steps the user has already moved past at least once. Used to render a
  // Forward affordance and to short-circuit the per-step submit if the user
  // is just clicking through unchanged values after a Back.
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set());
  // Snapshot of the inputs at the moment each step was last submitted, so we
  // can detect "no real change" on a re-submit and skip the backend call.
  const [committedValues, setCommittedValues] = useState<Record<number, Record<string, string>>>({});

  const markStepCompleted = (idx: number, values: Record<string, string>) => {
    setCompletedSteps((prev) => {
      const next = new Set(prev);
      next.add(idx);
      return next;
    });
    setCommittedValues((prev) => ({ ...prev, [idx]: values }));
  };

  /** True when the step has been completed and current inputs match the
   * snapshot taken on its last successful submit. Lets re-submit handlers
   * skip the network call. */
  const isUnchangedSinceCommit = (idx: number, values: Record<string, string>): boolean => {
    if (!completedSteps.has(idx)) return false;
    const committed = committedValues[idx] ?? {};
    for (const k of Object.keys(values)) {
      if ((committed[k] ?? "") !== (values[k] ?? "")) return false;
    }
    return true;
  };

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
  // bioaf-bootstrap email; populated by either install-gcp.sh prefill or VM
  // metadata read on backend startup. Display-only -- the wizard never
  // overrides it.
  const [gcpBootstrapSaEmail, setGcpBootstrapSaEmail] = useState("");
  const [gcpPrefilled, setGcpPrefilled] = useState(false);
  const [gcpSaving, setGcpSaving] = useState(false);
  const [gcpConfigured, setGcpConfigured] = useState(false);
  const [gcpValidation, setGcpValidation] = useState<{
    passed: boolean;
    checks: { name: string; passed: boolean; message: string }[];
    permission_details: { permission: string; granted: boolean; recommended_role: string }[];
    app_probe: {
      sa_email: string | null;
      passed: boolean;
      checks: { name: string; passed: boolean; message: string }[];
      permission_details: { permission: string; granted: boolean; recommended_role: string }[];
    } | null;
    bootstrap_probe: {
      sa_email: string | null;
      passed: boolean;
      checks: { name: string; passed: boolean; message: string }[];
      permission_details: { permission: string; granted: boolean; recommended_role: string }[];
    } | null;
  } | null>(null);

  // Step 3 (AWS variant): the app authenticates through the EC2 instance profile,
  // so there is no key to enter; we confirm/adjust the account / region / role /
  // org_slug the installer persisted and validate that the ambient credentials
  // resolve (STS). Mirrors the GCP step's save+validate gate for the deploy step.
  const [awsAccountId, setAwsAccountId] = useState("");
  const [awsRegion, setAwsRegion] = useState(DEFAULT_AWS_REGION);
  const [awsAppRoleArn, setAwsAppRoleArn] = useState("");
  const [awsBootstrapRoleArn, setAwsBootstrapRoleArn] = useState("");
  const [awsOrgSlug, setAwsOrgSlug] = useState("");
  const [awsCredentialSource, setAwsCredentialSource] = useState("instance_profile");
  const [awsPrefilled, setAwsPrefilled] = useState(false);
  const [awsSaving, setAwsSaving] = useState(false);
  const [awsConfigured, setAwsConfigured] = useState(false);
  const [awsValidation, setAwsValidation] = useState<{
    passed: boolean;
    checks: { name: string; passed: boolean; message: string; status: string }[];
    account_id: string | null;
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

  // Step 7: Select Components
  // Defaults: the minimum set that gives the user a working "first pipeline
  // + first notebook" experience without opening the Infrastructure menu.
  // Keys match the canonical KUBERNETES_COMPONENTS list (the same 7 the
  // post-install Infrastructure > Components page renders).
  const DEFAULT_SELECTED = ["nextflow", "jupyterhub"];
  const [pickerComponents, setPickerComponents] = useState<PickerComponent[]>([]);
  const [selectedComponents, setSelectedComponents] = useState<string[]>(DEFAULT_SELECTED);
  const [componentsLoading, setComponentsLoading] = useState(false);
  const [componentsSubmitting, setComponentsSubmitting] = useState(false);

  // Step 8: Deploying status snapshot
  const [deployStatus, setDeployStatus] = useState<
    { key: string; name: string; status: string }[]
  >([]);

  // Learn the install's cloud as early as possible (pre-auth) so the
  // credentials step + stack labels match. bootstrap/status is the
  // unauthenticated gate the /setup page already hits.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await api.get<{ cloud_provider?: string }>("/api/bootstrap/status");
        if (!cancelled && status?.cloud_provider) setCloudProvider(status.cloud_provider);
      } catch {
        // Unreachable pre-backend -- keep the gcp default.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Pre-populate GCP fields from platform_config once the user has
  // authenticated (we have a token after step 1). install-gcp.sh's prefill
  // path writes project/region/zone/credential-source/bootstrap-email to
  // platform_config, and the metadata-server fallback writes the bootstrap
  // email on first backend startup. Either way we want the wizard to mirror
  // what the system already knows so the user doesn't re-type values.
  useEffect(() => {
    let cancelled = false;
    if (step < 3 || isAws) return;
    (async () => {
      try {
        const cfg = await api.get<{
          gcp_project_id: string | null;
          gcp_region: string | null;
          gcp_zone: string | null;
          org_slug: string | null;
          gcp_credential_source: string;
          gcp_service_account_email: string | null;
          gcp_bootstrap_sa_email: string | null;
        }>("/api/v1/settings/gcp");
        if (cancelled) return;
        let prefilled = false;
        if (cfg.gcp_project_id && !gcpProjectId) {
          setGcpProjectId(cfg.gcp_project_id);
          prefilled = true;
        }
        if (cfg.gcp_region) {
          setGcpRegion(cfg.gcp_region);
        }
        if (cfg.gcp_zone) {
          setGcpZone(cfg.gcp_zone);
        }
        if (cfg.org_slug && !gcpOrgSlug) {
          setGcpOrgSlug(cfg.org_slug);
        }
        if (cfg.gcp_credential_source === "service_account_key" || cfg.gcp_credential_source === "vm_default") {
          setGcpCredentialSource(cfg.gcp_credential_source);
        }
        if (cfg.gcp_bootstrap_sa_email) {
          setGcpBootstrapSaEmail(cfg.gcp_bootstrap_sa_email);
          prefilled = true;
        }
        if (prefilled) setGcpPrefilled(true);
      } catch {
        // Endpoint isn't reachable yet (e.g. first render before auth) -- ignore.
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, isAws]);

  // AWS analog of the GCP prefill: mirror what install-aws.sh persisted (account
  // / region / role ARNs / org_slug) so the user confirms rather than retypes.
  useEffect(() => {
    let cancelled = false;
    if (step < 3 || !isAws) return;
    (async () => {
      try {
        const cfg = await api.get<{
          aws_account_id: string | null;
          aws_region: string | null;
          aws_app_role_arn: string | null;
          aws_bootstrap_role_arn: string | null;
          org_slug: string | null;
          aws_credential_source: string;
        }>("/api/v1/settings/aws");
        if (cancelled) return;
        let prefilled = false;
        if (cfg.aws_account_id && !awsAccountId) {
          setAwsAccountId(cfg.aws_account_id);
          prefilled = true;
        }
        if (cfg.aws_region) {
          setAwsRegion(cfg.aws_region);
        }
        if (cfg.aws_app_role_arn && !awsAppRoleArn) {
          setAwsAppRoleArn(cfg.aws_app_role_arn);
          prefilled = true;
        }
        if (cfg.aws_bootstrap_role_arn && !awsBootstrapRoleArn) {
          setAwsBootstrapRoleArn(cfg.aws_bootstrap_role_arn);
        }
        if (cfg.org_slug && !awsOrgSlug) {
          setAwsOrgSlug(cfg.org_slug);
        }
        if (cfg.aws_credential_source) {
          setAwsCredentialSource(cfg.aws_credential_source);
        }
        if (prefilled) setAwsPrefilled(true);
      } catch {
        // Endpoint isn't reachable yet (e.g. first render before auth) -- ignore.
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, isAws]);

  // --- Handlers ---

  const handleVerifyCode = async () => {
    setError("");
    if (isUnchangedSinceCommit(0, { setupCode })) {
      setStep(1);
      return;
    }
    if (completedSteps.has(0) && !confirm("Re-verify the setup code with the new value?")) {
      return;
    }
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
      markStepCompleted(0, { setupCode });
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
    const current = { email, password, name };
    if (isUnchangedSinceCommit(1, current)) {
      setStep(2);
      return;
    }
    if (completedSteps.has(1)) {
      const c = committedValues[1] ?? {};
      const diff = [
        c.email !== email ? `Email: ${c.email || "(empty)"} -> ${email}` : null,
        c.name !== name ? `Name: ${c.name || "(empty)"} -> ${name || "(empty)"}` : null,
        c.password !== password ? "Password updated" : null,
      ].filter(Boolean).join("\n");
      if (!confirm(`This will overwrite the admin account.\n\n${diff}\n\nContinue?`)) return;
    }
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
      markStepCompleted(1, current);
      setStep(2);
    } catch {
      setError("Failed to create admin");
    }
  };

  const handleConfigureOrg = async () => {
    setError("");
    const current = { orgName };
    if (isUnchangedSinceCommit(2, current)) {
      setStep(3);
      return;
    }
    if (completedSteps.has(2)) {
      const c = committedValues[2] ?? {};
      if (!confirm(`This will overwrite the organization name.\n\n${c.orgName} -> ${orgName}\n\nContinue?`)) return;
    }
    try {
      await api.post("/api/bootstrap/configure-org", { org_name: orgName });
      markStepCompleted(2, current);
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
        markStepCompleted(3, {
          gcpProjectId,
          gcpRegion,
          gcpZone,
          gcpOrgSlug,
          gcpCredentialSource,
        });
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

  const handleSaveAws = async () => {
    setError("");
    setAwsValidation(null);
    setAwsSaving(true);
    try {
      await api.put("/api/v1/settings/aws", {
        aws_account_id: awsAccountId || undefined,
        aws_region: awsRegion,
        aws_app_role_arn: awsAppRoleArn || undefined,
        aws_bootstrap_role_arn: awsBootstrapRoleArn || undefined,
        org_slug: awsOrgSlug || undefined,
      });
      const result = await api.post<typeof awsValidation>("/api/v1/settings/aws/validate");
      setAwsValidation(result);
      if (result?.passed) {
        setAwsConfigured(true);
        markStepCompleted(3, { awsAccountId, awsRegion, awsOrgSlug });
        setStep(4);
      } else {
        setError("Validation failed. Fix the issues below and try again.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save AWS configuration");
    } finally {
      setAwsSaving(false);
    }
  };

  const handleConfigureSmtp = async () => {
    setError("");
    const current = { smtpHost, smtpPort, smtpUsername, smtpPassword, smtpFrom };
    if (isUnchangedSinceCommit(4, current)) {
      setStep(5);
      return;
    }
    if (completedSteps.has(4)) {
      if (!confirm("This will overwrite the previously saved SMTP settings. Continue?")) return;
    }
    try {
      await api.post("/api/bootstrap/configure-smtp", {
        host: smtpHost,
        port: parseInt(smtpPort),
        username: smtpUsername,
        password: smtpPassword,
        from_address: smtpFrom,
      });
      markStepCompleted(4, current);
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
      // Bootstrap completion is deferred until after the user has submitted
      // their component selections, so an interrupted wizard does not look
      // "complete" while still half-configured.
      setStep(7);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to initialize infrastructure");
    } finally {
      setStackDeploying(false);
    }
  };

  const handleSelectComponents = async () => {
    setError("");
    setComponentsSubmitting(true);
    try {
      await api.post("/api/components/select-batch", {
        keys: selectedComponents,
      });
      try {
        await api.post("/api/bootstrap/complete");
      } catch {
        // Non-critical; the deploy step renders regardless.
      }
      setStep(8);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to queue components");
    } finally {
      setComponentsSubmitting(false);
    }
  };

  const handlePickerChange = useCallback((keys: string[]) => {
    setSelectedComponents(keys);
  }, []);

  // Poll per-component status on the Deploying step so the user can see
  // image builds completing, the cluster coming up, components flipping
  // enabled. The orchestrator runs on the backend; this is just a window.
  useEffect(() => {
    if (step !== 8) return;
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const data = await api.get<{
          components: { key: string; name: string; status: string }[];
        }>("/api/components");
        if (cancelled) return;
        const selected = new Set(selectedComponents);
        setDeployStatus(
          data.components
            .filter((c) => selected.has(c.key))
            .map((c) => ({ key: c.key, name: c.name, status: c.status }))
        );
      } catch {
        // Polling failures are benign; the next tick will retry.
      }
    };
    void fetchStatus();
    const handle = setInterval(fetchStatus, 8000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [step, selectedComponents]);

  // Fetch the component catalog when entering step 7 so the picker has data.
  // Hits the same endpoint the post-install components page uses so the two
  // views are guaranteed to show the same 7 K8s components, no more, no less.
  useEffect(() => {
    let cancelled = false;
    if (step !== 7) return;
    setComponentsLoading(true);
    (async () => {
      try {
        const data = await api.get<{
          compute_stack: string | null;
          components: {
            key: string;
            name: string;
            description: string;
            category: string;
            dependencies: string[];
            cost_estimate: string;
            status: string; // "enabled" | "disabled" | "provisioning" | "build_failed" | "coming_soon"
          }[];
        }>("/api/v1/infrastructure/stack/components");
        if (cancelled) return;
        // The endpoint's "status" is the runtime state; the picker only cares
        // about whether the component is selectable. Anything that is not
        // coming_soon is selectable on the wizard.
        const mapped: PickerComponent[] = (data.components ?? []).map((c) => ({
          key: c.key,
          name: c.name,
          description: c.description,
          category: c.category,
          dependencies: c.dependencies,
          cost_estimate: c.cost_estimate,
          status: c.status === "coming_soon" ? "coming_soon" : "available",
        }));
        setPickerComponents(mapped);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load components");
      } finally {
        if (!cancelled) setComponentsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [step]);

  return (
    <div className="bg-white shadow rounded-lg p-8">
      {/* Step indicator */}
      <div className="flex items-center justify-between mb-8">
        {displaySteps.map((label, i) => (
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

      <h2 className="text-xl font-semibold mb-4">{displaySteps[step]}</h2>

      {/* Back / Forward: allowed only on steps 1-6 (everything before TF deploy
          fires). Once Select Stack -> Continue triggers terraform/init and
          stack/deploy-background, the user is past the point of no return.
          Forward only renders for steps the user has already moved past, so
          they can go back to verify a value and return without re-submitting. */}
      {step >= 1 && step <= 6 && (
        <div className="mb-4 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setStep(step - 1)}
            aria-label="Back"
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            <span aria-hidden="true">&larr; </span>Back
          </button>
          {completedSteps.has(step) && (
            <button
              type="button"
              onClick={() => setStep(step + 1)}
              aria-label="Forward"
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Forward<span aria-hidden="true"> &rarr;</span>
            </button>
          )}
        </div>
      )}

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

      {/* Step 3: GCP Credentials (rendered on a GCP install) */}
      {step === 3 && !isAws && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-2">
            Configure your Google Cloud Platform project. Credentials are required before
            deploying a compute stack.
          </p>

          <details data-testid="gcp-prerequisites" className="bg-gray-50 border rounded p-4">
            <summary className="cursor-pointer text-sm font-semibold text-gray-700 select-none">
              Prerequisites: IAM Roles &amp; APIs
              <span className="ml-1 text-xs font-normal text-gray-400">
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
                      <span className="text-gray-400">{description}</span>
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
                      Bootstrap SA Email <span className="ml-1 text-gray-400 font-normal text-xs">(optional)</span>
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
                        <span className="text-gray-400">(needs {p.recommended_role})</span>
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
                        <span className="text-gray-400">(needs {p.recommended_role})</span>
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

      {/* Step 3: AWS Credentials (rendered on an AWS install) */}
      {step === 3 && isAws && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 mb-2">
            Confirm the AWS account this install runs on. The app authenticates through
            the EC2 instance profile, so there is nothing to paste here: we validate that
            the ambient credentials resolve before deploying a compute stack.
          </p>

          {awsPrefilled && (
            <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
              <p className="font-medium mb-1">Pre-populated from install-aws.sh</p>
              <p>
                We&apos;ve filled in the account, region, and role from the AWS installer
                you just ran. You can change anything here, but the defaults match what
                we just created.
              </p>
            </div>
          )}

          <div>
            <label htmlFor="aws-account-id" className="block text-sm font-medium text-gray-700 mb-1">AWS Account ID</label>
            <input id="aws-account-id" type="text" value={awsAccountId}
              onChange={(e) => setAwsAccountId(e.target.value)} placeholder="123456789012"
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>

          <div>
            <label htmlFor="aws-region" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
            <select id="aws-region" value={awsRegion} onChange={(e) => setAwsRegion(e.target.value)}
              className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500">
              {AWS_REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>

          <div>
            <label htmlFor="aws-org-slug" className="block text-sm font-medium text-gray-700 mb-1">
              Organization Slug
              <span className="ml-1 text-gray-400 font-normal text-xs">(3-30 chars, lowercase, hyphens allowed)</span>
            </label>
            <input id="aws-org-slug" type="text" value={awsOrgSlug} onChange={(e) => setAwsOrgSlug(e.target.value)}
              placeholder="my-bioaf-org" className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500" />
          </div>

          <div>
            <label htmlFor="aws-app-role-arn" className="block text-sm font-medium text-gray-700 mb-1">
              App Role ARN
              <span className="ml-1 text-gray-400 font-normal text-xs">(runtime / EC2 instance profile)</span>
            </label>
            <input id="aws-app-role-arn" type="text" value={awsAppRoleArn}
              onChange={(e) => setAwsAppRoleArn(e.target.value)}
              placeholder="arn:aws:iam::123456789012:role/bioaf-app"
              className="w-full px-3 py-2 border rounded font-mono text-xs focus:ring-2 focus:ring-bioaf-500" />
          </div>

          <div className="bg-gray-50 border rounded p-3 text-xs text-gray-600">
            Authentication: <code className="bg-white px-1 rounded">{awsCredentialSource}</code>. The app reads the
            EC2 instance profile via IMDS; no access key is stored. install-aws.sh provisioned the role.
          </div>

          <button onClick={handleSaveAws} disabled={awsSaving}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50">
            {awsSaving ? "Validating..." : "Save & Validate"}
          </button>

          {awsValidation && !awsValidation.passed && (
            <div className="border rounded divide-y text-sm">
              <div className="p-3 bg-red-50">
                <h4 className="font-semibold text-red-800">Validation Failed</h4>
              </div>
              <div className="p-3 space-y-1.5">
                {awsValidation.checks.map((c) => (
                  <div key={c.name} className="flex items-start gap-2 text-xs">
                    <span className={c.passed ? "text-green-600" : "text-red-600"}>
                      {c.passed ? "✓" : "✗"}
                    </span>
                    <span>
                      <span className="font-medium">{c.name}</span>{" "}
                      <span className="text-gray-500">{c.message}</span>
                    </span>
                  </div>
                ))}
              </div>
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
            cluster, storage buckets, and supporting resources on {cloudLabel}.
          </p>
          {!(isAws ? awsConfigured : gcpConfigured) && (
            <p className="text-sm text-amber-600">
              {cloudLabel} credentials are required to set up infrastructure. You can configure them
              later in Settings.
            </p>
          )}
          <button
            onClick={handleSetupInfrastructure}
            disabled={!(isAws ? awsConfigured : gcpConfigured)}
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
                <h3 className="font-semibold text-gray-900">{k8sStackLabel}</h3>
                <span className="text-xs bg-bioaf-100 text-bioaf-700 px-2 py-0.5 rounded-full font-medium">
                  Recommended
                </span>
              </div>
              <p className="text-sm text-gray-600">
                Cloud-native autoscaling with managed{" "}
                {k8sComputeLabel} and {k8sStorageLabel} object storage.
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
            {stackDeploying ? "Initializing infrastructure..." : `Continue with ${computeStack === "kubernetes" ? k8sStackLabel : "SLURM + NFS"}`}
          </button>
        </div>
      )}

      {/* Step 7: Select Components */}
      {step === 7 && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Pick the components you want enabled. Selected components will be
            queued and turned on automatically as the infrastructure becomes
            ready, so you can leave this page once you continue.
          </p>
          {componentsLoading ? (
            <div className="text-sm text-gray-500">Loading components...</div>
          ) : (
            <ComponentPicker
              components={pickerComponents}
              defaultSelected={DEFAULT_SELECTED}
              onChange={handlePickerChange}
            />
          )}
          <button
            onClick={handleSelectComponents}
            disabled={componentsSubmitting || componentsLoading}
            className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {componentsSubmitting ? "Queueing components..." : "Continue"}
          </button>
        </div>
      )}

      {/* Step 8: Deploying */}
      {step === 8 && (
        <div className="space-y-4">
          <div className="p-4 bg-blue-50 border border-blue-200 rounded">
            <p className="text-sm text-blue-800">
              Infrastructure deployment has started. This usually takes 10-15 minutes.
              Your selected components will turn on automatically as their
              prerequisites become ready; you do not need to come back here.
            </p>
          </div>
          {deployStatus.length > 0 && (
            <div className="border border-gray-200 rounded divide-y">
              {deployStatus.map((c) => (
                <div
                  key={c.key}
                  className="flex items-center justify-between px-3 py-2 text-sm"
                  data-testid={`deploy-status-${c.key}`}
                >
                  <span className="font-medium">{c.name}</span>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      c.status === "enabled"
                        ? "bg-green-100 text-green-700"
                        : c.status === "build_failed"
                          ? "bg-red-100 text-red-700"
                          : c.status === "provisioning"
                            ? "bg-amber-100 text-amber-700"
                            : "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {c.status === "enabled"
                      ? "Ready"
                      : c.status === "build_failed"
                        ? "Build failed"
                        : c.status === "provisioning"
                          ? "Building"
                          : c.status === "queued_for_infra"
                            ? "Queued"
                            : c.status}
                  </span>
                </div>
              ))}
            </div>
          )}
          <button onClick={() => setStep(9)} className="w-full bg-bioaf-600 text-white py-2 rounded hover:bg-bioaf-700">
            Continue to Getting Started
          </button>
        </div>
      )}

      {/* Step 9: Getting Started */}
      {step === 9 && (
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
