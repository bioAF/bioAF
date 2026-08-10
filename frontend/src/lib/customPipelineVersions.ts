import type { CustomPipelineVersion } from "@/lib/types";

/**
 * Which kind of change produced this version of a Custom Pipeline, as a key into
 * STATUS_STYLES.pipelineVersionChange (which carries the badge colour and the
 * label).
 *
 * This lived twice, as identical copies in the launch dialog and the detail
 * page. Version history is shown on both, so the two had to agree and nothing
 * made them.
 */
export function versionChangeKind(
  current: CustomPipelineVersion,
  previous: CustomPipelineVersion | null,
): "initial" | "image" | "config_and_image" | "config" {
  if (previous == null) return "initial";
  // An environment cascade is an image rebuild the user did not ask for
  // directly, so it is called out separately from a config edit.
  if (current.version_trigger === "environment_cascade") return "image";
  if (current.environment_version_id !== previous.environment_version_id) {
    return "config_and_image";
  }
  return "config";
}
