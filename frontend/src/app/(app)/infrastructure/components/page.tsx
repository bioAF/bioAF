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
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { useDeploymentProgress } from "@/hooks/useDeploymentProgress";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useStackOptions } from "@/hooks/useStackOptions";
import { storageDisplay } from "@/lib/storageDisplay";
import { GCP_REGIONS, zonesForRegion } from "@/lib/gcp-regions";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { invalidateComponentCache } from "@/hooks/useComponents";
import { useConfirm } from "@/hooks/useConfirm";
import { notebookSupportForMachine } from "@/lib/notebookCapacity";
import {
  INTERACTIVE_MACHINE_OPTIONS,
  PIPELINE_MACHINE_OPTIONS,
  formatMachineOptionLabel,
} from "@/lib/clusterMachineOptions";

interface TerraformStatus {
  terraform_initialized: boolean;
  terraform_state_bucket: string;
  gcp_credentials_configured: boolean;
  active_run_id: number | null;
  active_run_status: string | null;
}

interface TerraformRun {
  id: number;
  action: string;
  module_name: string | null;
  status: string;
  resources_planned: number | null;
  resources_completed: number;
  triggered_by_user_id: number;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
  plan_json: object | null;
  terraform_state_url: string | null;
}

interface NodePoolInfo {
  name: string;
  machine_type: string;
  min_nodes: number;
  max_nodes: number;
  current_nodes: number;
  spot: boolean;
  status: string;
}

interface ClusterInfo {
  cluster_name: string;
  status: string;
  node_count: number;
  pipeline_pool: NodePoolInfo;
  interactive_pool: NodePoolInfo;
}

interface StackStatus {
  compute_stack: string | null;
  compute_deployed: boolean;
  storage_deployed: boolean;
  pubsub_configured: boolean;
  cluster: ClusterInfo | null;
  has_orphaned_clusters: boolean;
}

interface ComponentDef {
  key: string;
  name: string;
  category: string;
  description: string;
  cost_estimate: string;
  dependencies: string[];
  status: string;
  configurable: boolean;
}

interface ComponentsData {
  compute_stack: string | null;
  compute_deployed: boolean;
  storage_deployed: boolean;
  components: ComponentDef[];
}

interface ClusterConfig {
  k8s_pipeline_machine_type: string;
  k8s_pipeline_max_nodes: number;
  k8s_pipeline_use_spot: boolean;
  k8s_interactive_machine_type: string;
  k8s_interactive_max_nodes: number;
}

const CATEGORY_LABELS: Record<string, string> = {
  pipeline_orchestration: "Pipeline Orchestration",
  analysis: "Analysis",
  visualization: "Visualization",
  search: "Search",
};

const CATEGORY_ORDER = [
  "pipeline_orchestration",
  "analysis",
  "visualization",
  "search",
];

