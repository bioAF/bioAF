"use client";

import { useState, Suspense } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";
import { DataExportModal } from "@/components/experiments/DataExportModal";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { FileBrowser } from "@/components/files/FileBrowser";
import { LiteratureTabPanel } from "@/components/literature/LiteratureTabPanel";
import { AgentReviewTab } from "@/components/agent-reviews/AgentReviewTab";
import { AgentReviewButtons } from "@/components/agent-reviews/AgentReviewButtons";
import { ExperimentOverviewTab } from "@/components/experiments/detail/ExperimentOverviewTab";
import { ExperimentSamplesTab, ExperimentSampleDialogs } from "@/components/experiments/detail/ExperimentSamplesTab";
import { ExperimentBatchesTab } from "@/components/experiments/detail/ExperimentBatchesTab";
import {
  ExperimentAnalysisTab,
  ExperimentAuditTab,
  ExperimentPipelineRunsTab,
} from "@/components/experiments/detail/ExperimentActivityTabs";
import { ExperimentResultsTab } from "@/components/experiments/detail/ExperimentResultsTab";
import { useOverviewEditor } from "@/components/experiments/detail/useOverviewEditor";
import { useSampleWorkspace } from "@/components/experiments/detail/useSampleWorkspace";
import { useBatchWorkspace } from "@/components/experiments/detail/useBatchWorkspace";
import { getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { useResource } from "@/hooks/useResource";
import { ExperimentTabKey, resolveExperimentTab } from "@/lib/experimentTabs";
import type { ExperimentDetail, NamingProfile } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { TabList, TabPanel, useTabIds } from "@/components/ui/Tabs";

type Tab = ExperimentTabKey;

export default function ExperimentDetailPage() {
  return (
    <Suspense fallback={null}>
      <ExperimentDetailRoute />
    </Suspense>
  );
}

/**
 * Each experiment gets its own owner: moving the route to another experiment remounts it, so no
 * record, rows, draft, selection, error or loading state of the previous one can reach the next.
 */
function ExperimentDetailRoute() {
  const params = useParams();
  const id = params.id as string;
  return <ExperimentDetailOwner key={id} id={id} />;
}

function isNotFound(e: unknown): boolean {
  return (e as { status?: number } | null)?.status === 404;
}

function ExperimentDetailOwner({ id }: { id: string }) {
  const toast = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { canAccess } = usePermissions();
  const canViewResults =
    canAccess("experiments", "view") || canAccess("pipelines", "view");

  // Only a 404 is a missing record. A 500, an expired gateway or a dropped connection was once
  // reported to the scientist as a deleted record.
  const record = useResource<ExperimentDetail>(
    `experiment:${id}`,
    (signal) => api.get<ExperimentDetail>(`/api/experiments/${id}`, { signal }),
    { expected: isNotFound, onError: (e) => logError(`loading experiment ${id}`, e) },
  );
  const profiles = useResource<NamingProfile[]>(
    "naming-profiles:active",
    (signal) => api.get<NamingProfile[]>("/api/naming-profiles?status=active", { signal }),
  );
  const namingProfiles = Array.isArray(profiles.data) ? profiles.data : [];

  const [activeTab, setActiveTab] = useState<Tab>(() => resolveExperimentTab(searchParams?.get("tab")));
  const tabIds = useTabIds();
  const [aiReviewSignal, setAiReviewSignal] = useState(0);
  const [showGeoExport, setShowGeoExport] = useState(false);
  const [showDataExport, setShowDataExport] = useState(false);

  const refreshExperiment = () => void record.refresh();
  const overview = useOverviewEditor(id, refreshExperiment);
  const samples = useSampleWorkspace(id, activeTab === "samples", refreshExperiment);
  const batches = useBatchWorkspace(id, activeTab === "batches", refreshExperiment);

  async function handleStatusUpdate(newStatus: string) {
    try {
      await api.patch(`/api/experiments/${id}/status`, { status: newStatus });
      refreshExperiment();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  if (record.loading) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LoadingSpinner size="lg" />
      </main>
    );
  }

  const experiment = record.data;
  if (!experiment) {
    return (
      <main className="flex-1 flex items-center justify-center">
        {record.denied ? (
          <p className="text-ink-subtle">Experiment not found</p>
        ) : (
          <div data-testid="experiment-load-failed" role="status" className="text-center">
            <p className="text-ink-subtle">{loadFailureMessage("This experiment")}</p>
            <button
              onClick={refreshExperiment}
              className="mt-3 underline text-sm hover:no-underline"
            >
              Retry
            </button>
          </div>
        )}
      </main>
    );
  }

  const role = (getCurrentUser()?.role_name as string) || "viewer";
  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "samples", label: `Samples (${experiment.sample_count})` },
    { key: "batches", label: "Batches" },
    { key: "files", label: "Files" },
    { key: "literature", label: "Literature" },
    { key: "analysis", label: "Analysis" },
    { key: "pipelines", label: "Pipeline Runs" },
    ...(canViewResults ? [{ key: "results" as Tab, label: "Results" }] : []),
    { key: "provenance", label: "Provenance" },
    { key: "audit", label: "Audit Trail" },
    { key: "agent_review", label: "AI Review" },
  ];

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {record.stale && <StaleNotice what="This experiment" onRetry={refreshExperiment} />}
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => router.push("/experiments")} className="text-ink-subtle hover:text-ink-muted">
          ← Back
        </button>
        <h1 className="text-2xl font-bold">{experiment.name}</h1>
        {experiment.code && (
          <span className="text-sm font-mono bg-elevated text-gray-600 px-2 py-0.5 rounded" title="Internal ID">{experiment.code}</span>
        )}
        {experiment.external_id && (
          <span className="text-sm font-mono bg-blue-50 text-blue-700 px-2 py-0.5 rounded" title="External ID">{experiment.external_id}</span>
        )}
        <ExperimentStatusBadge status={experiment.status} />
        <div className="ml-auto flex items-center gap-2">
          <ProvenanceExportMenu entityType="experiments" entityId={Number(id)} />
          {["admin", "comp_bio"].includes(role) ? (
            <>
              <Button variant="secondary" onClick={() => setShowDataExport(true)}>
                Export Data
              </Button>
              <Button onClick={() => setShowGeoExport(true)}>Export to GEO</Button>
            </>
          ) : null}
        </div>
      </div>

      <TabList label="Experiment sections" ids={tabIds} tabs={tabs} selected={activeTab} onSelect={setActiveTab} />

      <TabPanel ids={tabIds} tabKey={activeTab}>
        {activeTab === "overview" && (
          <ExperimentOverviewTab
            experiment={experiment}
            editor={overview}
            namingProfiles={namingProfiles}
            onStatusChange={(status) => void handleStatusUpdate(status)}
          />
        )}

        {activeTab === "samples" && <ExperimentSamplesTab experiment={experiment} workspace={samples} />}

        {activeTab === "batches" && <ExperimentBatchesTab workspace={batches} />}

        {activeTab === "files" && (
          <div>
            <h2 className="text-lg font-semibold mb-4">Files</h2>
            <FileBrowser experimentId={Number(id)} showSearch showUpload />
          </div>
        )}

        {activeTab === "literature" && (
          <div>
            <h2 className="text-lg font-semibold mb-4">Literature</h2>
            <LiteratureTabPanel experimentId={Number(id)} />
          </div>
        )}

        {activeTab === "analysis" && <ExperimentAnalysisTab experimentId={id} />}

        {activeTab === "pipelines" && <ExperimentPipelineRunsTab experimentId={id} />}

        {activeTab === "results" && <ExperimentResultsTab experimentId={Number(id)} />}

        {activeTab === "provenance" && (
          <ProvenanceReportPanel entityType="experiment" entityId={Number(id)} entityName={experiment.name} />
        )}

        {activeTab === "audit" && <ExperimentAuditTab experimentId={id} />}

        {activeTab === "agent_review" && (
          <div className="space-y-4">
            <AgentReviewButtons
              mode="experiment"
              experimentId={experiment.id}
              onTriggered={() => setAiReviewSignal((v) => v + 1)}
            />
            <AgentReviewTab entityType="experiment" entityId={experiment.id} refreshSignal={aiReviewSignal} />
          </div>
        )}
      </TabPanel>
      <ExperimentSampleDialogs experiment={experiment} workspace={samples} />
      <DataExportModal
        experimentId={Number(id)}
        experimentName={experiment.name}
        isOpen={showDataExport}
        onClose={() => setShowDataExport(false)}
      />
      <GeoExportModal
        experimentId={Number(id)}
        isOpen={showGeoExport}
        onClose={() => setShowGeoExport(false)}
        userRole={role}
      />
    </main>
  );
}
