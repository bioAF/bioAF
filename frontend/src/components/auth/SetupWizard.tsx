"use client";

import { useConfirm } from "@/hooks/useConfirm";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { logError } from "@/lib/errorReporting";
import {
  INSTALL_METRICS_NOTICE_VERSION,
  fetchSystemSettings,
  isChoiceConflict,
  saveInstallMetricsChoice,
  type InstallMetricsChoice,
  type InstallMetricsSettings,
} from "@/lib/installMetrics";
import { endSession, startSession } from "@/lib/session";
import { navigateToLogin } from "@/lib/navigation";
import { OperationStatusModal } from "@/components/infrastructure/OperationStatusModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  ExistingDeploymentStep,
  ResumeSummary,
  deploymentStateLabel,
  isActiveOperation,
  type SetupOperation,
} from "@/components/auth/setup/ResumeSteps";
import type { PickerComponent } from "@/components/components/ComponentPicker";
import { apiUrl } from "@/lib/apiUrl";
import { STEPS, DEFAULT_SELECTED_COMPONENTS } from "@/components/auth/setup/setupConstants";
import {
  NO_COMMITS,
  adminChangeSummary,
  commitStep,
  unchangedSinceCommit,
  type StepCommits,
  type StepValues,
} from "@/components/auth/setup/stepCommits";
import { useAwsCredentials, useGcpCredentials } from "@/components/auth/setup/useCloudCredentials";
import { AdminAccountStep, OrganizationStep, SetupCodeStep } from "@/components/auth/setup/AccountSteps";
import { GcpCredentialsStep } from "@/components/auth/setup/GcpCredentialsStep";
import { AwsCredentialsStep } from "@/components/auth/setup/AwsCredentialsStep";
import { SmtpStep, type SmtpFields } from "@/components/auth/setup/SmtpStep";
import {
  ComponentsStep,
  DeployingStep,
  GettingStartedStep,
  InfrastructureDecisionStep,
  StackStep,
} from "@/components/auth/setup/InfrastructureSteps";

interface SetupWizardProps {
  onComplete: () => void;
  /** ``resume``: an authorized administrator reviewing an unfinished installation's saved setup. */
  mode?: "initial" | "resume";
}

interface SavedSmtp {
  host: string;
  port: number;
  username: string;
  from_address: string;
  encryption: string;
  configured: boolean;
}

/** What an unfinished installation already has, read once when an administrator resumes setup. */
interface SavedSetup {
  orgName: string;
  smtp: SavedSmtp;
  enabledComponents: string[];
  activeOperation: SetupOperation | null;
  busy: boolean;
  /** The latest compute deployment, or null when there is none (or history could not be read). */
  deployment: SetupOperation | null;
  historyKnown: boolean;
}

interface StackComponentsResponse {
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
}

/**
 * First-run setup. This root owns step progression, the committed-value comparisons, every value
 * entered (credentials included, in memory only, for the wizard's lifetime), and the transition to
 * the first admin's session. The step views only render; showing a step again never repeats a
 * request. Each step's submit runs once however fast it is pressed.
 */