export default function InfraComponentsPage() {
  const confirm = useConfirm();
  const { has } = useCapabilities();
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
  const [showBootstrapModal, setShowBootstrapModal] = useState(false);
  const [showDeployModal, setShowDeployModal] = useState(false);
  const [deployStarted, setDeployStarted] = useState(false);
  const [showAbortConfirm, setShowAbortConfirm] = useState(false);
  const [abortLoading, setAbortLoading] = useState(false);
  const [showTeardownModal, setShowTeardownModal] = useState(false);
  const [showTeardownProgress, setShowTeardownProgress] = useState(false);
  const [teardownChecked, setTeardownChecked] = useState(false);
  const [showDestroyStorageModal, setShowDestroyStorageModal] = useState(false);
  const [showDestroyStorageProgress, setShowDestroyStorageProgress] = useState(false);
  const [destroyStorageChecked, setDestroyStorageChecked] = useState(false);
  const [destroyStoragePhrase, setDestroyStoragePhrase] = useState("");
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [showSpotTooltip, setShowSpotTooltip] = useState(false);
  const [configEdits, setConfigEdits] = useState<Partial<ClusterConfig>>({});
  const [configSaving, setConfigSaving] = useState(false);
  const [showRedeployConfirm, setShowRedeployConfirm] = useState(false);
  const [configError, setConfigError] = useState("");
  const [componentErrors, setComponentErrors] = useState<Record<string, string>>({});
  const [togglingComponent, setTogglingComponent] = useState<string | null>(null);
  const [buildStatusMap, setBuildStatusMap] = useState<
    Record<string, { build_id: string | null; build_status: string | null; image_uri: string | null }>
  >({});
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

  const DESTROY_STORAGE_PHRASE = "delete my data";

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

  // Fetch build status when any component is provisioning or build_failed
  const buildRelatedComponents = componentsData?.components.filter(
    (c) => c.status === "provisioning" || c.status === "build_failed",
  ) ?? [];
  const hasBuildRelated = buildRelatedComponents.length > 0;

  // Map component keys to their build status endpoints
  const notebookKeys = new Set(["rstudio", "jupyterhub"]);
  const cellxgeneKeys = new Set(["cellxgene"]);

  useEffect(() => {
    if (!hasBuildRelated) {
      setBuildStatusMap({});
      return;
    }
    let cancelled = false;

    async function pollBuild() {
      const endpoints: { type: string; url: string }[] = [];
      if (buildRelatedComponents.some((c) => notebookKeys.has(c.key))) {
        endpoints.push({ type: "notebook", url: "/api/v1/infrastructure/notebook-image/build-status" });
      }
      if (buildRelatedComponents.some((c) => cellxgeneKeys.has(c.key))) {
        endpoints.push({ type: "cellxgene", url: "/api/v1/infrastructure/cellxgene-image/build-status" });
      }

      const results: Record<string, { build_id: string | null; build_status: string | null; image_uri: string | null }> = {};
      let anyFinished = false;
      for (const ep of endpoints) {
        try {
          const status = await api.get<{
            build_id: string | null;
            build_status: string | null;
            image_uri: string | null;
          }>(ep.url);
          results[ep.type] = status;
          if (status.build_status && !["WORKING", "QUEUED"].includes(status.build_status)) {
            anyFinished = true;
          }
        } catch {
          // ignore
        }
      }
      if (!cancelled) {
        setBuildStatusMap(results);
        if (anyFinished) {
          setRefreshKey((k) => k + 1);
        }
      }
    }

    pollBuild();
    const isActive = componentsData?.components.some((c) => c.status === "provisioning");
    if (isActive) {
      const interval = setInterval(pollBuild, 15000);
      return () => {
        cancelled = true;
        clearInterval(interval);
      };
    }
    return () => {
      cancelled = true;
    };
  }, [hasBuildRelated, componentsData]);

  const [deployLoading, setDeployLoading] = useState(false);

  function handleStartDeploy() {
    if (deployLoading) return;

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
    if (!deployProgress.run_id) return;
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
    if (!tfStatus?.active_run_id) return;
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

  // Components that have a built image
  const imageComponents = new Set(["rstudio", "jupyterhub", "cellxgene"]);

  async function handleComponentToggle(componentKey: string, rebuildChoice?: boolean) {
    setComponentErrors((prev) => ({ ...prev, [componentKey]: "" }));

    // When re-enabling an image component, check if it already has a
    // successful build and ask the user whether to rebuild.
    const comp = componentsData?.components.find((c) => c.key === componentKey);
    const isEnabling = comp && comp.status === "disabled";
    let forceRebuild = rebuildChoice ?? false;

    if (rebuildChoice === undefined && isEnabling && imageComponents.has(componentKey)) {
      // Check if there's already a successful image for this component
      const buildType = cellxgeneKeys.has(componentKey) ? "cellxgene" : "notebook";
      const statusUrl = buildType === "cellxgene"
        ? "/api/v1/infrastructure/cellxgene-image/build-status"
        : "/api/v1/infrastructure/notebook-image/build-status";
      try {
        const status = await api.get<{
          build_id: string | null;
          build_status: string | null;
          image_uri: string | null;
        }>(statusUrl);
        if (status.image_uri) {
          // Hand off to the dialog and stop here. It re-enters this function with
          // an explicit choice, so dismissing it genuinely does nothing.
          setPendingRebuild(componentKey);
          return;
        }
      } catch {
        // No existing build -- proceed normally
      }
    }

    // Disabling had no gate while ENABLING opens a three-way dialog above. The
    // asymmetry ran the wrong way: re-enabling is recoverable, tearing down a
    // deployed add-on takes any sessions running on it with it.
    if (comp && comp.status === "enabled") {
      const ok = await confirm({
        title: `Disable ${comp.name}?`,
        message: (
          <>
            <p>
              This tears down the deployed {comp.name} component.{" "}
              <span className="font-medium">Any sessions currently running on it stop</span>, and
              unsaved work in them is lost.
            </p>
            <p>
              Files in storage are not affected, and you can enable it again later, though that
              means provisioning it from scratch.
            </p>
          </>
        ),
        confirmLabel: "Disable",
        variant: "danger",
      });
      if (!ok) return;
    }

    setTogglingComponent(componentKey);
    try {
      const url = forceRebuild
        ? `/api/v1/infrastructure/stack/components/${componentKey}/toggle?force_rebuild=true`
        : `/api/v1/infrastructure/stack/components/${componentKey}/toggle`;
      await api.post(url);
      invalidateComponentCache();
      setRefreshKey((k) => k + 1);
    } catch (e) {
      logError(`toggling the ${componentKey} component`, e);
      // A 4xx here is a validation message the backend wrote FOR the admin, e.g.
      // "Dependency not met: kubernetes_cluster must be enabled first". That is
      // already the plain sentence, and it names the fix. Replacing it with the
      // generic house sentence threw away the only useful part, so the rule is
      // scoped: show the server's reason when it gave one, otherwise say nothing
      // technical.
      const status = (e as { status?: number } | null)?.status;
      const reason = status && status >= 400 && status < 500 && e instanceof Error ? e.message : null;
      setComponentErrors((prev) => ({
        ...prev,
        [componentKey]:
          reason ?? "That change could not be applied. The technical detail is in the application logs.",
      }));
    } finally {
      setTogglingComponent(null);
    }
  }

  const [pendingRebuild, setPendingRebuild] = useState<string | null>(null);

  const [cancellingBuild, setCancellingBuild] = useState(false);

  async function handleCancelBuild() {
    setCancellingBuild(true);
    try {
      await api.post("/api/v1/infrastructure/notebook-image/cancel");
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Cancel failed";
      toast.error(message);
    } finally {
      setCancellingBuild(false);
    }
  }

  async function handleRetryBuild(componentKey: string) {
    setTogglingComponent(componentKey);
    try {
      // Disable then re-enable to trigger a fresh build
      await api.post(`/api/v1/infrastructure/stack/components/${componentKey}/toggle`);
      await api.post(`/api/v1/infrastructure/stack/components/${componentKey}/toggle`);
      invalidateComponentCache();
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Retry failed";
      setComponentErrors((prev) => ({ ...prev, [componentKey]: message }));
    } finally {
      setTogglingComponent(null);
    }
  }

  /**
   * True when the pending edits replace a machine type, which is the only
   * change that recreates a node pool. Scaling max_nodes or flipping spot does
   * not destroy running work, so the confirmation must not claim it does.
   */
  function redeployRecreatesAPool(): boolean {
    return (
      configEdits.k8s_pipeline_machine_type !== undefined ||
      configEdits.k8s_interactive_machine_type !== undefined
    );
  }

  async function handleApplyClusterConfig() {
    setShowRedeployConfirm(false);
    setConfigError("");
    setConfigSaving(true);
    try {
      await api.post("/api/v1/infrastructure/cluster/config", configEdits);
      setConfigEdits({});
      setShowConfigPanel(false);
      setShowDeployModal(true);
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : "Failed to apply changes");
    } finally {
      setConfigSaving(false);
    }
  }

  const isDeployed = stackStatus?.compute_deployed === true;
  const tfInitialized = tfStatus?.terraform_initialized === true;

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <h1 className="text-2xl font-bold mb-6">Components</h1>

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
                onBootstrapStart={() => setShowBootstrapModal(true)}
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
                      setDestroyStorageChecked(false);
                      setDestroyStoragePhrase("");
                      setShowDestroyStorageModal(true);
                    }}
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
                          <button
                            onClick={() => setShowDeployModal(true)}
                            className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 font-medium"
                          >
                            View Progress
                          </button>
                          <button
                            onClick={() => setShowAbortConfirm(true)}
                            className="px-3 py-1.5 text-sm bg-red-50 text-red-600 rounded hover:bg-red-100 font-medium"
                          >
                            Abort
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={handleStartDeploy}
                        disabled={deployLoading}
                        className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {deployLoading ? "Starting..." : "Deploy"}
                      </button>
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
                      setTeardownChecked(false);
                      setShowTeardownModal(true);
                    }}
                    className="ml-auto text-sm text-red-600 hover:text-red-800"
                  >
                    Teardown
                  </button>
                </div>

                {/* Cluster Status Card */}
                {stackStatus?.cluster && (
                  <div className="bg-white rounded-lg shadow p-6 border border-gray-200 mb-6">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <h3 className="font-semibold">Cluster Status</h3>
                        <span className="text-sm text-gray-600">
                          {stackStatus.cluster.cluster_name}
                        </span>
                        <span className="w-2 h-2 bg-green-500 rounded-full" />
                      </div>
                      <button
                        onClick={() => setShowConfigPanel(!showConfigPanel)}
                        className="text-sm text-bioaf-600 hover:text-bioaf-700"
                      >
                        Configure
                      </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {/* Pipeline Pool */}
                      <div className="bg-gray-50 rounded p-4">
                        <h4 className="text-sm font-medium mb-2">Pipeline Pool</h4>
                        <div className="text-xs text-gray-600 space-y-1">
                          <p>Name: {stackStatus.cluster.pipeline_pool.name}</p>
                          <p>Machine Type: {stackStatus.cluster.pipeline_pool.machine_type}</p>
                          <p>Max Nodes: {stackStatus.cluster.pipeline_pool.max_nodes}</p>
                          <p>Current: {stackStatus.cluster.pipeline_pool.current_nodes}</p>
                          <p>Spot: {stackStatus.cluster.pipeline_pool.spot ? "Yes" : "No"}</p>
                        </div>
                      </div>

                      {/* Interactive Pool */}
                      <div className="bg-gray-50 rounded p-4">
                        <h4 className="text-sm font-medium mb-2">Interactive Pool</h4>
                        <div className="text-xs text-gray-600 space-y-1">
                          <p>Name: {stackStatus.cluster.interactive_pool.name}</p>
                          {(() => {
                            const mt = stackStatus.cluster.interactive_pool.machine_type;
                            const support = notebookSupportForMachine(mt);
                            return (
                              <>
                                <p>
                                  Machine Type: {support.topLabel ? `${support.topLabel} (${mt})` : mt}
                                </p>
                                {support.supportSentence && (
                                  <p className="text-gray-500">{support.supportSentence}</p>
                                )}
                              </>
                            );
                          })()}
                          <p>Max Nodes: {stackStatus.cluster.interactive_pool.max_nodes}</p>
                          <p>Current: {stackStatus.cluster.interactive_pool.current_nodes}</p>
                        </div>
                      </div>
                    </div>

                    {/* Configuration Panel */}
                    {showConfigPanel && clusterConfig && (
                      <div className="mt-4 pt-4 border-t border-gray-200">
                        <h4 className="text-sm font-medium mb-3">Cluster Configuration</h4>

                        {configError && (
                          <div className="mb-3 p-2 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
                            {configError}
                          </div>
                        )}

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                          {/* Pipeline Nodes column */}
                          <div className="space-y-3">
                            <h5 className="text-sm font-medium text-gray-700">Pipeline Nodes</h5>
                            <div>
                              <label htmlFor="machine-size" className="text-xs text-gray-500">Machine Size</label>
                              <select id="machine-size"
                                value={configEdits.k8s_pipeline_machine_type ?? clusterConfig.k8s_pipeline_machine_type}
                                onChange={(e) => setConfigEdits({ ...configEdits, k8s_pipeline_machine_type: e.target.value })}
                                className="w-full border rounded px-2 py-1 text-sm mt-1 bg-white"
                                disabled={configSaving}
                              >
                                {PIPELINE_MACHINE_OPTIONS.map((opt) => (
                                  <option key={opt.value} value={opt.value}>
                                    {formatMachineOptionLabel(opt)}
                                  </option>
                                ))}
                              </select>
                            </div>
                            {has("autoscaling") && (
                            <div>
                              <label htmlFor="max-nodes" className="text-xs text-gray-500">Max Nodes</label>
                              <input id="max-nodes"
                                type="number"
                                value={configEdits.k8s_pipeline_max_nodes ?? clusterConfig.k8s_pipeline_max_nodes}
                                onChange={(e) => setConfigEdits({ ...configEdits, k8s_pipeline_max_nodes: Number(e.target.value) })}
                                className="w-full border rounded px-2 py-1 text-sm mt-1"
                                disabled={configSaving}
                              />
                            </div>
                            )}
                            {has("spot_retry") && (
                            <div className="flex items-center gap-2">
                              <label className="text-xs text-gray-500">Spot Instances</label>
                              <div className="relative">
                                <button
                                  type="button"
                                  onClick={() => setShowSpotTooltip(!showSpotTooltip)}
                                  className="inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-400 text-gray-500 text-[10px] leading-none hover:border-gray-600 hover:text-gray-600"
                                >
                                  i
                                </button>
                                {showSpotTooltip && (
                                  <div className="absolute left-6 top-1/2 -translate-y-1/2 z-10 w-72 p-3 bg-gray-800 text-white text-xs rounded-lg shadow-lg">
                                    Spot instances cost 60-70% less than standard VMs but can be reclaimed by Google Cloud with 30 seconds notice. bioAF automatically retries interrupted pipeline tasks without re-requesting additional resources. Recommended for most workloads. Disable only if your pipelines cannot tolerate any interruption.
                                    <button
                                      type="button"
                                      onClick={(e) => { e.stopPropagation(); setShowSpotTooltip(false); }}
                                      className="absolute top-1 right-2 text-gray-500 hover:text-white"
                                    >
                                      x
                                    </button>
                                  </div>
                                )}
                              </div>
                              <button
                                type="button"
                                disabled={configSaving}
                                onClick={() => {
                                  const current = configEdits.k8s_pipeline_use_spot ?? clusterConfig.k8s_pipeline_use_spot;
                                  setConfigEdits({ ...configEdits, k8s_pipeline_use_spot: !current });
                                }}
                                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                                  (configEdits.k8s_pipeline_use_spot ?? clusterConfig.k8s_pipeline_use_spot)
                                    ? "bg-bioaf-600"
                                    : "bg-gray-300"
                                }`}
                              >
                                <span
                                  className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                                    (configEdits.k8s_pipeline_use_spot ?? clusterConfig.k8s_pipeline_use_spot)
                                      ? "translate-x-4.5"
                                      : "translate-x-0.5"
                                  }`}
                                />
                              </button>
                              <span className="text-xs text-gray-600">
                                {(configEdits.k8s_pipeline_use_spot ?? clusterConfig.k8s_pipeline_use_spot) ? "On" : "Off"}
                              </span>
                            </div>
                            )}
                          </div>

                          {/* Interactive Nodes column */}
                          <div className="space-y-3">
                            <h5 className="text-sm font-medium text-gray-700">Interactive Nodes</h5>
                            <div>
                              <label htmlFor="machine-size-2" className="text-xs text-gray-500">Machine Size</label>
                              <select id="machine-size-2"
                                value={configEdits.k8s_interactive_machine_type ?? clusterConfig.k8s_interactive_machine_type}
                                onChange={(e) => setConfigEdits({ ...configEdits, k8s_interactive_machine_type: e.target.value })}
                                className="w-full border rounded px-2 py-1 text-sm mt-1 bg-white"
                                disabled={configSaving}
                              >
                                {INTERACTIVE_MACHINE_OPTIONS.map((opt) => (
                                  <option key={opt.value} value={opt.value}>
                                    {formatMachineOptionLabel(opt)}
                                  </option>
                                ))}
                              </select>
                              {(() => {
                                const selected =
                                  configEdits.k8s_interactive_machine_type ??
                                  clusterConfig.k8s_interactive_machine_type;
                                const sentence = notebookSupportForMachine(selected).supportSentence;
                                return sentence ? (
                                  <p className="text-xs text-gray-500 mt-1">{sentence}</p>
                                ) : null;
                              })()}
                            </div>
                            {has("autoscaling") && (
                            <div>
                              <label htmlFor="max-nodes-2" className="text-xs text-gray-500">Max Nodes</label>
                              <input id="max-nodes-2"
                                type="number"
                                value={configEdits.k8s_interactive_max_nodes ?? clusterConfig.k8s_interactive_max_nodes}
                                onChange={(e) => setConfigEdits({ ...configEdits, k8s_interactive_max_nodes: Number(e.target.value) })}
                                className="w-full border rounded px-2 py-1 text-sm mt-1"
                                disabled={configSaving}
                              />
                            </div>
                            )}
                          </div>
                        </div>
                        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mt-4">
                          Applying these changes redeploys the cluster immediately. Replacing a
                          machine type recreates its node pool, which stops pipelines and notebooks
                          running on it.
                        </p>
                        <div className="flex gap-2 mt-4">
                          <button
                            disabled={configSaving || Object.keys(configEdits).length === 0}
                            onClick={() => setShowRedeployConfirm(true)}
                            className="px-3 py-1 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
                          >
                            {configSaving ? "Applying..." : "Apply and redeploy"}
                          </button>
                          <button
                            disabled={configSaving}
                            onClick={() => {
                              setShowConfigPanel(false);
                              setConfigEdits({});
                              setConfigError("");
                            }}
                            className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Add-on Components */}
                {componentsData && componentsData.components.length > 0 && (
                  <div className="space-y-6 mb-8">
                    {CATEGORY_ORDER.filter((cat) =>
                      componentsData.components.some((c) => c.category === cat)
                    ).map((category) => (
                      <div key={category}>
                        <h2 className="text-lg font-semibold mb-3">
                          {CATEGORY_LABELS[category] ?? category}
                        </h2>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                          {componentsData.components
                            .filter((c) => c.category === category)
                            .map((comp) => comp.status === "coming_soon" ? (
                              <div
                                key={comp.key}
                                className="bg-white rounded-lg shadow p-5 border border-gray-200 opacity-60"
                              >
                                <div className="flex items-start justify-between mb-2">
                                  <h3 className="font-semibold text-sm text-gray-500">{comp.name}</h3>
                                  <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
                                    Coming Soon
                                  </span>
                                </div>
                                <p className="text-xs text-gray-500">{comp.description}</p>
                              </div>
                            ) : (
                              <div
                                key={comp.key}
                                className="bg-white rounded-lg shadow p-5 border border-gray-200"
                              >
                                {(() => {
                                  // Resolve the right build status for this component
                                  const buildType = cellxgeneKeys.has(comp.key) ? "cellxgene" : "notebook";
                                  const buildStatus = buildStatusMap[buildType] ?? null;
                                  return (<>
                                <div className="flex items-start justify-between mb-2">
                                  <h3 className="font-semibold text-sm">{comp.name}</h3>
                                  {(() => {
                                    const buildFailed = comp.status === "build_failed" ||
                                      (comp.status === "provisioning" && buildStatus && ["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? ""));
                                    return (
                                      <span
                                        className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                          comp.status === "enabled"
                                            ? "bg-green-100 text-green-700"
                                            : buildFailed
                                              ? "bg-red-100 text-red-700"
                                              : comp.status === "provisioning"
                                                ? "bg-amber-100 text-amber-700"
                                                : comp.status === "queued_for_infra"
                                                  ? "bg-blue-100 text-blue-700"
                                                  : "bg-gray-100 text-gray-600"
                                        }`}
                                      >
                                        {comp.status === "enabled"
                                          ? "Enabled"
                                          : buildFailed
                                            ? "Build Failed"
                                            : comp.status === "provisioning"
                                              ? "Building Image..."
                                              : comp.status === "queued_for_infra"
                                                ? "Queued"
                                                : "Disabled"}
                                      </span>
                                    );
                                  })()}
                                </div>
                                <p className="text-xs text-gray-600 mb-3">{comp.description}</p>
                                {comp.dependencies.length > 0 && (
                                  <p className="text-xs text-gray-500 mb-2">
                                    Requires: {comp.dependencies.join(", ")}
                                  </p>
                                )}
                                {/* Show building panel only when actually building (not when build already failed) */}
                                {comp.status === "provisioning" && buildStatus && !["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? "") && (
                                  <div className="bg-amber-50 border border-amber-200 rounded p-2 mb-2">
                                    <p className="text-xs font-medium text-amber-800">
                                      Building image...
                                    </p>
                                    <p className="text-xs text-amber-700 mt-0.5">
                                      Status: {buildStatus.build_status ?? "Starting"}
                                      {buildStatus.build_id && (
                                        <span className="text-amber-400 ml-1">
                                          ({buildStatus.build_id.slice(0, 8)})
                                        </span>
                                      )}
                                    </p>
                                    <p className="text-xs text-amber-500 mt-0.5">
                                      This one-time setup can take up to an hour. You can leave this page.
                                    </p>
                                    <button
                                      onClick={handleCancelBuild}
                                      disabled={cancellingBuild}
                                      className="mt-1 text-xs text-red-600 hover:text-red-800 font-medium"
                                    >
                                      {cancellingBuild ? "Cancelling..." : "Cancel Build"}
                                    </button>
                                  </div>
                                )}
                                {/* Show failure panel for build_failed OR provisioning-but-actually-failed */}
                                {(comp.status === "build_failed" || (comp.status === "provisioning" && buildStatus && ["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? ""))) && (
                                  <div className="bg-red-50 border border-red-200 rounded p-2 mb-2">
                                    <p className="text-xs font-medium text-red-800">
                                      Image build failed
                                    </p>
                                    {buildStatus?.build_id && (
                                      <p className="text-xs text-red-600 mt-0.5">
                                        Build: {buildStatus.build_id.slice(0, 8)} -- {buildStatus.build_status}
                                      </p>
                                    )}
                                    <button
                                      onClick={() => handleRetryBuild(comp.key)}
                                      disabled={togglingComponent === comp.key}
                                      className="mt-1 text-xs text-bioaf-600 hover:text-bioaf-700 font-medium"
                                    >
                                      {togglingComponent === comp.key ? "Retrying..." : "Retry Build"}
                                    </button>
                                  </div>
                                )}
                                <div className="flex items-center justify-end mt-3">
                                  <button
                                    onClick={() => handleComponentToggle(comp.key)}
                                    disabled={comp.status === "provisioning" || comp.status === "build_failed" || togglingComponent === comp.key}
                                    className={`px-3 py-1 text-xs rounded font-medium ${
                                      togglingComponent === comp.key
                                        ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                                        : comp.status === "enabled"
                                          ? "bg-red-50 text-red-700 hover:bg-red-100"
                                          : comp.status === "provisioning"
                                            ? "bg-amber-100 text-amber-700 cursor-not-allowed"
                                            : comp.status === "build_failed"
                                              ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                                              : "bg-bioaf-600 text-white hover:bg-bioaf-700"
                                    }`}
                                  >
                                    {togglingComponent === comp.key
                                      ? "Updating..."
                                      : comp.status === "enabled"
                                        ? "Disable"
                                        : comp.status === "provisioning"
                                          ? "Building..."
                                          : comp.status === "build_failed"
                                            ? "Failed"
                                            : "Enable"}
                                  </button>
                                </div>
                                {componentErrors[comp.key] && (
                                  <p className="text-xs text-red-600 mt-2">
                                    {componentErrors[comp.key]}
                                  </p>
                                )}
                                </>);
                                })()}
                              </div>
                            ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

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
          <button
            onClick={handleConfirmDeploy}
            disabled={deployLoading}
            className="px-4 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {deployLoading ? "Starting..." : "Deploy"}
          </button>
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

      {/* Teardown Confirmation Modal */}
      <Modal
        open={showTeardownModal}
        title="Teardown Compute Stack"
        onClose={() => setShowTeardownModal(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowTeardownModal(false)}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            disabled={!teardownChecked}
            onClick={async () => {
              setShowTeardownModal(false);
              try {
                await api.post("/api/v1/infrastructure/stack/teardown-background", { confirm: true });
                setDeployStarted(true);
                setShowTeardownProgress(true);
              } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : "Teardown failed to start";
                toast.error(msg);
              }
            }}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Teardown
          </button>
          </>
        }
      >
        <p className="text-sm text-gray-600 mb-4">
          This will destroy the Kubernetes cluster and all node pools. Active pipeline
          runs and notebook sessions will be terminated. Storage buckets and your data
          will NOT be affected.
        </p>
        <label className="flex items-start gap-2 mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={teardownChecked}
            onChange={(e) => setTeardownChecked(e.target.checked)}
            className="mt-0.5"
          />
          <span className="text-sm text-gray-700">
            I understand this will terminate all running workloads
          </span>
        </label>
      </Modal>

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
          onDismiss={() => setShowTeardownProgress(false)}
          onAbort={() => {
            setShowTeardownProgress(false);
            setShowAbortConfirm(true);
          }}
          onDone={handleTeardownComplete}
        />
      )}

      {/* Destroy Storage Confirmation Modal */}
      <Modal
        open={showDestroyStorageModal}
        title="Destroy Storage Infrastructure"
        onClose={() => setShowDestroyStorageModal(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowDestroyStorageModal(false)}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            disabled={!destroyStorageChecked || destroyStoragePhrase !== DESTROY_STORAGE_PHRASE}
            onClick={async () => {
              setShowDestroyStorageModal(false);
              try {
                await api.post("/api/v1/infrastructure/stack/destroy-storage-background", { confirm: true });
                setDeployStarted(true);
                setShowDestroyStorageProgress(true);
              } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : "Storage destroy failed to start";
                toast.error(msg);
              }
            }}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Destroy Storage
          </button>
          </>
        }
      >

        <div className="bg-red-50 border border-red-200 rounded p-3 mb-4">
          <p className="text-sm font-medium text-red-800 mb-1">
            All data will be permanently deleted
          </p>
          <p className="text-xs text-red-700">
            This will permanently destroy all {storageLabel} buckets and their
            contents, including raw sample data, processed pipeline outputs, and
            results. This action cannot be undone.
          </p>
        </div>

        <label className="flex items-start gap-2 mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={destroyStorageChecked}
            onChange={(e) => setDestroyStorageChecked(e.target.checked)}
            className="mt-0.5"
          />
          <span className="text-sm text-gray-700">
            I understand all files stored in {storageLabel} will be permanently lost
          </span>
        </label>

        <div className="mb-4">
          <label htmlFor="type-delete-my-data-to-confirm" className="text-xs text-gray-500 block mb-1">
            Type <span className="font-mono font-medium text-gray-700">delete my data</span> to confirm
          </label>
          <input id="type-delete-my-data-to-confirm"
            type="text"
            value={destroyStoragePhrase}
            onChange={(e) => setDestroyStoragePhrase(e.target.value)}
            placeholder="delete my data"
            className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
          />
        </div>

      </Modal>

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
          onDismiss={() => setShowDestroyStorageProgress(false)}
          onAbort={() => {
            setShowDestroyStorageProgress(false);
            setShowAbortConfirm(true);
          }}
          onDone={handleDestroyStorageComplete}
        />
      )}

      {/* Abort Deployment Confirmation Modal */}
      <Modal
        open={showAbortConfirm}
        title="Abort Deployment"
        onClose={() => setShowAbortConfirm(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowAbortConfirm(false)}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Continue Deploying
          </button>
          <button
            onClick={handleAbortDeploy}
            disabled={abortLoading}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
          >
            {abortLoading ? "Aborting..." : "Abort Deployment"}
          </button>
          </>
        }
      >

        <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
          <p className="text-sm font-medium text-amber-800 mb-1">
            This may leave infrastructure in an unexpected state
          </p>
          <p className="text-xs text-amber-700">
            Aborting will cancel the current deployment. Some resources may
            have already been created and will need to be cleaned up manually
            or by re-running the deployment.
          </p>
        </div>

      </Modal>

      {/* Abandon Run Confirmation Modal */}
      <Modal
        open={showAbandonModal}
        title="Abandon Terraform Operation"
        onClose={() => setShowAbandonModal(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowAbandonModal(false)}
            className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            Keep Running
          </button>
          <button
            onClick={handleAbandonRun}
            disabled={abandonLoading}
            className="px-4 py-2 text-sm bg-amber-600 text-white rounded hover:bg-amber-700 disabled:opacity-50"
          >
            {abandonLoading ? "Abandoning..." : "Abandon Operation"}
          </button>
          </>
        }
      >

        <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
          <p className="text-sm font-medium text-amber-800 mb-1">
            This may leave infrastructure in a partial state
          </p>
          <p className="text-xs text-amber-700">
            Abandoning a running operation releases the Terraform state lock
            so you can start a new operation. If Terraform was mid-apply,
            some resources may have been created or modified. You can re-run
            the operation to reconcile.
          </p>
        </div>

        <p className="text-sm text-gray-600 mb-4">
          Run <span className="font-mono font-medium">#{tfStatus?.active_run_id}</span>{" "}
          ({tfStatus?.active_run_status}) will be marked as cancelled.
        </p>

      </Modal>
      {/* Gate on the cluster redeploy. This is client-side only: the endpoint
          still plans and auto-applies in one request, so the two-step
          awaiting_confirmation flow that e4ed8f9 removed stays removed. */}
      <ConfirmDialog
        open={showRedeployConfirm}
        variant={redeployRecreatesAPool() ? "danger" : "default"}
        title={
          redeployRecreatesAPool()
            ? "Replace the machine type and redeploy?"
            : "Redeploy the cluster now?"
        }
        message={
          redeployRecreatesAPool() ? (
            <>
              <p>
                Replacing a machine type recreates its node pool. Pipelines and
                notebooks running on that pool stop immediately and are not
                resumed.
              </p>
              <p>Nothing changes if you cancel.</p>
            </>
          ) : (
            <>
              <p>
                Applying these changes redeploys the cluster now. The node pools
                are kept, so work already running continues.
              </p>
              <p>Nothing changes if you cancel.</p>
            </>
          )
        }
        confirmLabel="Redeploy"
        busy={configSaving}
        onConfirm={handleApplyClusterConfig}
        onCancel={() => setShowRedeployConfirm(false)}
      />
      <ConfirmDialog
        open={pendingRebuild !== null}
        title="Rebuild the image?"
        message={
          <>
            <p>
              An image for this component already exists. You can rebuild it with the
              latest definition, or enable the component using the image that is
              already there.
            </p>
            <p>Rebuilding takes several minutes. Nothing happens if you cancel.</p>
          </>
        }
        confirmLabel="Rebuild"
        secondaryLabel="Use existing image"
        cancelLabel="Cancel"
        onConfirm={() => {
          const key = pendingRebuild;
          setPendingRebuild(null);
          if (key) handleComponentToggle(key, true);
        }}
        onSecondary={() => {
          const key = pendingRebuild;
          setPendingRebuild(null);
          if (key) handleComponentToggle(key, false);
        }}
        onCancel={() => setPendingRebuild(null)}
      />
    </>
  );
}
