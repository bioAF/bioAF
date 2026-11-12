"use client";

import type React from "react";
import { versionChangeKind } from "@/lib/customPipelineVersions";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";
import { formatRecordedTime } from "@/lib/recordedTime";
import type { CustomPipelineDetail, GitHubRepo } from "@/lib/types";
import type { EnvVersionOption } from "./versionDraft";

/** A pipeline's versions, newest first, each expandable to its full definition. */
export function VersionHistory({
  pipeline,
  envOptionsById,
  repoById,
  expandedVersionIds,
  toggleVersion,
  canEdit,
  onDeprecate,
}: {
  pipeline: CustomPipelineDetail;
  envOptionsById: Map<number, EnvVersionOption>;
  repoById: Map<number, GitHubRepo>;
  expandedVersionIds: Set<number>;
  toggleVersion: (versionId: number) => void;
  canEdit: boolean;
  onDeprecate: (versionId: number) => void;
}) {
  return (
    <div className="divide-y">
      {pipeline.versions.map((version, idx) => {
        const previous = pipeline.versions[idx + 1] ?? null;
        const changeKind = versionChangeKind(version, previous);
        const expanded = expandedVersionIds.has(version.id);
        const env = envOptionsById.get(version.environment_version_id);
        const repo =
          version.github_repo_id != null
            ? repoById.get(version.github_repo_id)
            : null;
        return (
          <div key={version.id} className="p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="font-mono font-semibold">
                  v{version.version_number}
                </span>
                <span
                  className={`px-2 py-0.5 text-xs rounded-full ${statusBadgeClass("pipelineVersionChange", changeKind)}`}
                >
                  {statusLabel("pipelineVersionChange", changeKind)}
                </span>
                <span
                  className={`px-2 py-0.5 text-xs rounded-full ${
                    version.status === "active"
                      ? "bg-green-100 text-green-700"
                      : "bg-gray-200 text-gray-600"
                  }`}
                >
                  {version.status}
                </span>
                <span className="text-xs text-gray-500">
                  {formatRecordedTime(version.created_at)}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => toggleVersion(version.id)}
                  className="text-sm text-bioaf-600 hover:underline"
                >
                  {expanded ? "Hide Details" : "Show Details"}
                </button>
                {canEdit && version.status === "active" && (
                  <button
                    onClick={() => onDeprecate(version.id)}
                    className="text-sm text-gray-500 hover:text-red-600 hover:underline"
                  >
                    Deprecate
                  </button>
                )}
              </div>
            </div>

            {expanded && (
              <div className="mt-4 pl-2 border-l-2 border-gray-200 space-y-3 text-sm">
                <DetailRow label="Code source">
                  {version.code_source_type === "github_repo" ? (
                    repo ? (
                      <span className="font-mono text-gray-700">
                        {repo.display_name} ({repo.git_ssh_url})
                      </span>
                    ) : (
                      <span className="font-mono text-gray-500">
                        GitHub repo #{version.github_repo_id}
                      </span>
                    )
                  ) : version.code_source_type === "code_blob" ? (
                    <span>Code blob</span>
                  ) : (
                    <span>Inline command</span>
                  )}
                </DetailRow>
                <DetailRow label="Entrypoint">
                  <code className="font-mono bg-gray-100 px-2 py-0.5 rounded">
                    {version.entrypoint_command}
                  </code>
                </DetailRow>
                {(version.code_source_type === "code_blob" ||
                  version.code_source_type === "inline") &&
                  version.code_content && (
                    <div>
                      <div className="text-xs text-gray-500 mb-1">Code content</div>
                      <pre className="bg-gray-50 border rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap max-h-72">
                        {version.code_content}
                      </pre>
                    </div>
                  )}
                <DetailRow label="Pipeline Template">
                  {env ? (
                    <span>
                      {env.env_name} v{env.version_number}{" "}
                      <span className="text-xs text-gray-500">
                        ({env.status})
                      </span>
                    </span>
                  ) : (
                    <span className="text-gray-500">
                      Environment version #{version.environment_version_id}
                    </span>
                  )}
                </DetailRow>
                <DetailRow label="Resources">
                  <span className="font-mono">
                    CPU {version.cpu_request} / Memory {version.memory_request}
                  </span>
                </DetailRow>
                <DetailRow label="Log file">
                  {version.log_file_path ? (
                    <code className="font-mono">{version.log_file_path}</code>
                  ) : (
                    <span className="text-gray-500">Default (terminal output)</span>
                  )}
                </DetailRow>
                <div>
                  <div className="text-xs text-gray-500 mb-1">Variables</div>
                  {version.variables.length === 0 ? (
                    <p className="text-gray-500 text-xs">No variables.</p>
                  ) : (
                    <table className="w-full text-xs border">
                      <thead className="bg-gray-100 text-gray-600 uppercase">
                        <tr>
                          <th scope="col" className="px-2 py-1 text-left">Name</th>
                          <th scope="col" className="px-2 py-1 text-left">Type</th>
                          <th scope="col" className="px-2 py-1 text-left">Default</th>
                          <th scope="col" className="px-2 py-1 text-left">Required</th>
                        </tr>
                      </thead>
                      <tbody>
                        {version.variables.map((v) => (
                          <tr key={v.id} className="border-t">
                            <td className="px-2 py-1 font-mono">
                              {v.variable_name}
                            </td>
                            <td className="px-2 py-1">{v.variable_type}</td>
                            <td className="px-2 py-1 font-mono">
                              {v.default_value ?? ""}
                            </td>
                            <td className="px-2 py-1">
                              {v.is_required ? "Yes" : "No"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
      {pipeline.versions.length === 0 && (
        <div className="p-12 text-center text-gray-500 text-sm">
          <p className="font-medium text-gray-700">No versions yet.</p>
          {canEdit && (
            <p className="mt-1 text-gray-500">
              A version holds the code, entrypoint command, environment, and
              variables. Click <span className="font-semibold">New Version</span>{" "}
              to define the first one and make this pipeline launchable.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 items-start">
      <div className="text-xs text-gray-500 uppercase tracking-wide pt-0.5">{label}</div>
      <div className="text-sm text-gray-700">{children}</div>
    </div>
  );
}
