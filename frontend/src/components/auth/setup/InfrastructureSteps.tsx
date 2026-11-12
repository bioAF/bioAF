"use client";

import { Button } from "@/components/ui/Button";
import { ComponentPicker, type PickerComponent } from "@/components/components/ComponentPicker";
import { DEFAULT_SELECTED_COMPONENTS } from "./setupConstants";

/**
 * Setup's infrastructure steps. Views only: the wizard decides when a deployment starts, and
 * re-rendering or remounting one of these never starts, repeats or cancels anything.
 */

export function InfrastructureDecisionStep({
  cloudLabel,
  configured,
  onSetUp,
  onLater,
}: {
  cloudLabel: string;
  configured: boolean;
  onSetUp: () => void;
  onLater: () => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        Would you like to set up cloud infrastructure now? This deploys a Kubernetes
        cluster, storage buckets, and supporting resources on {cloudLabel}.
      </p>
      {!configured && (
        <p className="text-sm text-amber-700">
          {cloudLabel} credentials are required to set up infrastructure. You can configure them
          later in Settings.
        </p>
      )}
      <Button className="w-full"
        onClick={onSetUp}
        disabled={!configured}>
        Set up infrastructure
      </Button>
      <button onClick={onLater} className="w-full text-gray-500 text-sm hover:text-gray-700">
        Do this later
      </button>
    </div>
  );
}

export function StackStep({
  computeStack,
  setComputeStack,
  k8sStackLabel,
  k8sComputeLabel,
  k8sStorageLabel,
  deploying,
  onContinue,
}: {
  computeStack: string;
  setComputeStack: (stack: string) => void;
  k8sStackLabel: string;
  k8sComputeLabel: string;
  k8sStorageLabel: string;
  deploying: boolean;
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600 mb-4">
        Choose the compute infrastructure for running pipelines and notebooks.
      </p>
      {/* A radio group, not buttons: these are mutually exclusive choices
          of one setting. role="radio" is what makes "kubernetes is the
          selected one" available to a screen reader, which previously was
          conveyed only by the border colour. */}
      <div
        role="radiogroup"
        aria-label="Compute infrastructure"
        className="grid grid-cols-1 md:grid-cols-2 gap-4"
      >
        <div
          data-testid="compute-stack-kubernetes"
          role="radio"
          aria-checked={computeStack === "kubernetes"}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key !== "Enter" && e.key !== " ") return;
            e.preventDefault();
            setComputeStack("kubernetes");
          }}
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

        {/* Not yet selectable. aria-disabled rather than omitting the
            role, so it is still announced as an option in the group and
            its unavailability is stated rather than implied by opacity. */}
        <div
          data-testid="compute-stack-slurm"
          role="radio"
          aria-checked={false}
          aria-disabled={true}
          className="p-4 border-2 border-gray-200 rounded-lg opacity-60 cursor-not-allowed"
        >
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-gray-500">SLURM + NFS</h3>
            <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
              Coming Soon
            </span>
          </div>
          <p className="text-sm text-gray-500 mb-2">
            Traditional HPC cluster with shared filesystem.
          </p>
        </div>
      </div>

      <Button className="w-full" onClick={onContinue}
        disabled={deploying}>
        {deploying ? "Initializing infrastructure..." : `Continue with ${computeStack === "kubernetes" ? k8sStackLabel : "SLURM + NFS"}`}
      </Button>
    </div>
  );
}

export function ComponentsStep({
  loading,
  components,
  onSelectionChange,
  submitting,
  onContinue,
}: {
  loading: boolean;
  components: PickerComponent[];
  onSelectionChange: (keys: string[]) => void;
  submitting: boolean;
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        Pick the components you want enabled. Selected components will be
        queued and turned on automatically as the infrastructure becomes
        ready, so you can leave this page once you continue.
      </p>
      {loading ? (
        <div className="text-sm text-gray-500">Loading components...</div>
      ) : (
        <ComponentPicker
          components={components}
          defaultSelected={DEFAULT_SELECTED_COMPONENTS}
          onChange={onSelectionChange}
        />
      )}
      <Button className="w-full"
        onClick={onContinue}
        disabled={submitting || loading}>
        {submitting ? "Queueing components..." : "Continue"}
      </Button>
    </div>
  );
}

export interface DeployComponentStatus {
  key: string;
  name: string;
  status: string;
}

export function DeployingStep({
  statusUnavailable,
  deployStatus,
  onContinue,
}: {
  statusUnavailable: boolean;
  deployStatus: DeployComponentStatus[];
  onContinue: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="p-4 bg-blue-50 border border-blue-200 rounded">
        <p className="text-sm text-blue-800">
          Infrastructure deployment has started. This usually takes 10-15 minutes.
          Your selected components will turn on automatically as their
          prerequisites become ready; you do not need to come back here.
        </p>
      </div>
      {statusUnavailable && (
        <p data-testid="deploy-status-unavailable" role="status" className="text-sm text-amber-700">
          Component status could not be read. bioAF keeps checking.
        </p>
      )}
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
      <Button className="w-full" onClick={onContinue}>
        Continue to Getting Started
      </Button>
    </div>
  );
}

export function GettingStartedStep({ onComplete }: { onComplete: () => void }) {
  return (
    <div className="space-y-4">
      <div className="p-4 bg-green-50 border border-green-200 rounded">
        <h3 className="font-semibold text-green-800">Setup Complete</h3>
        <p className="text-sm text-green-700 mt-1">
          Your bioAF platform is configured. You can explore the Getting Started guide
          anytime from your profile page.
        </p>
      </div>
      <Button className="w-full" onClick={onComplete}>
        Go to Dashboard
      </Button>
    </div>
  );
}
