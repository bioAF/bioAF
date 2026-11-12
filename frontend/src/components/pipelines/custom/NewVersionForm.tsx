"use client";

import type React from "react";
import type { CustomPipelineCodeSource, GitHubRepo } from "@/lib/types";
import { RuntimeContractPanel } from "./RuntimeContractPanel";
import { QcConfigPanel } from "./QcConfigPanel";
import {
  REFERENCE_CATEGORY_OPTIONS,
  type EnvVersionOption,
  type VariableDraft,
  type VersionDraft,
} from "./versionDraft";

/** The new-version fields. The draft and its validation belong to the page (see versionDraft). */
export function NewVersionForm({
  versionForm,
  setVersionForm,
  readyEnvOptions,
  repos,
  addVariable,
  updateVariable,
  removeVariable,
}: {
  versionForm: VersionDraft;
  setVersionForm: React.Dispatch<React.SetStateAction<VersionDraft>>;
  readyEnvOptions: EnvVersionOption[];
  repos: GitHubRepo[];
  addVariable: () => void;
  updateVariable: (idx: number, patch: Partial<VariableDraft>) => void;
  removeVariable: (idx: number) => void;
}) {
  return (
    <div className="space-y-4">
      <RuntimeContractPanel />
      <div>
        <label className="text-sm text-gray-500 block mb-1">Code source</label>
        <div className="flex gap-4 text-sm">
          {(["github_repo", "code_blob", "inline"] as CustomPipelineCodeSource[]).map(
            (src) => (
              <label key={src} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="code_source_type"
                  value={src}
                  checked={versionForm.code_source_type === src}
                  onChange={() =>
                    setVersionForm((prev) => ({ ...prev, code_source_type: src }))
                  }
                />
                {src === "github_repo"
                  ? "GitHub repo"
                  : src === "code_blob"
                    ? "Code blob"
                    : "Inline command"}
              </label>
            ),
          )}
        </div>
      </div>

      {versionForm.code_source_type === "github_repo" && (
        <div>
          <label htmlFor="github-repo" className="text-sm text-gray-500 block mb-1">GitHub repo</label>
          <select id="github-repo"
            value={versionForm.github_repo_id ?? ""}
            onChange={(e) =>
              setVersionForm((prev) => ({
                ...prev,
                github_repo_id: e.target.value ? Number(e.target.value) : null,
              }))
            }
            className="w-full border rounded px-3 py-2 text-sm bg-white"
          >
            <option value="">Select a repo...</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.display_name} ({r.git_ssh_url})
              </option>
            ))}
          </select>
          {repos.length === 0 && (
            <p className="text-xs text-gray-500 mt-1">
              No repos registered. Add one from Workbench &gt; Work Nodes.
            </p>
          )}
        </div>
      )}

      {versionForm.code_source_type === "code_blob" && (
        <div>
          <label htmlFor="code-blob" className="text-sm text-gray-500 block mb-1">Code blob</label>
          <textarea id="code-blob"
            value={versionForm.code_content}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, code_content: e.target.value }))
            }
            rows={10}
            className="w-full border rounded px-3 py-2 text-sm font-mono"
            placeholder={"#!/bin/bash\necho hello"}
          />
        </div>
      )}

      {versionForm.code_source_type === "inline" && (
        <div>
          <label htmlFor="inline-command" className="text-sm text-gray-500 block mb-1">Inline command</label>
          <input id="inline-command"
            value={versionForm.code_content}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, code_content: e.target.value }))
            }
            placeholder="echo hello"
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
      )}

      <div>
        <label htmlFor="entrypoint-command" className="text-sm text-gray-500 block mb-1">Entrypoint command</label>
        <input id="entrypoint-command"
          value={versionForm.entrypoint_command}
          onChange={(e) =>
            setVersionForm((prev) => ({ ...prev, entrypoint_command: e.target.value }))
          }
          placeholder="bash run.sh"
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      </div>

      <div>
        <label htmlFor="environment" className="text-sm text-gray-500 block mb-1">Pipeline Template</label>
        <select id="environment"
          value={versionForm.environment_version_id ?? ""}
          onChange={(e) =>
            setVersionForm((prev) => ({
              ...prev,
              environment_version_id: e.target.value ? Number(e.target.value) : null,
            }))
          }
          className="w-full border rounded px-3 py-2 text-sm bg-white"
        >
          <option value="">Select a template version...</option>
          {readyEnvOptions.map((opt) => (
            <option key={opt.version_id} value={opt.version_id}>
              {opt.env_name} v{opt.version_number}
            </option>
          ))}
        </select>
        {readyEnvOptions.length === 0 && (
          <p className="text-xs text-gray-500 mt-1">
            No ready pipeline environment versions. Build one in Pipelines &gt; Environments.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="cpu" className="text-sm text-gray-500 block mb-1">CPU</label>
          <input id="cpu"
            value={versionForm.cpu_request}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, cpu_request: e.target.value }))
            }
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
        <div>
          <label htmlFor="memory" className="text-sm text-gray-500 block mb-1">Memory</label>
          <input id="memory"
            value={versionForm.memory_request}
            onChange={(e) =>
              setVersionForm((prev) => ({ ...prev, memory_request: e.target.value }))
            }
            className="w-full border rounded px-3 py-2 text-sm font-mono"
          />
        </div>
      </div>

      <div>
        <label htmlFor="log-file-path-optional" className="text-sm text-gray-500 block mb-1">Log file path (optional)</label>
        <input id="log-file-path-optional"
          value={versionForm.log_file_path}
          onChange={(e) =>
            setVersionForm((prev) => ({ ...prev, log_file_path: e.target.value }))
          }
          placeholder="/outputs/my-script.log"
          className="w-full border rounded px-3 py-2 text-sm font-mono"
        />
      </div>

      <QcConfigPanel
        qcTemplate={versionForm.qc_template}
        qcConfigJsonText={versionForm.qc_config_json_text}
        onChangeTemplate={(value) =>
          setVersionForm((prev) => ({ ...prev, qc_template: value }))
        }
        onChangeConfig={(value) =>
          setVersionForm((prev) => ({ ...prev, qc_config_json_text: value }))
        }
      />

      <div>
        <label htmlFor="variables" className="text-sm text-gray-500 block mb-2">Variables</label>
        <div className="space-y-2">
          {versionForm.variables.map((v, idx) => (
            <div
              key={v._key}
              className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,1.2fr)_auto_auto] gap-2 items-center"
            >
              <input id="variables"
                value={v.variable_name}
                onChange={(e) => updateVariable(idx, { variable_name: e.target.value })}
                placeholder="Variable name"
                className="border rounded px-2 py-1.5 text-sm"
              />
              <select aria-label="Variable type"
                value={v.variable_type}
                onChange={(e) =>
                  updateVariable(idx, {
                    variable_type: e.target.value as VariableDraft["variable_type"],
                    // When switching to reference, default to 'any' so the
                    // schema-required field is non-empty; clear it on switch
                    // back to a primitive type.
                    reference_category:
                      e.target.value === "reference"
                        ? (v.reference_category ?? "any")
                        : null,
                  })
                }
                className="border rounded px-2 py-1.5 text-sm bg-white"
              >
                <option value="string">string</option>
                <option value="number">number</option>
                <option value="boolean">boolean</option>
                <option value="reference">reference</option>
              </select>
              {v.variable_type === "reference" ? (
                <select
                  value={v.reference_category ?? "any"}
                  onChange={(e) =>
                    updateVariable(idx, {
                      reference_category: e.target.value as VariableDraft["reference_category"],
                    })
                  }
                  className="border rounded px-2 py-1.5 text-sm bg-white"
                  aria-label="Reference category"
                >
                  {REFERENCE_CATEGORY_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              ) : (
                <input aria-label="Default value"
                  value={v.default_value ?? ""}
                  onChange={(e) => updateVariable(idx, { default_value: e.target.value })}
                  placeholder="Default value"
                  className="border rounded px-2 py-1.5 text-sm"
                />
              )}
              <label className="flex items-center gap-1 text-xs text-gray-600 px-1">
                <input
                  type="checkbox"
                  checked={v.is_required}
                  onChange={(e) => updateVariable(idx, { is_required: e.target.checked })}
                  className="rounded border-gray-300"
                />
                Required
              </label>
              <button
                type="button"
                onClick={() => removeVariable(idx)}
                className="text-red-400 hover:text-red-600 text-sm px-1"
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addVariable}
            className="text-sm text-bioaf-600 hover:underline"
          >
            + Add Variable
          </button>
        </div>
      </div>
    </div>
  );
}
