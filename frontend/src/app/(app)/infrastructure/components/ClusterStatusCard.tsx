"use client";

import { useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { notebookSupportForMachine } from "@/lib/notebookCapacity";
import {
  INTERACTIVE_MACHINE_OPTIONS,
  PIPELINE_MACHINE_OPTIONS,
  formatMachineOptionLabel,
} from "@/lib/clusterMachineOptions";
import { DISK_TYPE_OPTIONS, describeDiskFor } from "./clusterDiskOptions";
import { applyIsBlocked, describePoolCapacity, describeVerdict, type QuotaVerdict } from "./clusterQuotaVerdict";
import type { ClusterConfig, StackStatus } from "./infrastructureTypes";

/**
 * The deployed cluster's status and its configuration panel: pending edits, the live quota
 * preflight (a read, open to any viewer), and applying them, which redeploys the cluster and
 * needs infrastructure `configure`. Opening or remounting the panel never applies anything.
 */
export function ClusterStatusCard({
  stackStatus,
  clusterConfig,
  configRead,
  onApplied,
}: {
  stackStatus: StackStatus | null;
  clusterConfig: ClusterConfig | null;
  /**
   * The page's configuration read. Retry re-reads only the configuration; the edits not yet
   * applied are this card's and survive it.
   */
  configRead: { loading: boolean; failed: boolean; stale: boolean; retry: () => void };
  /** The redeploy started; the page shows its progress. */
  onApplied: () => void;
}) {
  const { has } = useCapabilities();
  const actions = useInfrastructureActions();
  const configureReason = actions.reason("configure");
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [showSpotTooltip, setShowSpotTooltip] = useState(false);
  const [configEdits, setConfigEdits] = useState<Partial<ClusterConfig>>({});
  const [quotaVerdict, setQuotaVerdict] = useState<QuotaVerdict | null>(null);
  const [configSaving, setConfigSaving] = useState(false);
  const [showRedeployConfirm, setShowRedeployConfirm] = useState(false);
  const [configError, setConfigError] = useState("");

  /**
   * Price the pending edits against the region's live quota.
   *
   * Debounced because it runs on every keystroke in the disk-size field, and it is a cloud
   * round-trip. With no pending edits the panel falls back to the verdict for the CURRENT pool,
   * which is what the capacity line reports.
   *
   * A failed preflight clears the verdict rather than blocking: the whole design fails open, and
   * an operator must never be stopped by a call that did not answer.
   */
  useEffect(() => {
    if (!showConfigPanel) return;
    if (Object.keys(configEdits).length === 0) {
      setQuotaVerdict(clusterConfig?.pipeline_pool_quota ?? null);
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const verdict = await api.post<QuotaVerdict>(
          "/api/v1/infrastructure/cluster/config/preflight",
          configEdits,
        );
        if (!cancelled) setQuotaVerdict(verdict);
      } catch (e) {
        logError("cluster config quota preflight", e);
        if (!cancelled) setQuotaVerdict(null);
      }
    }, 400);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [configEdits, showConfigPanel, clusterConfig]);

  /**
   * True when the pending edits change something in a pool's `node_config`,
   * which is what forces GKE to recreate the pool: the machine type, or the
   * node disk's size or type. Scaling max_nodes or flipping spot does not
   * destroy running work, so the confirmation must not claim it does.
   */
  function redeployRecreatesAPool(): boolean {
    return (
      configEdits.k8s_pipeline_machine_type !== undefined ||
      // A disk change recreates the node pool, exactly like a machine-type change.
      configEdits.k8s_pipeline_disk_size_gb !== undefined ||
      configEdits.k8s_pipeline_disk_type !== undefined ||
      configEdits.k8s_interactive_machine_type !== undefined
    );
  }

  async function handleApplyClusterConfig() {
    setShowRedeployConfirm(false);
    // Permission is checked again: it may have changed while the confirmation was open.
    if (!actions.stillAllowed("configure")) return;
    setConfigError("");
    setConfigSaving(true);
    try {
      await api.post("/api/v1/infrastructure/cluster/config", configEdits);
      setConfigEdits({});
      setShowConfigPanel(false);
      onApplied();
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : "Failed to apply changes");
    } finally {
      setConfigSaving(false);
    }
  }

  return (
    <>
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
        {showConfigPanel && configRead.loading && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <LoadingSpinner label="Loading the cluster configuration" />
          </div>
        )}
        {showConfigPanel && !configRead.loading && configRead.failed && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <ErrorState message={loadFailureMessage("Cluster configuration")} onRetry={configRead.retry} />
          </div>
        )}
        {showConfigPanel && !configRead.loading && clusterConfig && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <h4 className="text-sm font-medium mb-3">Cluster Configuration</h4>
            {configRead.stale && <StaleNotice what="Cluster configuration" onRetry={configRead.retry} />}

            {configError && (
              <div className="mb-3 p-2 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
                {configError}
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Pipeline Nodes column */}
              <div className="space-y-3">
                <h5 className="text-sm font-medium text-gray-700">Pipeline Nodes</h5>
                <p data-testid="pipeline-pool-capacity" className="text-xs text-gray-500">
                  {describePoolCapacity(
                    clusterConfig.pipeline_pool_quota,
                    clusterConfig.k8s_pipeline_max_nodes,
                  )}
                </p>
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
                <div>
                  <label htmlFor="pipeline-disk-size" className="text-xs text-gray-500">
                    Node Disk (GB)
                  </label>
                  <input id="pipeline-disk-size"
                    type="number"
                    min={50}
                    max={1000}
                    value={configEdits.k8s_pipeline_disk_size_gb ?? clusterConfig.k8s_pipeline_disk_size_gb}
                    onChange={(e) =>
                      setConfigEdits({ ...configEdits, k8s_pipeline_disk_size_gb: Number(e.target.value) })
                    }
                    className="w-full border rounded px-2 py-1 text-sm mt-1"
                    disabled={configSaving}
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    {describeDiskFor(
                      configEdits.k8s_pipeline_disk_size_gb ?? clusterConfig.k8s_pipeline_disk_size_gb,
                    )}
                  </p>
                </div>
                <div>
                  <label htmlFor="pipeline-disk-type" className="text-xs text-gray-500">
                    Node Disk Type
                  </label>
                  <select id="pipeline-disk-type"
                    value={configEdits.k8s_pipeline_disk_type ?? clusterConfig.k8s_pipeline_disk_type}
                    onChange={(e) => setConfigEdits({ ...configEdits, k8s_pipeline_disk_type: e.target.value })}
                    className="w-full border rounded px-2 py-1 text-sm mt-1 bg-white"
                    disabled={configSaving}
                  >
                    {DISK_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
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
            {(() => {
              // What this region's quota allows for the pending edits. A config that
              // could not build one node is refused here rather than accepted, applied,
              // reported RUNNING, and discovered as a run whose pods never schedule.
              const display = describeVerdict(quotaVerdict);
              if (!display) return null;
              const tone =
                display.tone === "error"
                  ? "text-red-700 bg-red-50 border-red-200"
                  : display.tone === "warning"
                    ? "text-amber-700 bg-amber-50 border-amber-200"
                    : "text-gray-600 bg-gray-50 border-gray-200";
              return (
                <p
                  data-testid="cluster-quota-verdict"
                  className={`text-xs border rounded px-3 py-2 mt-2 ${tone}`}
                >
                  {display.text}
                </p>
              );
            })()}
            <div className="flex gap-2 mt-4">
              <button
                disabled={
                  configSaving ||
                  Object.keys(configEdits).length === 0 ||
                  applyIsBlocked(quotaVerdict) ||
                  configureReason !== null
                }
                title={configureReason ?? undefined}
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
    </>
  );
}
