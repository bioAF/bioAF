"use client";

import { useToast } from "@/components/shared/Toast";
import { Modal } from "@/components/shared/Modal";
import { useEffect, useState } from "react";
import { StorageSection } from "@/components/components/StorageSection";
import { BootstrapCard } from "@/components/infrastructure/BootstrapCard";
import { TerraformProgressModal } from "@/components/infrastructure/TerraformProgressModal";
import { DeployProgressModal } from "@/components/infrastructure/DeployProgressModal";
import { TerraformRunHistory } from "@/components/infrastructure/TerraformRunHistory";
import { OrphanedResourcesCard } from "@/components/infrastructure/OrphanedResourcesCard";
import { DeployRecoveryModal } from "@/components/infrastructure/DeployRecoveryModal";
import { InfraUpdatesCard } from "@/components/infrastructure/InfraUpdatesCard";
import { useDeploymentProgress } from "@/hooks/useDeploymentProgress";
import { useStackOptions } from "@/hooks/useStackOptions";
import { storageDisplay } from "@/lib/storageDisplay";
import { GCP_REGIONS, zonesForRegion } from "@/lib/gcp-regions";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { ClusterStatusCard } from "./ClusterStatusCard";
import { AddOnComponentsSection } from "./AddOnComponentsSection";
import { AbandonRunDialog, AbortDeploymentDialog, DestroyStorageDialog, TeardownDialog } from "./StackActionDialogs";
import type { ComponentsData, StackStatus, TerraformRun, TerraformStatus, ClusterConfig } from "./infrastructureTypes";

