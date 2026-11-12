"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { NOT_SET } from "@/lib/placeholders";
import { useConfirm } from "@/hooks/useConfirm";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { api, ApiError } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { usePermissions } from "@/hooks/usePermissions";
import { SessionBucketFilter, type SessionBucket } from "@/components/shared/SessionBucketFilter";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { useResource } from "@/hooks/useResource";
import { useToast } from "@/components/shared/Toast";
import { formatLinkedTo } from "@/lib/sessionStatus";
import { prefillFromWorkNode, type WorkNodeRecreatePrefill } from "@/lib/sessionRecreate";
import type { GitHubRepoListResponse, WorkNode, WorkNodeLaunchRequest, WorkNodeListResponse } from "@/lib/types";
import { statusBadgeClass } from "@/lib/statusStyles";
import { clickableRow } from "@/lib/a11y";
import { Button } from "@/components/ui/Button";
import { formatRecordedTime } from "@/lib/recordedTime";
import { Alert } from "@/components/shared/Alert";
import { Card } from "@/components/ui/Card";
import { ListContent } from "@/components/shared/ListContent";
import { WorkNodeLaunchDialog } from "@/components/workNodes/WorkNodeLaunchDialog";
import { WorkNodeDetailModal } from "@/components/workNodes/WorkNodeDetailModal";
import { GitHubReposPanel } from "@/components/workNodes/GitHubReposPanel";
import { sshCommand, workNodeStatusLabel } from "@/components/workNodes/workNodeDisplay";

interface ProvisioningNotice {
  id: number;
  kind: "info" | "error";
  message: string;
}

/**
 * The work-node page owns what outlives a dialog: the node list and its polling, the
 * repositories, and each launch's provisioning notice. The launch dialog is mounted per opening
 * and owns its draft; the detail view is a dialog over the current list.
 */