export function SetupWizard({ onComplete, mode = "initial" }: SetupWizardProps) {
  const confirm = useConfirm();
  const resume = mode === "resume";
  // Resuming starts at the summary card, over the organization step: the setup code and admin
  // account already exist and are never shown again.
  const [step, setStep] = useState(resume ? 2 : 0);
  const [reviewing, setReviewing] = useState(resume);
  const firstReviewStep = resume ? 2 : 0;
  const [error, setError] = useState("");
  const stepGuard = useSubmitGuard();
  const submit = (work: () => Promise<void>) => () => void stepGuard.run(work);

  // The install's cloud (gcp | aws), read from /api/bootstrap/status. We do NOT use
  // useStackOptions here: that endpoint needs an authenticated, permissioned session and only
  // fetches once at mount, but during first-run setup there is no admin yet. bootstrap/status is
  // the unauthenticated gate the /setup page already hits, so it is available before the wizard
  // creates an admin. Fails safe to "gcp", so a GCP install renders exactly as before.
  const status = useResource<{ cloud_provider?: string }>("setup:bootstrap-status", (signal) =>
    api.get<{ cloud_provider?: string }>("/api/bootstrap/status", { signal }),
  );
  const cloudProvider = status.data?.cloud_provider || "gcp";
  // The credentials step (index 3) is the only cloud-specific step; everything else is shared.
  const isAws = cloudProvider === "aws";
  const cloudLabel = isAws ? "AWS" : "GCP";
  const credentialsStepLabel = `${cloudLabel} Credentials`;
  const displaySteps = STEPS.map((label, i) => (i === 3 ? credentialsStepLabel : label));
  // Provider-appropriate stack labels (GCP -> GKE+GCS, AWS -> EKS+S3).
  const k8sStackLabel = isAws ? "Kubernetes + S3" : "Kubernetes + GCS";
  const k8sComputeLabel = isAws ? "Kubernetes (EKS)" : "Kubernetes (GKE)";
  const k8sStorageLabel = isAws ? "S3" : "GCS";

  // Steps the user has already moved past at least once, and the inputs each was last submitted
  // with: a Forward affordance, and a re-submit of unchanged values skips the backend call.
  const [commits, setCommits] = useState<StepCommits>(NO_COMMITS);
  const markStepCompleted = (idx: number, values: StepValues) => setCommits((c) => commitStep(c, idx, values));
  const isUnchangedSinceCommit = (idx: number, values: StepValues) => unchangedSinceCommit(commits, idx, values);

  // Step 0: Setup code. The setup token authorizes only admin creation, before any session.
  const [setupCode, setSetupCode] = useState("");
  const [setupToken, setSetupToken] = useState("");

  // Step 1: Admin creation
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [name, setName] = useState("");

  // Step 2: Org name, and the installation metrics choice (ADR-075). Nothing is preselected; the
  // choice is saved before the name and, once saved, never asked again.
  const [orgName, setOrgName] = useState("");
  const [metricsChoice, setMetricsChoice] = useState<InstallMetricsChoice | null>(null);
  const [metricsCommitted, setMetricsCommitted] = useState<InstallMetricsChoice | null>(null);
  // True when the saved choice is another administrator's, not the one made on this step.
  const [metricsChosenElsewhere, setMetricsChosenElsewhere] = useState(false);

  // Step 3: cloud credentials. Mirror what the installer persisted, read once authenticated.
  const gcp = useGcpCredentials(step >= 3 && !isAws ? `setup:gcp-settings:${step}` : null);
  const aws = useAwsCredentials(step >= 3 && isAws ? `setup:aws-settings:${step}` : null);

  // Step 4: SMTP
  const [smtp, setSmtp] = useState<SmtpFields>({
    smtpHost: "",
    smtpPort: "587",
    smtpUsername: "",
    smtpPassword: "",
    smtpFrom: "",
  });
  // Kept as saved: the step has no encryption control, and a save must not reset it to the default.
  const [smtpEncryption, setSmtpEncryption] = useState("starttls");

  // Step 6: Compute stack
  const [computeStack, setComputeStack] = useState("kubernetes");
  const [stackDeploying, setStackDeploying] = useState(false);

  // Step 7: Select Components
  const [selectedComponents, setSelectedComponents] = useState<string[]>(DEFAULT_SELECTED_COMPONENTS);
  const [componentsSubmitting, setComponentsSubmitting] = useState(false);

  // --- Resuming: saved settings, read once ---

  const saved = useResource<SavedSetup>(resume ? "setup:saved" : null, async (signal) => {
    const [org, smtpSaved, components, active] = await Promise.all([
      api.get<{ org_name: string }>("/api/bootstrap/org-settings", { signal }),
      api.get<SavedSmtp>("/api/bootstrap/smtp-settings", { signal }),
      api.get<{ components: { key: string; enabled?: boolean }[] }>("/api/components", { signal }),
      api.get<{ busy: boolean; operation?: SetupOperation | null }>("/api/v1/infrastructure/operations/active", {
        signal,
      }),
    ]);
    // History is optional: when it cannot be read, nothing claims there was no deployment, and
    // deployment actions stay unavailable.
    let deployment: SetupOperation | null = null;
    let historyKnown = true;
    try {
      const history = await api.get<{ operations: SetupOperation[] }>(
        "/api/v1/infrastructure/operations?action=compute_deploy&limit=1",
        { signal },
      );
      deployment = history.operations[0] ?? null;
    } catch (e) {
      logError("reading the deployment history", e);
      historyKnown = false;
    }
    const activeOperation = active.operation ?? null;
    if (activeOperation?.action === "compute_deploy" && (!deployment || deployment.id <= activeOperation.id)) {
      deployment = activeOperation;
    }
    return {
      orgName: org.org_name,
      smtp: smtpSaved,
      enabledComponents: components.components.filter((c) => c.enabled).map((c) => c.key),
      activeOperation,
      busy: active.busy,
      deployment,
      historyKnown,
    };
  });

  // Fill the form from the saved settings once, and record them as this session's committed values,
  // so continuing with them unchanged writes nothing.
  const hydrated = useRef(false);
  useEffect(() => {
    const data = saved.data;
    if (!data || hydrated.current) return;
    hydrated.current = true;
    setOrgName(data.orgName);
    setCommits((c) => commitStep(c, 2, { orgName: data.orgName }));
    if (data.smtp.configured) {
      const fields: SmtpFields = {
        smtpHost: data.smtp.host,
        smtpPort: String(data.smtp.port),
        smtpUsername: data.smtp.username,
        smtpPassword: "", // blank keeps the stored secret; the masked value is never sent back
        smtpFrom: data.smtp.from_address,
      };
      setSmtp(fields);
      setSmtpEncryption(data.smtp.encryption || "starttls");
      setCommits((c) => commitStep(c, 4, { ...fields }));
    }
    if (data.enabledComponents.length > 0) setSelectedComponents(data.enabledComponents);
  }, [saved.data]);

  const [viewingOperation, setViewingOperation] = useState<number | null>(null);
  const deployment = saved.data?.deployment ?? null;
  const enabledComponents = saved.data?.enabledComponents ?? [];

  // --- Completion: advances only once the server confirms it ---

  const [completionFailed, setCompletionFailed] = useState(false);
  const componentsQueued = useRef(false);

  const completeSetup = async (): Promise<boolean> => {
    setCompletionFailed(false);
    try {
      await api.post("/api/bootstrap/complete");
      return true;
    } catch (e) {
      logError("marking setup complete", e);
      // The answer may have been lost after the server completed it.
      try {
        const current = await api.get<{ setup_complete?: boolean }>("/api/bootstrap/status");
        if (current.setup_complete) return true;
      } catch (readError) {
        logError("rereading the setup status", readError);
      }
      setCompletionFailed(true);
      return false;
    }
  };

  // --- Handlers ---

  const handleVerifyCode = async () => {
    setError("");
    if (isUnchangedSinceCommit(0, { setupCode })) {
      setStep(1);
      return;
    }
    if (commits.completed.has(0)) {
      const ok = await confirm({
        title: "Re-verify the setup code with the new value?",
        message: "The code you already verified is replaced by the one entered now.",
        confirmLabel: "Re-verify",
      });
      if (!ok) return;
    }
    try {
      // Use raw fetch since the api module auto-redirects on 401
      const resp = await fetch(apiUrl("/api/bootstrap/verify-setup-code"), {
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
    if (commits.completed.has(1)) {
      const diff = adminChangeSummary(commits.values[1] ?? {}, current);
      const ok = await confirm({
        title: "Overwrite the admin account?",
        message: (
          <>
            <p>This replaces the admin account you already created:</p>
            <p className="whitespace-pre-line font-medium text-gray-900">{diff}</p>
          </>
        ),
        confirmLabel: "Overwrite",
        variant: "danger",
      });
      if (!ok) return;
    }
    try {
      // Use raw fetch with setup token (not the stored auth token)
      const resp = await fetch(apiUrl("/api/bootstrap/create-admin"), {
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
      startSession(data.access_token);
      markStepCompleted(1, current);
      setStep(2);
    } catch {
      setError("Failed to create admin");
    }
  };

  /** The installation's current metrics setting; null when it cannot be read. */
  const readMetrics = async (): Promise<InstallMetricsSettings | null> => {
    try {
      return (await fetchSystemSettings())?.install_metrics ?? null;
    } catch (readError) {
      logError("reading the installation metrics setting", readError);
      return null;
    }
  };

  /** Adopt a choice another administrator already saved; it stands for this installation. */
  const adoptStoredChoice = (preference: InstallMetricsChoice) => {
    setMetricsCommitted(preference);
    setMetricsChosenElsewhere(true);
  };

  // Show a decision already stored for this installation as soon as the step opens.
  useEffect(() => {
    if (step !== 2 || metricsCommitted) return;
    let active = true;
    void readMetrics().then((current) => {
      if (active && (current?.preference === "enabled" || current?.preference === "disabled")) {
        adoptStoredChoice(current.preference);
      }
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  /**
   * Save the metrics choice at the revision the server reports (an identity reset leaves it pending at a
   * later revision); true once a choice is committed. A different choice another administrator committed
   * meanwhile stands and is shown first (false, so the step does not advance unseen); a conflict that
   * leaves the choice pending retries once.
   */
  const commitMetricsChoice = async (): Promise<boolean> => {
    if (metricsCommitted) return true;
    if (!metricsChoice) return false;
    let current = await readMetrics();
    for (let attempt = 0; ; attempt++) {
      if (current?.preference === "enabled" || current?.preference === "disabled") {
        adoptStoredChoice(current.preference);
        return current.preference === metricsChoice;
      }
      try {
        const saved = await saveInstallMetricsChoice(
          { notice_version: INSTALL_METRICS_NOTICE_VERSION, revision: current?.revision ?? 0 },
          metricsChoice,
        );
        setMetricsCommitted((saved?.install_metrics?.preference as InstallMetricsChoice | undefined) ?? metricsChoice);
        return true;
      } catch (e) {
        if (!isChoiceConflict(e) || attempt > 0) {
          logError("saving the installation metrics choice", e);
          break;
        }
        current = await readMetrics();
      }
    }
    setError("Your installation metrics choice was not saved. Try again.");
    return false;
  };

  const handleConfigureOrg = async () => {
    setError("");
    if (!(await commitMetricsChoice())) return;
    const current = { orgName };
    if (isUnchangedSinceCommit(2, current)) {
      setStep(3);
      return;
    }
    if (commits.completed.has(2)) {
      const c = commits.values[2] ?? {};
      const ok = await confirm({
        title: "Overwrite the organization name?",
        message: (
          <>
            <p>This replaces the name you already saved:</p>
            <p className="font-medium text-gray-900">
              {c.orgName} -&gt; {orgName}
            </p>
          </>
        ),
        confirmLabel: "Overwrite",
        variant: "danger",
      });
      if (!ok) return;
    }
    try {
      await api.post("/api/bootstrap/configure-org", { org_name: orgName });
      markStepCompleted(2, current);
      setStep(3);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure org");
    }
  };

  const saveCredentials = async () => {
    setError("");
    const cloud = isAws ? aws : gcp;
    try {
      if (await cloud.saveAndValidate()) {
        markStepCompleted(3, cloud.committedValues);
        setStep(4);
      } else {
        setError("Validation failed. Fix the issues below and try again.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : `Failed to save ${cloudLabel} configuration`);
    }
  };

  const handleConfigureSmtp = async () => {
    setError("");
    const current = { ...smtp };
    if (isUnchangedSinceCommit(4, current)) {
      setStep(5);
      return;
    }
    if (commits.completed.has(4)) {
      const ok = await confirm({
        title: "Overwrite the saved SMTP settings?",
        message: "The settings you already saved are replaced by the values entered now.",
        confirmLabel: "Overwrite",
        variant: "danger",
      });
      if (!ok) return;
    }
    try {
      await api.post("/api/bootstrap/configure-smtp", {
        host: smtp.smtpHost,
        port: parseInt(smtp.smtpPort),
        username: smtp.smtpUsername,
        password: smtp.smtpPassword,
        from_address: smtp.smtpFrom,
        encryption: smtpEncryption,
      });
      markStepCompleted(4, current);
      setStep(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure SMTP");
    }
  };

  const handleDoInfraLater = async () => {
    if (await completeSetup()) setStep(8);
  };

  const handleSelectStack = async () => {
    // The most expensive and least reversible action in the product, and it was
    // an ordinary primary button. Re-typing a six-character setup code, two steps
    // earlier, got a full confirmation dialog.
    const ok = await confirm({
      title: "Create cloud infrastructure now?",
      message: (
        <>
          <p>
            This provisions real infrastructure in your cloud account and
            <span className="font-medium"> starts incurring charges</span>. It usually takes
            10 to 15 minutes.
          </p>
          <p>
            Undoing it means tearing the stack down again from the Infrastructure page, which
            is not instant and not always clean.
          </p>
        </>
      ),
      confirmLabel: "Create infrastructure",
    });
    if (!ok) return;

    setError("");
    setStackDeploying(true);
    try {
      await api.post("/api/v1/infrastructure/terraform/init");
      // This POST used to be wrapped in a bare `catch {}` and the wizard advanced
      // regardless, so step 8 asserted "Infrastructure deployment has started"
      // whether or not it had. A user whose deploy never started would wait 15
      // minutes and then go hunting in Infrastructure for something that was
      // never queued. If it did not start, say so and stay put.
      await api.post("/api/v1/infrastructure/stack/deploy-background", {
        stack_type: computeStack,
      });
      // Bootstrap completion is deferred until after the user has submitted
      // their component selections, so an interrupted wizard does not look
      // "complete" while still half-configured.
      setStep(7);
    } catch (e) {
      logError("starting the cloud stack deployment", e);
      setError(
        "Infrastructure could not be started, so nothing has been created. The technical detail is in the application logs.",
      );
    } finally {
      setStackDeploying(false);
    }
  };

  // One key per explicit retry, kept while its answer is unknown so a resend cannot start another.
  const retryKey = useRef<string | null>(null);
  const handleRetryDeployment = async () => {
    const ok = await confirm({
      title: "Retry deployment?",
      message: (
        <p>
          This provisions real infrastructure in your cloud account and
          <span className="font-medium"> starts incurring charges</span>. It usually takes 10 to 15 minutes.
        </p>
      ),
      confirmLabel: "Retry deployment",
    });
    if (!ok) return;
    setError("");
    retryKey.current ??= crypto.randomUUID();
    try {
      await api.post(
        "/api/v1/infrastructure/stack/deploy-background",
        { stack_type: computeStack },
        { idempotencyKey: retryKey.current },
      );
      retryKey.current = null;
      saved.refresh();
      setStep(7);
    } catch (e) {
      logError("retrying the cloud stack deployment", e);
      saved.refresh(); // decide from what the server now records before offering another attempt
      setError(
        "Infrastructure could not be started, so nothing has been created. The technical detail is in the application logs.",
      );
    }
  };

  const handleSelectComponents = async () => {
    setError("");
    setComponentsSubmitting(true);
    try {
      if (!componentsQueued.current) {
        // The endpoint only adds: send what is new, never what is already enabled.
        const additions = selectedComponents.filter((k) => !enabledComponents.includes(k));
        if (additions.length > 0 || enabledComponents.length === 0) {
          await api.post("/api/components/select-batch", { keys: additions });
        }
        componentsQueued.current = true;
      }
      if (await completeSetup()) setStep(8);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to queue components");
    } finally {
      setComponentsSubmitting(false);
    }
  };

  const handlePickerChange = useCallback((keys: string[]) => {
    setSelectedComponents(keys);
  }, []);

  // The component catalog, read while the Select Components step is shown, from the same endpoint
  // the post-install components page uses so the two views show the same 7 K8s components.
  const catalog = useResource<StackComponentsResponse>(
    step === 7 ? "setup:stack-components" : null,
    (signal) => api.get<StackComponentsResponse>("/api/v1/infrastructure/stack/components", { signal }),
    { onError: (e) => setError(e instanceof Error ? e.message : "Failed to load components") },
  );
  // The endpoint's "status" is the runtime state; the picker only cares whether the component is
  // selectable. Anything that is not coming_soon is selectable on the wizard.
  const pickerComponents = useMemo<PickerComponent[]>(
    () =>
      (catalog.data?.components ?? []).map((c) => ({
        key: c.key,
        name: c.name,
        description: c.description,
        category: c.category,
        dependencies: c.dependencies,
        cost_estimate: c.cost_estimate,
        status: c.status === "coming_soon" ? "coming_soon" : "available",
      })),
    [catalog.data],
  );

  // Poll per-component status on the Deploying step so the user can see
  // image builds completing, the cluster coming up, components flipping
  // enabled. The orchestrator runs on the backend; this is just a window, open
  // only while this step owns it. Each read starts 8 s after the previous one
  // settles; a failed read is said, and the next tick retries.
  const deployPoll = useResource(
    step === 8 || (step === 5 && deployment) ? `deploy-status:${step}:${selectedComponents.join(",")}` : null,
    useCallback(
      (signal: AbortSignal) =>
        api.get<{
          components: { key: string; name: string; status: string }[];
        }>("/api/components", { signal }),
      [],
    ),
    { poll: 8000 },
  );
  // The selected components' statuses from the latest answer (kept through a failed refresh).
  const deployStatus = useMemo(() => {
    const selected = new Set(selectedComponents);
    return (deployPoll.data?.components ?? [])
      .filter((c) => selected.has(c.key))
      .map((c) => ({ key: c.key, name: c.name, status: c.status }));
  }, [deployPoll.data, selectedComponents]);

  if (resume && saved.error) {
    return (
      <Card className="p-8 space-y-4">
        <p className="text-sm text-gray-700">Your saved setup couldn&apos;t be loaded.</p>
        <div className="flex items-center gap-4">
          <Button onClick={() => saved.refresh()}>Try again</Button>
          <button
            type="button"
            onClick={() => {
              endSession();
              navigateToLogin();
            }}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Sign out
          </button>
        </div>
      </Card>
    );
  }

  const operationModal = viewingOperation !== null && (
    <OperationStatusModal
      operationId={viewingOperation}
      openedFromBanner={false}
      onClose={() => setViewingOperation(null)}
      onSettled={() => saved.refresh()}
    />
  );

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
              {i < step ? "✓" : i + 1}
            </div>
            {i < STEPS.length - 1 && (
              <div className={`w-8 h-0.5 ${i < step ? "bg-green-500" : "bg-gray-200"}`} />
            )}
          </div>
        ))}
      </div>

      {reviewing ? (
        saved.data ? (
          <ResumeSummary
            deploymentState={deployment ? deploymentStateLabel(deployment.status) : null}
            onViewDeployment={() => deployment && setViewingOperation(deployment.id)}
            onReview={() => setReviewing(false)}
          />
        ) : (
          <div className="flex justify-center py-8">
            <LoadingSpinner size="lg" />
          </div>
        )
      ) : (
      <>
      <h2 className="text-xl font-semibold mb-4">{displaySteps[step]}</h2>

      {/* Back / Forward: allowed only on steps 1-6 (everything before TF deploy
          fires). Once Select Stack -> Continue triggers terraform/init and
          stack/deploy-background, the user is past the point of no return.
          Forward only renders for steps the user has already moved past, so
          they can go back to verify a value and return without re-submitting. */}
      {step >= 1 && step <= 6 && (
        <div className="mb-4 flex items-center justify-between">
          {step > firstReviewStep ? (
            <button
              type="button"
              onClick={() => setStep(step - 1)}
              aria-label="Back"
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              <span aria-hidden="true">&larr; </span>Back
            </button>
          ) : (
            <span />
          )}
          {commits.completed.has(step) && (
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

      {completionFailed && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm flex items-center justify-between gap-4">
          <span>Setup couldn&apos;t be marked complete. Your settings are saved.</span>
          <Button
            onClick={submit(async () => {
              if (step === 7) await handleSelectComponents();
              else await handleDoInfraLater();
            })}
          >
            Try again
          </Button>
        </div>
      )}

      {step === 0 && (
        <SetupCodeStep setupCode={setupCode} setSetupCode={setSetupCode} onVerify={submit(handleVerifyCode)} />
      )}

      {step === 1 && (
        <AdminAccountStep
          name={name}
          setName={setName}
          email={email}
          setEmail={setEmail}
          password={password}
          setPassword={setPassword}
          confirmPassword={confirmPassword}
          setConfirmPassword={setConfirmPassword}
          onCreate={submit(handleCreateAdmin)}
        />
      )}

      {step === 2 && (
        <OrganizationStep
          orgName={orgName}
          setOrgName={setOrgName}
          metricsChoice={metricsChoice}
          setMetricsChoice={setMetricsChoice}
          metricsCommitted={metricsCommitted}
          metricsChosenElsewhere={metricsChosenElsewhere}
          onSave={submit(handleConfigureOrg)}
        />
      )}

      {step === 3 && !isAws && <GcpCredentialsStep gcp={gcp} onSave={submit(saveCredentials)} onSkip={() => setStep(4)} />}

      {step === 3 && isAws && <AwsCredentialsStep aws={aws} onSave={submit(saveCredentials)} onSkip={() => setStep(4)} />}

      {step === 4 && (
        <SmtpStep
          fields={smtp}
          onChange={(patch) => setSmtp((prev) => ({ ...prev, ...patch }))}
          onSave={submit(handleConfigureSmtp)}
          onSkip={() => setStep(5)}
        />
      )}

      {step === 5 && deployment && deployment.status !== "succeeded" && (
        <ExistingDeploymentStep
          operation={deployment}
          deployStatus={deployStatus}
          retryUnblocked={
            !saved.data?.busy &&
            saved.data?.historyKnown === true &&
            !isActiveOperation(deployment.status) &&
            deployment.status !== "recovery_required"
          }
          onViewDetails={() => setViewingOperation(deployment.id)}
          onContinue={() => setStep(7)}
          onRetry={submit(handleRetryDeployment)}
        />
      )}

      {step === 5 && !(deployment && deployment.status !== "succeeded") && (
        <InfrastructureDecisionStep
          cloudLabel={cloudLabel}
          configured={isAws ? aws.awsConfigured : gcp.gcpConfigured}
          // Compute already deployed: never create the stack again, go to the saved components.
          onSetUp={() => setStep(deployment?.status === "succeeded" ? 7 : 6)}
          onLater={submit(handleDoInfraLater)}
        />
      )}

      {step === 6 && (
        <StackStep
          computeStack={computeStack}
          setComputeStack={setComputeStack}
          k8sStackLabel={k8sStackLabel}
          k8sComputeLabel={k8sComputeLabel}
          k8sStorageLabel={k8sStorageLabel}
          deploying={stackDeploying}
          onContinue={submit(handleSelectStack)}
        />
      )}

      {step === 7 && (
        <ComponentsStep
          loading={catalog.loading || (resume && !saved.data)}
          defaultSelected={enabledComponents.length > 0 ? enabledComponents : undefined}
          locked={enabledComponents}
          components={pickerComponents}
          onSelectionChange={handlePickerChange}
          submitting={componentsSubmitting}
          onContinue={submit(handleSelectComponents)}
        />
      )}

      {step === 8 && (
        <DeployingStep
          statusUnavailable={deployPoll.error !== null}
          deployStatus={deployStatus}
          onContinue={() => setStep(9)}
        />
      )}

      {step === 9 && <GettingStartedStep onComplete={onComplete} />}
      </>
      )}
      {operationModal}
    </div>
  );
}
