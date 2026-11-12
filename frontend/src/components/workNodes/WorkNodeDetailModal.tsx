"use client";

import { NOT_SET } from "@/lib/placeholders";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import { formatSessionStatusLabel, formatLinkedTo } from "@/lib/sessionStatus";
import { formatRecordedTime } from "@/lib/recordedTime";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { GitHubRepo, WorkNode } from "@/lib/types";
import { formatUptime, sshCommand, workNodeStatusLabel } from "./workNodeDisplay";

/** One work node's details, from the page's current list. It stays a dialog, not a route. */
export function WorkNodeDetailModal({
  node,
  repos,
  canStop,
  onStop,
  onClose,
}: {
  node: WorkNode;
  repos: GitHubRepo[];
  canStop: boolean;
  onStop: (nodeId: number) => void;
  onClose: () => void;
}) {
  return (
  <Modal
    open
    title="Work Node Details"
    onClose={() => onClose()}
    size="md"
  >
    <div className="flex justify-between items-center mb-4">
      <button onClick={() => onClose()} className="text-gray-500 hover:text-gray-600 text-xl">&times;</button>
    </div>
    <div className="space-y-3 text-sm">
      <div className="flex justify-between">
        <span className="text-gray-500">Status</span>
        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClass("computeSession", node.status)}`}>
          {workNodeStatusLabel(node)}
        </span>
      </div>
      {node.status === "failed" && node.failure_message && (
        <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
          <div className="font-medium mb-1">
            {formatSessionStatusLabel({ status: node.status, failure_reason: node.failure_reason })}
          </div>
          <div className="font-mono whitespace-pre-wrap break-words">{node.failure_message}</div>
        </div>
      )}
      {node.status === "failed" && !node.failure_message && !node.access_url && (
        <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
          GCP Resources Unavailable -- the VM could not be created. Try again later or choose a different machine type.
        </div>
      )}
      <div className="flex justify-between">
        <span className="text-gray-500">User</span>
        <span>{node.user?.name || node.user?.email || NOT_SET}</span>
      </div>
      {formatLinkedTo({ project: node.project }) && (
        <div className="flex justify-between">
          <span className="text-gray-500">Linked to</span>
          <span>{formatLinkedTo({ project: node.project })}</span>
        </div>
      )}
      <div className="flex justify-between">
        <span className="text-gray-500">Machine Type</span>
        <span>{node.machine_type || "-"}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-gray-500">Resources</span>
        <span>{node.cpu_cores} CPU / {node.memory_gb} GB RAM</span>
      </div>
      {node.requested_disk_gb !== null && node.requested_disk_gb !== undefined && (
        <div className="flex justify-between">
          <span className="text-gray-500">Disk Size</span>
          <span>{node.requested_disk_gb} GB</span>
        </div>
      )}
      {node.gce_instance_name && (
        <div className="flex justify-between">
          <span className="text-gray-500">VM Instance</span>
          <span className="font-mono text-xs">{node.gce_instance_name}</span>
        </div>
      )}
      {node.gce_zone && (
        <div className="flex justify-between">
          <span className="text-gray-500">Zone</span>
          <span>{node.gce_zone}</span>
        </div>
      )}
      {node.access_url && node.status === "running" && (
        <div>
          <span className="text-gray-500 block mb-1">SSH Command</span>
          <div className="bg-gray-900 text-green-400 rounded p-3 font-mono text-xs flex items-center justify-between">
            <code>{sshCommand(node.access_url)}</code>
            <button
              onClick={() => navigator.clipboard.writeText(sshCommand(node.access_url))}
              className="ml-2 text-gray-500 hover:text-white text-xs"
            >
              Copy
            </button>
          </div>
        </div>
      )}
      {node.github_repo_ids && node.github_repo_ids.length > 0 && (
        <div>
          <span className="text-gray-500 block mb-1">Cloned Repos</span>
          <ul className="text-xs text-gray-700 space-y-1">
            {node.github_repo_ids.map((repoId) => {
              const repo = repos.find((r) => r.id === repoId);
              return (
                <li key={repoId} className="font-mono bg-gray-50 px-2 py-1 rounded">
                  ~/repos/{repo?.display_name || `repo-${repoId}`}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {node.input_file_ids && node.input_file_ids.length > 0 && (
        <div className="flex justify-between">
          <span className="text-gray-500">Input Files</span>
          <span>{node.input_file_ids.length} file(s) in /data/</span>
        </div>
      )}
      <div className="flex justify-between">
        <span className="text-gray-500">Started</span>
        <span>{formatRecordedTime(node.started_at, "datetime", { missing: "-" })}</span>
      </div>
      {node.status === "running" && node.started_at && (
        <div className="flex justify-between">
          <span className="text-gray-500">Uptime</span>
          <span>{formatUptime(node.started_at)}</span>
        </div>
      )}
      <div className="flex justify-between">
        <span className="text-gray-500">Last Heartbeat</span>
        <span>{node.heartbeat_at ? formatRecordedTime(node.heartbeat_at, "datetime", { missing: "-" }) : "-"}</span>
      </div>
      {node.stopped_at && (
        <div className="flex justify-between">
          <span className="text-gray-500">Stopped</span>
          <span>{formatRecordedTime(node.stopped_at, "datetime", { missing: "-" })}</span>
        </div>
      )}
    </div>
    {canStop && node.status === "running" && (
      <div className="mt-4 pt-4 border-t">
        <Button variant="danger" className="w-full"
          onClick={() => onStop(node.id)}>
          Stop Work Node
        </Button>
        <p className="text-xs text-gray-500 mt-1 text-center">Files in /outputs/ will be synced. Data in /scratch will be lost.</p>
      </div>
    )}
  </Modal>
  );
}