export default function InfraComponentsPage() {
  // Provider-appropriate stack labels (GCP -> GKE+GCS, AWS -> EKS+S3); fails safe
  // to GCP defaults so a GCP install renders unchanged.
  const { kubernetesOption, cloudProvider } = useStackOptions();
  const isAws = cloudProvider === "aws";
  const k8sStackLabel = kubernetesOption?.label ?? "Kubernetes + GCS";
  const storageLabel = storageDisplay(kubernetesOption?.storage_backend).label;
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [tfStatus, setTfStatus] = useState<TerraformStatus | null>(null);
  const [runs, setRuns] = useState<TerraformRun[]>([]);
  const [stackStatus, setStackStatus] = useState<StackStatus | null>(null);
  const [componentsData, setComponentsData] = useState<ComponentsData | null>(null);
  const [clusterConfig, setClusterConfig] = useState<ClusterConfig | null>(null);
  const toast = useToast();
  // Each mutation needs its endpoint's permission (configure / deploy / create); reading needs view.
  const actions = useInfrastructureActions();
  const configureGate = actions.gate("configure");
  const deployGate = actions.gate("deploy");
  const createGate = actions.gate("create");
  const [showBootstrapModal, setShowBootstrapModal] = useState(false);
  const [showDeployModal, setShowDeployModal] = useState(false);
  const [deployStarted, setDeployStarted] = useState(false);
  const [showAbortConfirm, setShowAbortConfirm] = useState(false);
  const [abortLoading, setAbortLoading] = useState(false);
  const [showTeardownModal, setShowTeardownModal] = useState(false);
  const [showTeardownProgress, setShowTeardownProgress] = useState(false);
  const [showDestroyStorageModal, setShowDestroyStorageModal] = useState(false);
  const [showDestroyStorageProgress, setShowDestroyStorageProgress] = useState(false);
  const [showAbandonModal, setShowAbandonModal] = useState(false);
  const [abandonLoading, setAbandonLoading] = useState(false);
  const [showRecoveryModal, setShowRecoveryModal] = useState(false);
  const [showDeployConfirm, setShowDeployConfirm] = useState(false);
  const [defaultRegion, setDefaultRegion] = useState("us-central1");
  const [defaultZone, setDefaultZone] = useState("us-central1-a");
  const [deployRegion, setDeployRegion] = useState("us-central1");
  const [deployZone, setDeployZone] = useState("us-central1-a");
  const [useSpecificZone, setUseSpecificZone] = useState(false);
  const [recoveryCheckedOnLoad, setRecoveryCheckedOnLoad] = useState(false);


  // Always poll for deployment progress on this page. This catches
  // in-progress deploys on page load (e.g. after a refresh).
  const deployProgress = useDeploymentProgress(true);

  // Detect an in-progress deployment on page load and set deployStarted
  useEffect(() => {
    if (deployProgress.active && !deployStarted) {
      setDeployStarted(true);
    }
  }, [deployProgress.active, deployStarted]);

  // When a tracked operation finishes, refresh page data.
  // Keep the terminal status visible in the modal for 1 render cycle
  // before clearing deployStarted so the modal can show Done/Error.
  const [terminalStatus, setTerminalStatus] = useState<string | null>(null);
  useEffect(() => {
    if (!deployStarted) return;
    if (!deployProgress.active && deployProgress.status !== null) {
      // Capture the terminal status so the modal can display it
      setTerminalStatus(deployProgress.status);
    }
  }, [deployStarted, deployProgress.active, deployProgress.status]);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, isAws]);

  async function loadData() {
    // The compute region comes from the install's cloud settings: gcp_region on
    // GCP, aws_region on AWS. EKS is regional (it spans AZs automatically), so an
    // AWS install has no zone concept; only GCP reads gcp_zone.
    const cloudSettingsPath = isAws ? "/api/v1/settings/aws" : "/api/v1/settings/gcp";
    // Fetch all data in parallel so the page renders once with complete state
    const [tfResult, runsResult, ssResult, cdResult, ccResult, cloudResult] = await Promise.allSettled([
      api.get<TerraformStatus>("/api/v1/infrastructure/terraform/status"),
      api.get<{ runs: TerraformRun[] }>("/api/v1/infrastructure/terraform/runs"),
      api.get<StackStatus>("/api/v1/infrastructure/stack/status"),
      api.get<ComponentsData>("/api/v1/infrastructure/stack/components"),
      api.get<ClusterConfig>("/api/v1/infrastructure/cluster/config"),
      api.get<{ gcp_region?: string; gcp_zone?: string; aws_region?: string }>(cloudSettingsPath),
    ]);

    if (tfResult.status === "fulfilled") setTfStatus(tfResult.value);
    if (runsResult.status === "fulfilled") setRuns(runsResult.value.runs);
    if (ssResult.status === "fulfilled") setStackStatus(ssResult.value);
    if (cdResult.status === "fulfilled") setComponentsData(cdResult.value);
    if (ccResult.status === "fulfilled") setClusterConfig(ccResult.value);
    if (cloudResult.status === "fulfilled") {
      const cfg = cloudResult.value;
      const region = isAws ? cfg.aws_region : cfg.gcp_region;
      if (region) {
        setDefaultRegion(region);
        setDeployRegion(region);
      }
      if (!isAws && cfg.gcp_zone) {
        setDefaultZone(cfg.gcp_zone);
        setDeployZone(cfg.gcp_zone);
      }
    }
    setLoading(false);
  }

  // Auto-open recovery modal when orphaned clusters exist and compute is not deployed
  useEffect(() => {
    if (recoveryCheckedOnLoad) return;
    if (!stackStatus) return;
    if (stackStatus.compute_deployed) return;
    if (!stackStatus.has_orphaned_clusters) return;
    if (deployProgress.active) return;
    setRecoveryCheckedOnLoad(true);
    setShowRecoveryModal(true);
  }, [stackStatus, deployProgress.active, recoveryCheckedOnLoad]);

  const [deployLoading, setDeployLoading] = useState(false);

  function handleStartDeploy() {
    if (deployLoading || !actions.allowed("deploy")) return;

    // Check for orphaned clusters before deploying to prevent duplicates
    if (stackStatus?.has_orphaned_clusters) {
      setShowRecoveryModal(true);
      return;
    }

    setDeployRegion(defaultRegion);
    setDeployZone(defaultZone);
    setUseSpecificZone(false);
    setShowDeployConfirm(true);
  }

  async function handleConfirmDeploy() {
    setShowDeployConfirm(false);
    if (!actions.stillAllowed("deploy")) return;
    setDeployLoading(true);
    try {
      const body: Record<string, string> = { stack_type: "kubernetes" };
      // The compute region/zone override is GCP-only (the EKS deploy uses the
      // install's aws_region and spans AZs automatically), so AWS sends neither.
      if (!isAws && deployRegion !== defaultRegion) {
        body.compute_region = deployRegion;
      }
      if (!isAws && useSpecificZone && deployZone) {
        body.compute_zone = deployZone;
      }
      await api.post("/api/v1/infrastructure/stack/deploy-background", body);
      setDeployStarted(true);
      setShowDeployModal(true);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to start deployment";
      toast.error(message);
    } finally {
      setDeployLoading(false);
    }
  }

  function handleDeployComplete() {
    setShowDeployModal(false);
    setDeployStarted(false);
    setTerminalStatus(null);
    setRefreshKey((k) => k + 1);
  }

  function handleRecoveryComplete() {
    setShowRecoveryModal(false);
    setRefreshKey((k) => k + 1);
  }

  async function handleStartFresh() {
    setShowRecoveryModal(false);
    if (!actions.stillAllowed("deploy")) return;
    // Orphans were cleaned by the modal, now start fresh deploy
    setDeployLoading(true);
    try {
      await api.post("/api/v1/infrastructure/stack/deploy-background", {
        stack_type: "kubernetes",
      });
      setDeployStarted(true);
      setShowDeployModal(true);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to start deployment";
      toast.error(message);
    } finally {
      setDeployLoading(false);
    }
  }

  async function handleAbortDeploy() {
    if (!deployProgress.run_id || !actions.stillAllowed("create")) return;
    setAbortLoading(true);
    try {
      await api.post(`/api/v1/infrastructure/terraform/abandon/${deployProgress.run_id}`);
      setShowAbortConfirm(false);
      setShowDeployModal(false);
      setDeployStarted(false);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Abort failed";
      toast.error(message);
    } finally {
      setAbortLoading(false);
    }
  }

  async function handleTeardown() {
    setShowTeardownModal(false);
    if (!actions.stillAllowed("configure")) return;
    try {
      await api.post("/api/v1/infrastructure/stack/teardown-background", { confirm: true });
      setDeployStarted(true);
      setShowTeardownProgress(true);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Teardown failed to start";
      toast.error(msg);
    }
  }

  async function handleDestroyStorage() {
    setShowDestroyStorageModal(false);
    if (!actions.stillAllowed("configure")) return;
    try {
      await api.post("/api/v1/infrastructure/stack/destroy-storage-background", { confirm: true });
      setDeployStarted(true);
      setShowDestroyStorageProgress(true);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Storage destroy failed to start";
      toast.error(msg);
    }
  }

  function handleBootstrapComplete() {
    setShowBootstrapModal(false);
    setRefreshKey((k) => k + 1);
  }

  function handleTeardownComplete() {
    setShowTeardownProgress(false);
    setDeployStarted(false);
    setTerminalStatus(null);
    setRefreshKey((k) => k + 1);
  }

  function handleDestroyStorageComplete() {
    setShowDestroyStorageProgress(false);
    setDeployStarted(false);
    setTerminalStatus(null);
    setRefreshKey((k) => k + 1);
  }

  async function handleAbandonRun() {
    if (!tfStatus?.active_run_id || !actions.stillAllowed("create")) return;
    setAbandonLoading(true);
    try {
      await api.post(`/api/v1/infrastructure/terraform/abandon/${tfStatus.active_run_id}`);
      setShowAbandonModal(false);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Abandon failed";
      toast.error(message);
    } finally {
      setAbandonLoading(false);
    }
  }

  const isDeployed = stackStatus?.compute_deployed === true;
  const tfInitialized = tfStatus?.terraform_initialized === true;

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <h1 className="text-2xl font-bold mb-1">Components</h1>
        <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
          The optional services that make up this installation, and whether each one is enabled and healthy.
        </p>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="text-center">
              <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-bioaf-600 border-t-transparent" />
              <p className="mt-3 text-sm text-gray-500">Loading infrastructure status...</p>
            </div>
          </div>
        ) : (
          <>
            {/* Stuck Terraform run banner */}
            {tfStatus?.active_run_id && (
              <div className="bg-amber-50 border border-amber-300 rounded-lg p-4 mb-6">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-amber-900">
                      Terraform operation in progress
                    </h3>
                    <p className="text-xs text-amber-700 mt-1">
                      Run #{tfStatus.active_run_id} is in{" "}
                      <span className="font-medium">{tfStatus.active_run_status}</span>{" "}
                      status. New deployments and teardowns are blocked until this
                      operation completes or is abandoned.
                    </p>
                  </div>
                  <button
                    onClick={() => setShowAbandonModal(true)}
                    {...createGate}
                    className="ml-4 shrink-0 px-3 py-1.5 text-sm text-amber-800 bg-amber-200 hover:bg-amber-300 rounded font-medium"
                  >
                    Abandon
                  </button>
                </div>
              </div>
            )}

            {/* Bootstrap card (show if terraform not initialized) */}
            {tfStatus && !tfInitialized && (
              <BootstrapCard
                terraformInitialized={false}
                gcpCredentialsConfigured={tfStatus.gcp_credentials_configured}
                onBootstrapStart={() => {
                  // The progress dialog sends terraform/bootstrap as it opens.
                  if (actions.allowed("create")) setShowBootstrapModal(true);
                }}
              />
            )}

            {/* Check for infrastructure updates: re-plan deployed modules and
                apply additive changes (e.g. a newly added storage bucket). */}
            {tfInitialized && (isDeployed || stackStatus?.storage_deployed) && (
              <InfraUpdatesCard onApplyStarted={() => setRefreshKey((k) => k + 1)} />
            )}

            {/* Storage destroy section: compute is down but storage is still provisioned */}
            {tfInitialized && !isDeployed && stackStatus?.storage_deployed && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-amber-900">
                      Storage infrastructure is provisioned
                    </h3>
                    <p className="text-xs text-amber-700 mt-1">
                      {storageLabel} buckets and messaging topics are still running and accruing costs.
                    </p>
                  </div>
                  <button
                    onClick={() => {
                      setShowDestroyStorageModal(true);
                    }}
                    {...configureGate}
                    className="ml-4 shrink-0 text-sm text-red-600 hover:text-red-800 font-medium"
                  >
                    Destroy Storage
                  </button>
                </div>
              </div>
            )}

            {/* State 1: Initialized but not deployed - show stack selection cards */}
            {tfInitialized && !isDeployed && (
              <div className="space-y-4 mb-8">
                <h2 className="text-lg font-semibold">Select Compute Stack</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Kubernetes + GCS card */}
                  <div className="bg-white rounded-lg shadow-md border-2 border-blue-200 p-6">
                    <div className="flex items-start justify-between mb-3">
                      <h3 className="text-lg font-semibold text-blue-900">
                        {k8sStackLabel} (Recommended)
                      </h3>
                    </div>
                    <p className="text-sm text-gray-600 mb-4">
                      Cloud-native compute with automatic scaling. Pipeline jobs run as
                      containers on managed {kubernetesOption?.compute_label ?? "Kubernetes (GKE)"}.
                      Storage is pay-per-use with {kubernetesOption?.storage_label ?? "GCS"} object
                      storage. Best for most teams.
                    </p>
                    <p className="text-xs text-gray-500 mb-4">
                      $0 when idle. Scales automatically with your workloads.
                    </p>
                    {deployProgress.active ? (
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <span className="inline-block h-2 w-2 bg-blue-500 rounded-full animate-pulse" />
                          <span className="text-sm text-blue-700 font-medium">
                            Deployment in progress
                          </span>
                        </div>
                        {deployProgress.resources_total > 0 && (
                          <p className="text-xs text-gray-500">
                            {deployProgress.resources_completed} of {deployProgress.resources_total} components
                          </p>
                        )}
                        <div className="flex gap-2">
                          <Button size="sm"
                            onClick={() => setShowDeployModal(true)}>
                            View Progress
                          </Button>
                          <button
                            onClick={() => setShowAbortConfirm(true)}
                            {...createGate}
                            className="px-3 py-1.5 text-sm bg-red-50 text-red-600 rounded hover:bg-red-100 font-medium"
                          >
                            Abort
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        {deployProgress.failed && (
                          <p data-testid="deploy-progress-unavailable" role="status" className="text-xs text-amber-700 mb-2">
                            Deployment status could not be read. bioAF keeps checking.
                          </p>
                        )}
                        <Button
                          onClick={handleStartDeploy}
                          disabled={deployLoading || !actions.allowed("deploy")}
                          title={deployGate.title}>
                          {deployLoading ? "Starting..." : "Deploy"}
                        </Button>
                      </>
                    )}
                  </div>

                  {/* SLURM + NFS card (coming soon) */}
                  <div className="bg-white rounded-lg shadow-md border border-gray-200 p-6 opacity-60">
                    <div className="flex items-start justify-between mb-3">
                      <h3 className="text-lg font-semibold text-gray-500">SLURM + NFS</h3>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
                        Coming Soon
                      </span>
                    </div>
                    <p className="text-sm text-gray-500 mb-4">
                      Traditional HPC scheduler with NFS shared storage. Available in a
                      future release.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* State 3: Deployed - operational view */}
            {isDeployed && (
              <>
                {/* Compute Stack Banner */}
                <div className="flex items-center gap-3 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 mb-6">
                  <span className="text-sm font-medium text-blue-700">
                    Compute Stack: {k8sStackLabel}
                  </span>
                  <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">
                    Active
                  </span>
                  <button
                    onClick={() => {
                      setShowTeardownModal(true);
                    }}
                    {...configureGate}
                    className="ml-auto text-sm text-red-600 hover:text-red-800"
                  >
                    Teardown
                  </button>
                </div>

                <ClusterStatusCard
                  stackStatus={stackStatus}
                  clusterConfig={clusterConfig}
                  onApplied={() => setShowDeployModal(true)}
                />

                <AddOnComponentsSection
                  componentsData={componentsData}
                  onChanged={() => setRefreshKey((k) => k + 1)}
                />

                {/* Storage Section */}
                <div className="mt-8">
                  <h2 className="text-xl font-semibold mb-4">Storage</h2>
                  <StorageSection
                    storageDeployed={stackStatus?.storage_deployed ?? false}
                    terraformInitialized={tfInitialized}
                    pubsubConfigured={stackStatus?.pubsub_configured ?? false}
                    onDeploy={handleStartDeploy}
                    onUpdateStorage={() => setRefreshKey((k) => k + 1)}
                  />
                </div>
              </>
            )}

            {/* Orphaned Resources */}
            <div className="mt-10">
              <OrphanedResourcesCard />
            </div>

            {/* Run History */}
            <div className="mt-10">
              <h2 className="text-xl font-semibold mb-4">Recent Operations</h2>
              <TerraformRunHistory runs={runs} />
            </div>
          </>
        )}
      </main>

      {/* Bootstrap Modal */}
      {showBootstrapModal && (
        <TerraformProgressModal
          title="Initialize Infrastructure"
          sseUrl="/api/v1/infrastructure/terraform/bootstrap"
          onComplete={handleBootstrapComplete}
          onClose={() => setShowBootstrapModal(false)}
        />
      )}

      {/* Deploy Recovery Modal */}
      <DeployRecoveryModal
        open={showRecoveryModal}
        onClose={() => setShowRecoveryModal(false)}
        onRecovered={handleRecoveryComplete}
        onStartFresh={handleStartFresh}
      />

      {/* Deploy Stack Modal (poll-based) */}
      {showDeployModal && (
        <DeployProgressModal
          phase={deployProgress.phase}
          status={deployProgress.status ?? terminalStatus ?? (deployStarted ? "planning" : null)}
          resourcesCompleted={deployProgress.resources_completed}
          resourcesTotal={deployProgress.resources_total}
          completedResources={deployProgress.completed_resources}
          plannedResources={deployProgress.planned_resources}
          errorMessage={deployProgress.error_message}
          progressUnavailable={deployProgress.failed}
          onDismiss={() => setShowDeployModal(false)}
          onAbort={() => {
            setShowDeployModal(false);
            setShowAbortConfirm(true);
          }}
          onDone={handleDeployComplete}
        />
      )}

      {/* Deploy Confirmation Modal - Region/Zone Selection */}
      <Modal
        open={showDeployConfirm}
        title="Deploy Compute Infrastructure"
        onClose={() => setShowDeployConfirm(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowDeployConfirm(false)}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <Button
            onClick={handleConfirmDeploy}
            disabled={deployLoading || !actions.allowed("deploy")}
            title={deployGate.title}>
            {deployLoading ? "Starting..." : "Deploy"}
          </Button>
          </>
        }
      >

        {isAws ? (
          /* AWS (EKS): regional cluster, no per-deploy region/zone choice.
             EKS spans availability zones automatically, so there is no GKE-
             style zone selector or cross-region GCP cost warning. */
          <div className="mb-4">
            <label htmlFor="region" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
            <input id="region"
              type="text"
              value={deployRegion}
              readOnly
              className="w-full px-3 py-2 border rounded text-sm bg-gray-50 text-gray-600"
            />
            <p className="text-xs text-gray-500 mt-1">
              The cluster deploys in your install region and spans multiple availability
              zones for resilience.
            </p>
          </div>
        ) : (
          <>
            {/* Region (GCP) */}
            <div className="mb-4">
              <label htmlFor="region-2" className="block text-sm font-medium text-gray-700 mb-1">Region</label>
              <select id="region-2"
                value={deployRegion}
                onChange={(e) => {
                  setDeployRegion(e.target.value);
                  setDeployZone(zonesForRegion(e.target.value)[0]);
                }}
                className="w-full px-3 py-2 border rounded text-sm"
              >
                {GCP_REGIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}{r === defaultRegion ? " (default)" : ""}
                  </option>
                ))}
              </select>
              {deployRegion !== defaultRegion && (
                <p className="text-xs text-amber-700 mt-1 flex items-start gap-1">
                  <span className="mt-0.5">&#9888;</span>
                  Your storage is in {defaultRegion}. Deploying compute in {deployRegion} may
                  incur cross-region network costs in GCP.
                </p>
              )}
            </div>

            {/* Zone toggle (GCP) */}
            <div className="mb-4">
              <label id="lbl-page-1" className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useSpecificZone}
                  onChange={(e) => setUseSpecificZone(e.target.checked)}
                />
                <span className="text-sm text-gray-700">Deploy to a specific zone</span>
                <span
                  className="text-gray-500 cursor-help"
                  title="Without a specific zone, GKE creates a multi-zonal cluster with nodes distributed across availability zones. This provides better resilience but may cost slightly more."
                >
                  &#9432;
                </span>
              </label>
              {useSpecificZone && (
                <select aria-labelledby="lbl-page-1"
                  value={deployZone}
                  onChange={(e) => setDeployZone(e.target.value)}
                  className="w-full px-3 py-2 border rounded text-sm mt-2"
                >
                  {zonesForRegion(deployRegion).map((z) => (
                    <option key={z} value={z}>{z}</option>
                  ))}
                </select>
              )}
              {!useSpecificZone && (
                <p className="text-xs text-gray-500 mt-1">
                  Multi-zonal deployment across {deployRegion}. Greater flexibility,
                  slightly higher GCP cost.
                </p>
              )}
            </div>
          </>
        )}

      </Modal>

      <TeardownDialog
        open={showTeardownModal}
        reason={configureGate.title}
        onCancel={() => setShowTeardownModal(false)}
        onConfirm={() => void handleTeardown()}
      />

      {/* Teardown Progress Modal */}
      {showTeardownProgress && (
        <DeployProgressModal
          mode="teardown"
          phase={deployProgress.phase}
          status={deployProgress.status ?? terminalStatus ?? (deployStarted ? "applying" : null)}
          resourcesCompleted={deployProgress.resources_completed}
          resourcesTotal={deployProgress.resources_total}
          completedResources={deployProgress.completed_resources}
          plannedResources={deployProgress.planned_resources}
          errorMessage={deployProgress.error_message}
          progressUnavailable={deployProgress.failed}
          onDismiss={() => setShowTeardownProgress(false)}
          onAbort={() => {
            setShowTeardownProgress(false);
            setShowAbortConfirm(true);
          }}
          onDone={handleTeardownComplete}
        />
      )}

      <DestroyStorageDialog
        open={showDestroyStorageModal}
        storageLabel={storageLabel}
        reason={configureGate.title}
        onCancel={() => setShowDestroyStorageModal(false)}
        onConfirm={() => void handleDestroyStorage()}
      />

      {/* Destroy Storage Progress Modal */}
      {showDestroyStorageProgress && (
        <DeployProgressModal
          mode="teardown"
          phase={deployProgress.phase}
          status={deployProgress.status ?? terminalStatus ?? (deployStarted ? "applying" : null)}
          resourcesCompleted={deployProgress.resources_completed}
          resourcesTotal={deployProgress.resources_total}
          completedResources={deployProgress.completed_resources}
          plannedResources={deployProgress.planned_resources}
          errorMessage={deployProgress.error_message}
          progressUnavailable={deployProgress.failed}
          onDismiss={() => setShowDestroyStorageProgress(false)}
          onAbort={() => {
            setShowDestroyStorageProgress(false);
            setShowAbortConfirm(true);
          }}
          onDone={handleDestroyStorageComplete}
        />
      )}

      <AbortDeploymentDialog
        open={showAbortConfirm}
        busy={abortLoading}
        reason={createGate.title}
        onCancel={() => setShowAbortConfirm(false)}
        onConfirm={() => void handleAbortDeploy()}
      />

      <AbandonRunDialog
        open={showAbandonModal}
        busy={abandonLoading}
        reason={createGate.title}
        runId={tfStatus?.active_run_id}
        runStatus={tfStatus?.active_run_status}
        onCancel={() => setShowAbandonModal(false)}
        onConfirm={() => void handleAbandonRun()}
      />
    </>
  );
}
