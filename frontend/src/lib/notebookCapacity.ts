import { RESOURCE_PROFILES, ResourceProfile } from "@/lib/types";

// Friendly tier labels. Must match PROFILE_META in src/app/notebooks/page.tsx.
export const PROFILE_LABELS: Record<ResourceProfile, string> = {
  small: "Small",
  medium: "Medium",
  large: "Large",
  xlarge: "X Large",
  "2xlarge": "XX Large",
};

const PROFILE_ORDER: ResourceProfile[] = ["small", "medium", "large", "xlarge", "2xlarge"];

// Lowercase form for use in support sentences. Multi-word tiers keep their
// proper case (e.g. "X Large") because "x large" reads awkwardly.
const PROFILE_LOWER: Record<ResourceProfile, string> = {
  small: "small",
  medium: "medium",
  large: "large",
  xlarge: "X Large",
  "2xlarge": "XX Large",
};

// Memory-per-vCPU ratio (GB) for standard GCE machine families. Used to size
// machine types that are not in the curated capacity table below. Mirrors the
// backend `_FAMILY_MEM_PER_VCPU` in app/services/machine_types.py.
const FAMILY_MEM_PER_VCPU: Record<string, number> = {
  standard: 4,
  highmem: 8,
  highcpu: 1,
};

const KNOWN_CAPACITY: Record<string, { cpu: number; memory: number }> = {
  "e2-standard-4": { cpu: 4, memory: 16 },
  "e2-standard-8": { cpu: 8, memory: 32 },
  "n2-standard-4": { cpu: 4, memory: 16 },
  "n2-standard-8": { cpu: 8, memory: 32 },
  "e2-highmem-8": { cpu: 8, memory: 64 },
  "n2-highmem-8": { cpu: 8, memory: 64 },
  "n2-highmem-16": { cpu: 16, memory: 128 },
  "n2-highmem-32": { cpu: 32, memory: 256 },
};

export function machineTypeCapacity(name: string): { cpu: number; memory: number } | null {
  const hit = KNOWN_CAPACITY[name];
  if (hit) return hit;
  const parts = (name || "").split("-");
  if (parts.length < 3) return null;
  const vcpuStr = parts[parts.length - 1];
  const cls = parts[parts.length - 2];
  const vcpu = Number(vcpuStr);
  const perVcpu = FAMILY_MEM_PER_VCPU[cls];
  if (!perVcpu || !Number.isFinite(vcpu) || vcpu <= 0) return null;
  return { cpu: vcpu, memory: vcpu * perVcpu };
}

export interface NotebookSupport {
  supported: ResourceProfile[];
  topLabel: string | null;
  supportSentence: string;
}

export function notebookSupportForMachine(machineType: string): NotebookSupport {
  const cap = machineTypeCapacity(machineType);
  if (!cap) return { supported: [], topLabel: null, supportSentence: "" };

  // Notebook pods run with requests == limits, so a tier only schedules when
  // it is strictly smaller than a single pool node (GKE reserves CPU and
  // memory per node). Matches the backend rule.
  const supported = PROFILE_ORDER.filter((p) => {
    const specs = RESOURCE_PROFILES[p];
    return specs.cpu < cap.cpu && specs.memory < cap.memory;
  });

  if (supported.length === 0) {
    return { supported, topLabel: null, supportSentence: "" };
  }
  if (supported.length === PROFILE_ORDER.length) {
    return {
      supported,
      topLabel: PROFILE_LABELS[supported[supported.length - 1]],
      supportSentence: "Supports all notebook sizes",
    };
  }

  const names = supported.map((p) => PROFILE_LOWER[p]);
  let sentence: string;
  if (names.length === 1) {
    sentence = `Supports only ${names[0]} notebooks`;
  } else if (names.length === 2) {
    sentence = `Supports ${names[0]} and ${names[1]} notebooks`;
  } else {
    const head = names.slice(0, -1).join(", ");
    sentence = `Supports ${head}, and ${names[names.length - 1]} notebooks`;
  }

  return {
    supported,
    topLabel: PROFILE_LABELS[supported[supported.length - 1]],
    supportSentence: sentence,
  };
}
