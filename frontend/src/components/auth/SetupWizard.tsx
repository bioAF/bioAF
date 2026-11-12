"use client";

import { useConfirm } from "@/hooks/useConfirm";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { logError } from "@/lib/errorReporting";
import { startSession } from "@/lib/session";
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
export function SetupWizard({ onComplete }: SetupWizardProps) {
  const confirm = useConfirm();
  const [step, setStep] = useState(0);
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

  // Step 2: Org name
  const [orgName, setOrgName] = useState("");

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

  // Step 6: Compute stack
  const [computeStack, setComputeStack] = useState("kubernetes");
  const [stackDeploying, setStackDeploying] = useState(false);

  // Step 7: Select Components
  const [selectedComponents, setSelectedComponents] = useState<string[]>(DEFAULT_SELECTED_COMPONENTS);
  const [componentsSubmitting, setComponentsSubmitting] = useState(false);

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

  const handleConfigureOrg = async () => {
    setError("");
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
      });
      markStepCompleted(4, current);
      setStep(5);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to configure SMTP");
    }
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
    step === 8 ? `deploy-status:${selectedComponents.join(",")}` : null,
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

      {step === 2 && <OrganizationStep orgName={orgName} setOrgName={setOrgName} onSave={submit(handleConfigureOrg)} />}

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

      {step === 5 && (
        <InfrastructureDecisionStep
          cloudLabel={cloudLabel}
          configured={isAws ? aws.awsConfigured : gcp.gcpConfigured}
          onSetUp={() => setStep(6)}
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
          loading={catalog.loading}
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
    </div>
  );
}
