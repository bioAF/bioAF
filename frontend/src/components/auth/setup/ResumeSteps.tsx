"use client";

import { Button } from "@/components/ui/Button";
import { usePermissionGate } from "@/hooks/useInfrastructureActions";
import type { DeployComponentStatus } from "./InfrastructureSteps";

/**
 * Views for reviewing an unfinished setup (project 12A, approved D3). Views only: the wizard
 * decides what runs, and rendering one of these never starts or repeats infrastructure work.
 */

/** The parts of an infrastructure operation the setup review shows. */
export interface SetupOperation {
  id: number;
  action: string;
  status: string;
  accepted_at: string;
  started_at?: string | null;
}

const STATE_LABELS: Record<string, string> = {
  accepted: "Queued",
  running: "Running",
  stopping: "Stopping",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
  recovery_required: "Needs recovery",
};

export function deploymentStateLabel(status: string): string {
  return STATE_LABELS[status] ?? status;
}

/** Work that still holds or is about to hold the installation's infrastructure exclusion. */
export function isActiveOperation(status: string): boolean {
  return status === "accepted" || status === "running" || status === "stopping";
}

export function ResumeSummary({
  deploymentState,
  onViewDeployment,
  onReview,
}: {
  deploymentState: string | null;
  onViewDeployment: () => void;
  onReview: () => void;
}) {
  return (
    <div className="space-y-4" data-testid="setup-resume-summary">
      <h2 className="text-xl font-semibold">Finish setting up bioAF</h2>
      <p className="text-sm text-gray-600">Review your saved settings to finish setup.</p>
      {deploymentState && (
        <p className="text-sm text-gray-700">
          Infrastructure deployment: {deploymentState}{" "}
          <button type="button" onClick={onViewDeployment} className="text-bioaf-600 hover:underline">
            View deployment
          </button>
        </p>
      )}
      <Button className="w-full" onClick={onReview}>
        Review saved setup
      </Button>
    </div>
  );
}

function startedAt(op: SetupOperation): string {
  return new Date(op.started_at || op.accepted_at).toLocaleString();
}

/**
 * Replaces the create flow when a compute deployment already exists. Retry needs the deploy
 * permission as well as ``retryUnblocked`` (nothing holds the infrastructure exclusion, history was
 * read, and no recovery acknowledgement is outstanding). Rendered only for a signed-in reviewer.
 */
export function ExistingDeploymentStep({
  operation,
  deployStatus,
  retryUnblocked,
  onViewDetails,
  onContinue,
  onRetry,
}: {
  operation: SetupOperation;
  deployStatus: DeployComponentStatus[];
  retryUnblocked: boolean;
  onViewDetails: () => void;
  onContinue: () => void;
  onRetry: () => void;
}) {
  const permission = usePermissionGate<"deploy">("infrastructure");
  const retryAvailable = retryUnblocked && permission.allowed("deploy");
  if (isActiveOperation(operation.status)) {
    return (
      <div className="space-y-4">
        <div className="p-4 bg-blue-50 border border-blue-200 rounded space-y-1">
          <p className="text-sm font-medium text-blue-900">Deployment already in progress</p>
          <p className="text-sm text-blue-800">
            Started {startedAt(operation)}. This usually takes 10-15 minutes.
          </p>
        </div>
        {deployStatus.length > 0 && (
          <div className="border border-gray-200 rounded divide-y">
            {deployStatus.map((c) => (
              <div key={c.key} className="flex items-center justify-between px-3 py-2 text-sm">
                <span className="font-medium">{c.name}</span>
                <span className="text-xs text-gray-600">{c.status}</span>
              </div>
            ))}
          </div>
        )}
        {operation.status === "stopping" && (
          <button type="button" onClick={onViewDetails} className="text-sm text-bioaf-600 hover:underline">
            View details
          </button>
        )}
        <Button className="w-full" onClick={onContinue}>
          Continue to components
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="p-4 bg-amber-50 border border-amber-200 rounded space-y-1">
        <p className="text-sm font-medium text-amber-900">The last deployment didn&apos;t finish</p>
        <p className="text-sm text-amber-800">Your settings are saved. Review what happened before trying again.</p>
      </div>
      <button type="button" onClick={onViewDetails} className="text-sm text-bioaf-600 hover:underline">
        View details
      </button>
      <Button className="w-full" onClick={onRetry} disabled={!retryAvailable}>
        Retry deployment
      </Button>
    </div>
  );
}
