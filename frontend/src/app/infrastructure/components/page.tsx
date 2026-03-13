"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { StorageSection } from "@/components/components/StorageSection";
import { BootstrapCard } from "@/components/infrastructure/BootstrapCard";
import { TerraformProgressModal } from "@/components/infrastructure/TerraformProgressModal";
import { TerraformRunHistory } from "@/components/infrastructure/TerraformRunHistory";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";

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
  cluster: ClusterInfo | null;
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
  const router = useRouter();
  const [refreshKey, setRefreshKey] = useState(0);
  const [tfStatus, setTfStatus] = useState<TerraformStatus | null>(null);
  const [runs, setRuns] = useState<TerraformRun[]>([]);
  const [stackStatus, setStackStatus] = useState<StackStatus | null>(null);
  const [componentsData, setComponentsData] = useState<ComponentsData | null>(null);
  const [clusterConfig, setClusterConfig] = useState<ClusterConfig | null>(null);
  const [showBootstrapModal, setShowBootstrapModal] = useState(false);
  const [showDeployModal, setShowDeployModal] = useState(false);
  const [showTeardownModal, setShowTeardownModal] = useState(false);
  const [showTeardownProgress, setShowTeardownProgress] = useState(false);
  const [teardownChecked, setTeardownChecked] = useState(false);
  const [showConfigPanel, setShowConfigPanel] = useState(false);
  const [componentErrors, setComponentErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadData();
  }, [router, refreshKey]);

  async function loadData() {
    try {
      const status = await api.get<TerraformStatus>("/api/v1/infrastructure/terraform/status");
      setTfStatus(status);
      const runsData = await api.get<{ runs: TerraformRun[] }>("/api/v1/infrastructure/terraform/runs");
      setRuns(runsData.runs);
    } catch {
      // non-admin users won't have access
    }

    try {
      const ss = await api.get<StackStatus>("/api/v1/infrastructure/stack/status");
      setStackStatus(ss);
    } catch {
      // ignore
    }

    try {
      const cd = await api.get<ComponentsData>("/api/v1/infrastructure/stack/components");
      setComponentsData(cd);
    } catch {
      // ignore
    }

    try {
      const cc = await api.get<ClusterConfig>("/api/v1/infrastructure/cluster/config");
      setClusterConfig(cc);
    } catch {
      // ignore
    }
  }

  function handleDeployComplete() {
    setShowDeployModal(false);
    setRefreshKey((k) => k + 1);
  }

  function handleBootstrapComplete() {
    setShowBootstrapModal(false);
    setRefreshKey((k) => k + 1);
  }

  function handleTeardownComplete() {
    setShowTeardownProgress(false);
    setRefreshKey((k) => k + 1);
  }

  async function handleComponentToggle(componentKey: string) {
    setComponentErrors((prev) => ({ ...prev, [componentKey]: "" }));
    try {
      await api.post(`/api/v1/infrastructure/stack/components/${componentKey}/toggle`);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Toggle failed";
      setComponentErrors((prev) => ({ ...prev, [componentKey]: message }));
    }
  }

  const isDeployed = stackStatus?.compute_deployed === true;
  const tfInitialized = tfStatus?.terraform_initialized === true;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Components</h1>

          {/* Bootstrap card (show if terraform not initialized) */}
          {tfStatus && !tfInitialized && (
            <BootstrapCard
              terraformInitialized={false}
              gcpCredentialsConfigured={tfStatus.gcp_credentials_configured}
              onBootstrapStart={() => setShowBootstrapModal(true)}
            />
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
                      Kubernetes + GCS (Recommended)
                    </h3>
                  </div>
                  <p className="text-sm text-gray-600 mb-4">
                    Cloud-native compute with automatic scaling. Pipeline jobs run as
                    containers on Google Kubernetes Engine. Storage is pay-per-use with
                    Google Cloud Storage. Best for most teams.
                  </p>
                  <p className="text-xs text-gray-500 mb-4">
                    $0 when idle. Scales automatically with your workloads.
                  </p>
                  <button
                    onClick={() => setShowDeployModal(true)}
                    className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 font-medium"
                  >
                    Deploy
                  </button>
                </div>

                {/* SLURM + NFS card (coming soon) */}
                <div className="bg-white rounded-lg shadow-md border border-gray-200 p-6 opacity-60">
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="text-lg font-semibold text-gray-400">SLURM + NFS</h3>
                    <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                      Coming Soon
                    </span>
                  </div>
                  <p className="text-sm text-gray-400 mb-4">
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
                  Compute Stack: Kubernetes + GCS
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
                      className="text-sm text-blue-600 hover:text-blue-800"
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
                        <p>Machine Type: {stackStatus.cluster.interactive_pool.machine_type}</p>
                        <p>Max Nodes: {stackStatus.cluster.interactive_pool.max_nodes}</p>
                        <p>Current: {stackStatus.cluster.interactive_pool.current_nodes}</p>
                      </div>
                    </div>
                  </div>

                  {/* Configuration Panel */}
                  {showConfigPanel && clusterConfig && (
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <h4 className="text-sm font-medium mb-3">Cluster Configuration</h4>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                          <label className="text-xs text-gray-500">Pipeline Machine Type</label>
                          <input
                            type="text"
                            defaultValue={clusterConfig.k8s_pipeline_machine_type}
                            className="w-full border rounded px-2 py-1 text-sm mt-1"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-500">Pipeline Max Nodes</label>
                          <input
                            type="number"
                            defaultValue={clusterConfig.k8s_pipeline_max_nodes}
                            className="w-full border rounded px-2 py-1 text-sm mt-1"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-500">Interactive Machine Type</label>
                          <input
                            type="text"
                            defaultValue={clusterConfig.k8s_interactive_machine_type}
                            className="w-full border rounded px-2 py-1 text-sm mt-1"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-500">Interactive Max Nodes</label>
                          <input
                            type="number"
                            defaultValue={clusterConfig.k8s_interactive_max_nodes}
                            className="w-full border rounded px-2 py-1 text-sm mt-1"
                          />
                        </div>
                      </div>
                      <div className="flex gap-2 mt-4">
                        <button className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
                          Preview Changes
                        </button>
                        <button
                          onClick={() => setShowConfigPanel(false)}
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
                          .map((comp) => (
                            <div
                              key={comp.key}
                              className="bg-white rounded-lg shadow p-5 border border-gray-200"
                            >
                              <div className="flex items-start justify-between mb-2">
                                <h3 className="font-semibold text-sm">{comp.name}</h3>
                                <span
                                  className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                    comp.status === "enabled"
                                      ? "bg-green-100 text-green-700"
                                      : "bg-gray-100 text-gray-500"
                                  }`}
                                >
                                  {comp.status === "enabled" ? "Enabled" : "Disabled"}
                                </span>
                              </div>
                              <p className="text-xs text-gray-600 mb-3">{comp.description}</p>
                              {comp.dependencies.length > 0 && (
                                <p className="text-xs text-gray-400 mb-2">
                                  Requires: {comp.dependencies.join(", ")}
                                </p>
                              )}
                              <div className="flex items-center justify-between mt-3">
                                <span className="text-xs text-gray-500">{comp.cost_estimate}</span>
                                <button
                                  onClick={() => handleComponentToggle(comp.key)}
                                  className={`px-3 py-1 text-xs rounded font-medium ${
                                    comp.status === "enabled"
                                      ? "bg-red-50 text-red-700 hover:bg-red-100"
                                      : "bg-blue-600 text-white hover:bg-blue-700"
                                  }`}
                                >
                                  {comp.status === "enabled" ? "Disable" : "Enable"}
                                </button>
                              </div>
                              {componentErrors[comp.key] && (
                                <p className="text-xs text-red-600 mt-2">
                                  {componentErrors[comp.key]}
                                </p>
                              )}
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
                  onDeploy={() => {}}
                />
              </div>
            </>
          )}

          {/* Run History */}
          <div className="mt-10">
            <h2 className="text-xl font-semibold mb-4">Recent Operations</h2>
            <TerraformRunHistory runs={runs} />
          </div>
        </main>
      </div>

      {/* Bootstrap Modal */}
      {showBootstrapModal && (
        <TerraformProgressModal
          title="Initialize Infrastructure"
          sseUrl="/api/v1/infrastructure/terraform/bootstrap"
          onComplete={handleBootstrapComplete}
          onClose={() => setShowBootstrapModal(false)}
        />
      )}

      {/* Deploy Stack Modal */}
      {showDeployModal && (
        <TerraformProgressModal
          title="Deploy Compute Stack"
          sseUrl="/api/v1/infrastructure/stack/deploy"
          onComplete={handleDeployComplete}
          onClose={() => setShowDeployModal(false)}
        />
      )}

      {/* Teardown Confirmation Modal */}
      {showTeardownModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
            <h2 className="text-lg font-semibold mb-4">Teardown Compute Stack</h2>
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
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowTeardownModal(false)}
                className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                disabled={!teardownChecked}
                onClick={() => {
                  setShowTeardownModal(false);
                  setShowTeardownProgress(true);
                }}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Teardown
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Teardown Progress Modal */}
      {showTeardownProgress && (
        <TerraformProgressModal
          title="Teardown Compute Stack"
          sseUrl="/api/v1/infrastructure/stack/teardown"
          onComplete={handleTeardownComplete}
          onClose={() => setShowTeardownProgress(false)}
        />
      )}
    </div>
  );
}
