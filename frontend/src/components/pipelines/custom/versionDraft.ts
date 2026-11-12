/**
 * The new-version draft of a custom pipeline and the decisions about it: what it is seeded from,
 * whether it can be sent, and the request it becomes. The form and the page both use these, so a
 * rule (such as what counts as valid QC config) is stated once.
 */
import type {
  CustomPipelineCodeSource,
  CustomPipelineDetail,
  CustomPipelineVariableDefinition,
  CustomPipelineVersionCreateRequest,
} from "@/lib/types";

export interface EnvVersionOption {
  env_id: number;
  env_name: string;
  version_id: number;
  version_number: number;
  status: string;
}

export interface VariableDraft extends CustomPipelineVariableDefinition {
  _key: string;
}

export interface VersionDraft {
  code_source_type: CustomPipelineCodeSource;
  github_repo_id: number | null;
  code_content: string;
  entrypoint_command: string;
  environment_version_id: number | null;
  cpu_request: string;
  memory_request: string;
  log_file_path: string;
  variables: VariableDraft[];
  qc_template: string;
  qc_config_json_text: string;
}

// What one pipeline-head node can schedule for a custom job; the server refuses more than 1.4 CPU / 5Gi.
export const DEFAULT_CPU = "1";
export const DEFAULT_MEMORY = "4Gi";

export const REFERENCE_CATEGORY_OPTIONS = [
  "any",
  "genome",
  "annotation",
  "index",
  "atlas",
  "markers",
  "other",
] as const;

export function emptyVariableDraft(): VariableDraft {
  return {
    _key: Math.random().toString(36).slice(2),
    variable_name: "",
    default_value: "",
    variable_type: "string",
    reference_category: null,
    is_required: false,
  };
}

/**
 * A new version starts as a copy of the latest one. A pipeline with no versions starts empty, on
 * the first ready template version.
 */
export function seedVersionDraft(detail: CustomPipelineDetail, options: EnvVersionOption[]): VersionDraft {
  const latest = detail.versions[0];
  if (!latest) {
    return {
      code_source_type: "code_blob",
      github_repo_id: null,
      code_content: "",
      entrypoint_command: "",
      environment_version_id: options.find((o) => o.status === "ready")?.version_id ?? null,
      cpu_request: DEFAULT_CPU,
      memory_request: DEFAULT_MEMORY,
      log_file_path: "",
      variables: [],
      qc_template: "",
      qc_config_json_text: "",
    };
  }
  return {
    code_source_type: latest.code_source_type,
    github_repo_id: latest.github_repo_id,
    code_content: latest.code_content ?? "",
    entrypoint_command: latest.entrypoint_command,
    environment_version_id: latest.environment_version_id,
    cpu_request: latest.cpu_request || DEFAULT_CPU,
    memory_request: latest.memory_request || DEFAULT_MEMORY,
    log_file_path: latest.log_file_path ?? "",
    variables: latest.variables.map((v) => ({
      _key: `${v.id}`,
      variable_name: v.variable_name,
      default_value: v.default_value,
      variable_type: v.variable_type,
      reference_category: v.reference_category ?? null,
      is_required: v.is_required,
    })),
    qc_template: latest.qc_template ?? "",
    qc_config_json_text: latest.qc_config_json ? JSON.stringify(latest.qc_config_json, null, 2) : "",
  };
}

export type QcConfigReading =
  | { ok: true; value: Record<string, unknown> | null }
  | { ok: false; reason: "not_object" }
  | { ok: false; reason: "invalid"; message: string };

/** Blank text is no config, and JSON `null` is no config; anything else must be a JSON object. */
export function readQcConfig(text: string): QcConfigReading {
  if (!text.trim()) return { ok: true, value: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return { ok: false, reason: "invalid", message: err instanceof Error ? err.message : "parse error" };
  }
  if (parsed === null) return { ok: true, value: null };
  if (typeof parsed !== "object" || Array.isArray(parsed)) return { ok: false, reason: "not_object" };
  return { ok: true, value: parsed as Record<string, unknown> };
}

/** The request for a draft, or the first reason it cannot be sent. */
export function versionRequest(
  draft: VersionDraft,
): { ok: true; body: CustomPipelineVersionCreateRequest } | { ok: false; error: string } {
  if (!draft.entrypoint_command.trim()) return { ok: false, error: "Entrypoint command is required." };
  if (draft.environment_version_id == null) return { ok: false, error: "Select a template version." };
  if (draft.code_source_type === "github_repo" && !draft.github_repo_id) {
    return { ok: false, error: "Select a GitHub repo." };
  }
  if ((draft.code_source_type === "code_blob" || draft.code_source_type === "inline") && !draft.code_content.trim()) {
    return { ok: false, error: "Provide code content for the chosen source type." };
  }
  const seenNames = new Set<string>();
  for (const v of draft.variables) {
    if (!v.variable_name.trim()) return { ok: false, error: "All variables need a name." };
    if (seenNames.has(v.variable_name)) {
      return {
        ok: false,
        error: `Each variable needs its own name, and "${v.variable_name}" is used more than once. Rename or remove one of them.`,
      };
    }
    seenNames.add(v.variable_name);
  }

  const qc = readQcConfig(draft.qc_config_json_text);
  if (!qc.ok) {
    return {
      ok: false,
      error:
        qc.reason === "not_object"
          ? 'QC config JSON must be an object (e.g. { "sections": [...] }).'
          : `QC config JSON is not valid: ${qc.message}`,
    };
  }

  const fromRepo = draft.code_source_type === "github_repo";
  return {
    ok: true,
    body: {
      code_source_type: draft.code_source_type,
      code_content: fromRepo ? null : draft.code_content,
      github_repo_id: fromRepo ? draft.github_repo_id : null,
      entrypoint_command: draft.entrypoint_command,
      environment_version_id: draft.environment_version_id,
      cpu_request: draft.cpu_request || DEFAULT_CPU,
      memory_request: draft.memory_request || DEFAULT_MEMORY,
      log_file_path: draft.log_file_path || null,
      variables: draft.variables.map((v) => ({
        variable_name: v.variable_name,
        default_value: v.default_value || null,
        variable_type: v.variable_type,
        reference_category: v.variable_type === "reference" ? (v.reference_category ?? null) : null,
        is_required: v.is_required,
      })),
      qc_template: draft.qc_template.trim() || null,
      qc_config_json: qc.value,
    },
  };
}
