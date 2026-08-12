import type { ParameterSchema, SampleBrief } from "@/lib/types";
import { parameterGroups } from "./ParameterForm";

/** 10x Chromium chemistry as recorded on a sample, mapped to the `protocol`
 *  value the 10x-aware aligners expect. */
const CHEMISTRY_TO_PROTOCOL: Record<string, string> = {
  "v1": "10XV1",
  "v2": "10XV2",
  "v3": "10XV3",
  "v3.1": "10XV3",
  "nextgem v3.1": "10XV3",
  "nextgem v3": "10XV3",
  "10x chromium 3' v1": "10XV1",
  "10x chromium 3' v2": "10XV2",
  "10x chromium 3' v3": "10XV3",
  "10x chromium 3' v3.1": "10XV3",
  "10x chromium 5' v1": "10XV1",
  "10x chromium 5' v2": "10XV2",
  "10x chromium 5' v3": "10XV3",
};

/** The 10x protocol shared by every selected sample, or null.
 *
 *  Disagreement yields null rather than a guess: a run whose samples were
 *  prepared with different chemistries has no single right answer. */
export function detectProtocol(samples: SampleBrief[]): string | null {
  const versions = new Set(
    samples.map((s) => s.chemistry_version?.trim().toLowerCase()).filter(Boolean) as string[],
  );
  if (versions.size !== 1) return null;
  return CHEMISTRY_TO_PROTOCOL[[...versions][0]] || null;
}

/** Whether this pipeline would actually accept `protocol = value`.
 *
 *  The detected value used to be written into the launch parameters for every
 *  pipeline. Sarek received a parameter it does not have, and nf-core/nanoseq
 *  received `10XV3` in a real parameter of the same name whose meaning is the
 *  input sample type ('DNA' | 'cDNA' | 'directRNA').
 *
 *  So the question is asked of the pipeline's own schema, never of its name:
 *  does it declare `protocol`, and does it list this value? A `protocol`
 *  parameter with no enum is treated as NOT accepting it, because a free-text
 *  parameter that merely shares the name is exactly how the nanoseq case
 *  arose. */
export function pipelineAcceptsProtocol(schema: ParameterSchema | null, value: string | null): boolean {
  if (!schema || !value) return false;

  for (const [, group] of parameterGroups(schema)) {
    const prop = group.properties?.protocol;
    if (prop) return Array.isArray(prop.enum) && prop.enum.includes(value);
  }
  return false;
}