export default function WorkNodesPage() {
  const toast = useToast();
  const confirm = useConfirm();
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();

  const [bucket, setBucket] = useState<SessionBucket>("active");
  // The whole-page spinner covers only the first answer; a later bucket change waits
  // inside the list so the bucket filter stays on screen.
  const [loadedOnce, setLoadedOnce] = useState(false);
  const loading = !loadedOnce;
  const [viewingNode, setViewingNode] = useState<WorkNode | null>(null);
  const [launch, setLaunch] = useState<{ key: number; prefill: WorkNodeRecreatePrefill | null } | null>(null);
  const openings = useRef(0);
  // One notice per launch sent, kept on the page after the dialog closes until it settles (or,
  // for a failure, until dismissed). Each launch only ever changes its own notice.
  const [notices, setNotices] = useState<ProvisioningNotice[]>([]);
  const launchCount = useRef(0);
  const [stoppingNodes, setStoppingNodes] = useState<Set<number>>(new Set());
  const [showGuide, setShowGuide] = useState(false);

  const canView = !permLoading && canAccess("work_nodes", "view");
  const canLaunch = canAccess("work_nodes", "launch");
  const canStop = canAccess("work_nodes", "stop");

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("work_nodes", "view")) router.push("/dashboard");
  }, [router, permLoading, canAccess]);

  // The node list, keyed by the session bucket. Auto-refresh every 10 s while a node
  // is starting or stopping; each read starts after the previous one settles, and a
  // bucket's late answer cannot replace another bucket's.
  const nodesPath = `/api/v1/work-nodes/sessions?bucket=${bucket}`;
  const nodeList = useResource(
    canView ? nodesPath : null,
    useCallback(
      (signal: AbortSignal) => api.get<WorkNodeListResponse>(nodesPath, { signal }),
      [nodesPath],
    ),
    {
      poll: ({ data }) =>
        data?.sessions.some((n) => n.status === "starting" || n.status === "stopping") ? 10000 : false,
      // Ends the page's first-load spinner; the list itself shows the hook's own loading state.
      onSuccess: () => setLoadedOnce(true),
      onError: (e) => {
        // Never fall through to the empty state here: these nodes bill by the hour,
        // and "you have none" is a very different claim from "we could not ask".
        logError("loading work nodes", e);
        setLoadedOnce(true);
      },
    },
  );
  const nodes: WorkNode[] = nodeList.data?.sessions ?? [];
  const loadError = nodeList.error && !nodeList.stale ? loadFailureMessage("Work nodes") : null;
  const loadNodes = nodeList.refresh;

  const repoList = useResource(
    canView ? "github-repos" : null,
    (signal) => api.get<GitHubRepoListResponse>("/api/v1/github-repos", { signal }),
    {
      onError: (e) => {
        logError("loading GitHub repositories", e);
        toast.error(loadFailureMessage("Repositories"));
      },
    },
  );
  const repos = repoList.data?.repos ?? [];

  function openLaunch(prefill: WorkNodeRecreatePrefill | null) {
    openings.current += 1;
    setLaunch({ key: openings.current, prefill });
    setViewingNode(null);
  }

  /** Provisioning takes minutes and is watched from the page; closing the dialog never cancels it. */
  async function sendLaunch(request: WorkNodeLaunchRequest) {
    setLaunch(null);
    const id = ++launchCount.current;
    setNotices((prev) => [
      ...prev,
      { id, kind: "info", message: "Provisioning work node... this can take 1-3 minutes." },
    ]);
    try {
      await api.post("/api/v1/work-nodes/sessions", request);
      setNotices((prev) => prev.filter((n) => n.id !== id));
      void loadNodes();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Launch failed";
      setNotices((prev) => prev.map((n) => (n.id === id ? { ...n, kind: "error", message } : n)));
    }
  }

  async function handleStop(nodeId: number) {
    const ok = await confirm({
      title: "Stop this work node?",
      message: "Files in /outputs/ will be synced to GCS. Data in /scratch will be lost.",
      confirmLabel: "Stop",
      variant: "danger",
    });
    if (!ok) return;
    setStoppingNodes((prev) => new Set(prev).add(nodeId));
    try {
      await api.post(`/api/v1/work-nodes/sessions/${nodeId}/stop`);
      void loadNodes();
      setViewingNode((current) => (current?.id === nodeId ? null : current));
    } catch (e) {
      // A silent failure here leaves a node running and billing while the user
      // believes they stopped it.
      toast.error(e instanceof Error ? e.message : "Could not stop the work node.");
    } finally {
      setStoppingNodes((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  }

  if (permLoading || loading) {
    return (
      <main className="flex-1 flex items-center justify-center">
        <LoadingSpinner />
      </main>
    );
  }

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Work Nodes</h1>
          <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
            Full Linux VMs with SSH access, for work that does not fit a notebook or a pipeline.
          </p>
        </div>
        {canLaunch && <Button onClick={() => openLaunch(null)}>New Work Node</Button>}
      </div>

      {notices.map((notice) => (
        <Alert
          key={notice.id}
          tone={notice.kind === "error" ? "error" : "info"}
          className="mb-4"
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setNotices((prev) => prev.filter((n) => n.id !== notice.id))}
            >
              Dismiss
            </Button>
          }
        >
          <span className="flex items-center gap-2">
            {notice.kind === "info" && <LoadingSpinner size="sm" />}
            <span>{notice.message}</span>
          </span>
        </Alert>
      ))}

      {/* Quick Start Guide */}
      <div className="mb-6">
        <button
          onClick={() => setShowGuide(!showGuide)}
          aria-expanded={showGuide}
          className="inline-flex items-center gap-1.5 text-sm text-bioaf-600 hover:text-bioaf-700"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          How work nodes work
        </button>
        {showGuide && (
          <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-4">
            <div className="text-sm text-blue-800 space-y-2">
              <ul className="space-y-1.5 text-blue-700">
                <li><strong>Work nodes</strong> are full Linux VMs with SSH access. They run the conda software stack you configure on the <a href="/environments" className="underline font-medium">Workbench Templates</a> page.</li>
                <li><strong>Input files</strong> are mounted at <code className="bg-blue-100 px-1 rounded">/data/</code>. Select data mounts during launch to access pipeline outputs, uploads, and shared results.</li>
                <li><strong>GitHub repos</strong> are cloned at boot into <code className="bg-blue-100 px-1 rounded">~/repos/</code>. Add repos in the section below, then select them when launching.</li>
                <li><strong>Output files</strong> should be saved to <code className="bg-blue-100 px-1 rounded">/outputs/</code>. Everything here is automatically synced to GCS when you stop the node.</li>
                <li><strong>Scratch space</strong> at <code className="bg-blue-100 px-1 rounded">/scratch/</code> is for temporary computation. This data is lost when the node stops.</li>
                <li><strong>SSH access</strong> uses the credentials from your <a href="/profile" className="underline font-medium">Profile Settings</a>. The SSH command appears after launch.</li>
              </ul>
            </div>
          </div>
        )}
      </div>

      <GitHubReposPanel repos={repos} canManage={canLaunch} onChanged={() => void repoList.refresh()} />

      {/* Node list */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-gray-900">Work Nodes</h2>
        <SessionBucketFilter value={bucket} onChange={setBucket} />
      </div>
      {nodeList.stale && <StaleNotice what="Work nodes" onRetry={() => void loadNodes()} />}
      <ListContent
        loading={nodeList.loading}
        error={loadError}
        onRetry={() => void loadNodes()}
        empty={nodes.length === 0}
        emptyState={
          <Card className="text-center">
            <p className="text-gray-500">No work nodes in this view.</p>
          </Card>
        }
      >
        <Card padding="none" className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Linked to</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Machine Type</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resources</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Start Time</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {nodes.map((node) => (
                <tr key={node.id} className="hover:bg-gray-50 cursor-pointer" {...clickableRow(() => setViewingNode(node))}>
                  <td className="px-4 py-3 text-sm">{node.user?.name || node.user?.email || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatLinkedTo({ project: node.project }) ?? NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-900">{node.machine_type || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{node.cpu_cores} CPU / {node.memory_gb} GB</td>
                  <td className="px-4 py-3">
                    {stoppingNodes.has(node.id) ? (
                      <span className="flex items-center gap-1 text-xs text-orange-700">
                        <LoadingSpinner size="sm" />
                        Syncing outputs...
                      </span>
                    ) : (
                      <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClass("computeSession", node.status)}`}>
                        {workNodeStatusLabel(node)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {formatRecordedTime(node.started_at)}
                  </td>
                  <td className="px-4 py-3 text-sm font-mono" onClick={(e) => e.stopPropagation()}>
                    {node.access_url && node.status === "running" ? (
                      <button
                        onClick={() => navigator.clipboard.writeText(sshCommand(node.access_url))}
                        className="text-bioaf-600 hover:underline"
                        title={sshCommand(node.access_url)}
                      >
                        {sshCommand(node.access_url)}
                      </button>
                    ) : NOT_SET}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-2">
                      {canStop && node.status === "running" && (
                        <button
                          onClick={() => handleStop(node.id)}
                          className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50"
                        >
                          Stop
                        </button>
                      )}
                      {canLaunch && (
                        <button
                          onClick={() => openLaunch(prefillFromWorkNode(node))}
                          className="text-xs px-2 py-1 border border-green-600 text-green-700 rounded hover:bg-green-50"
                        >
                          Recreate
                        </button>
                      )}
                      <button
                        onClick={() => setViewingNode(node)}
                        className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50"
                      >
                        Details
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </ListContent>

      {viewingNode && (
        <WorkNodeDetailModal
          node={viewingNode}
          repos={repos}
          canStop={canStop}
          onStop={(nodeId) => void handleStop(nodeId)}
          onClose={() => setViewingNode(null)}
        />
      )}

      {launch && (
        <WorkNodeLaunchDialog
          key={launch.key}
          prefill={launch.prefill}
          repos={repos}
          onClose={() => setLaunch(null)}
          onLaunch={(request) => void sendLaunch(request)}
        />
      )}
    </main>
  );
}
