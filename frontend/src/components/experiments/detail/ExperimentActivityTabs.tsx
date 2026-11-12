"use client";

/**
 * The experiment tabs that only read and list: linked notebook sessions, pipeline runs, and the
 * audit trail. Each reads while it is mounted (the selected tab), through the shared resource
 * lifecycle, and announces a failed read instead of rendering it as an empty list.
 */
import { useRouter } from "next/navigation";
import { NOT_SET } from "@/lib/placeholders";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import { AutoRunConfigSection } from "@/components/experiments/AutoRunConfigSection";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { AuditLogResponse, PipelineRunListResponse, SessionListResponse } from "@/lib/types";

function useAnnouncedRead<T>(key: string, path: string, what: string, context: string) {
  const toast = useToast();
  return useResource<T>(key, (signal) => api.get<T>(path, { signal }), {
    onError: (e) => {
      logError(context, e);
      toast.error(loadFailureMessage(what));
    },
  });
}

export function ExperimentAnalysisTab({ experimentId }: { experimentId: string }) {
  const toast = useToast();
  const sessions = useAnnouncedRead<SessionListResponse>(
    `experiment:${experimentId}:notebook-sessions`,
    "/api/notebooks/sessions",
    "Notebook sessions",
    "loading the notebook sessions",
  );
  const linked = (sessions.data?.sessions ?? []).filter((s) => s.experiment?.id === Number(experimentId));

  async function launch(sessionType: "jupyter" | "rstudio") {
    try {
      await api.post("/api/notebooks/sessions", {
        session_type: sessionType,
        resource_profile: "small",
        experiment_id: Number(experimentId),
      });
      void sessions.refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to launch session");
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-lg font-semibold mb-4">Launch Notebook</h2>
        <p className="text-sm text-ink-subtle mb-4">
          Start a Jupyter or RStudio session pre-linked to this experiment.
        </p>
        <div className="flex gap-3">
          <Button onClick={() => void launch("jupyter")}>Launch Jupyter</Button>
          <Button onClick={() => void launch("rstudio")}>Launch RStudio</Button>
        </div>
      </Card>

      {linked.length > 0 && (
        <Card padding="none">
          <div className="p-6 border-b">
            <h2 className="text-lg font-semibold">Linked Sessions</h2>
          </div>
          <table className="min-w-full divide-y divide-hairline">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Type</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Profile</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {linked.map((s) => (
                <tr key={s.id}>
                  <td className="px-4 py-3 text-sm capitalize">{s.session_type}</td>
                  <td className="px-4 py-3 text-sm">{s.status}</td>
                  <td className="px-4 py-3 text-sm capitalize">{s.resource_profile}</td>
                  <td className="px-4 py-3 text-sm text-ink-subtle">{new Date(s.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <SnapshotTimeline experimentId={Number(experimentId)} />
    </div>
  );
}

export function ExperimentPipelineRunsTab({ experimentId }: { experimentId: string }) {
  const router = useRouter();
  const runs = useAnnouncedRead<PipelineRunListResponse>(
    `experiment:${experimentId}:pipeline-runs`,
    `/api/pipeline-runs?experiment_id=${experimentId}`,
    "Pipeline runs",
    "loading the pipeline runs",
  );
  const pipelineRuns = runs.data?.runs ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Pipeline Runs</h2>
        <Button onClick={() => router.push(`/pipelines?experiment=${experimentId}`)}>Launch Pipeline</Button>
      </div>
      {pipelineRuns.length === 0 ? (
        <div className="bg-surface rounded-lg shadow p-12 text-center">
          <p className="text-ink-subtle">No pipeline runs for this experiment yet.</p>
        </div>
      ) : (
        <div className="bg-surface rounded-lg shadow overflow-x-auto">
          <table className="min-w-full divide-y divide-hairline">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Pipeline</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Progress</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Started</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {pipelineRuns.map((r) => (
                <tr key={r.id} className="hover:bg-surface-muted">
                  <td className="px-4 py-3 text-sm">{r.pipeline_name} {r.pipeline_version ? `v${r.pipeline_version}` : ""}</td>
                  <td className="px-4 py-3"><span className={`px-2 py-0.5 text-xs rounded-full ${statusBadgeClass("pipelineRun", r.status)}`}>{r.status}</span></td>
                  <td className="px-4 py-3">
                    {r.progress ? (
                      <div className="flex items-center gap-2">
                        <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                          <div className="h-full bg-bioaf-500 rounded-full" style={{ width: `${r.progress.percent_complete}%` }} />
                        </div>
                        <span className="text-xs">{Math.round(r.progress.percent_complete)}%</span>
                      </div>
                    ) : NOT_SET}
                  </td>
                  <td className="px-4 py-3 text-sm text-ink-subtle">{r.started_at ? new Date(r.started_at).toLocaleString() : NOT_SET}</td>
                  <td className="px-4 py-3">
                    <button onClick={() => router.push(`/pipelines/runs/${r.id}`)} className="text-bioaf-600 text-sm hover:underline">View</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AutoRunConfigSection experimentId={Number(experimentId)} />
    </div>
  );
}

export function ExperimentAuditTab({ experimentId }: { experimentId: string }) {
  const audit = useAnnouncedRead<AuditLogResponse>(
    `experiment:${experimentId}:audit:1`,
    `/api/experiments/${experimentId}/audit?page=1`,
    "The audit trail",
    "loading the audit trail",
  );
  const entries = audit.data?.entries ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Audit Trail ({audit.data?.total ?? 0} entries)</h2>
      </div>
      <div className="bg-surface rounded-lg shadow overflow-x-auto">
        <table className="min-w-full divide-y divide-hairline">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Timestamp</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Entity</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Action</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {entries.map((e) => (
              <tr key={e.id}>
                <td className="px-4 py-3 text-sm text-ink-subtle">{new Date(e.timestamp).toLocaleString()}</td>
                <td className="px-4 py-3 text-sm">{e.entity_type} #{e.entity_id}</td>
                <td className="px-4 py-3 text-sm">{e.action}</td>
                <td className="px-4 py-3 text-sm text-ink-subtle">
                  {e.details ? (
                    <details>
                      <summary className="cursor-pointer">View</summary>
                      <pre className="text-xs mt-1 bg-gray-50 p-2 rounded overflow-auto max-w-md">
                        {JSON.stringify(e.details, null, 2)}
                      </pre>
                    </details>
                  ) : NOT_SET}
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-ink-subtle">No audit entries</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
