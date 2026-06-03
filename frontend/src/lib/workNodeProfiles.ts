import type { MachineType } from "./types";

export type WorkNodeProfileId =
  | "small"
  | "medium"
  | "large"
  | "xlarge"
  | "2xlarge"
  | "gpu"
  | "hmem";

export const WORK_NODE_PROFILE_ORDER: WorkNodeProfileId[] = [
  "small",
  "medium",
  "large",
  "xlarge",
  "2xlarge",
  "gpu",
  "hmem",
];

export interface WorkNodeProfile {
  id: WorkNodeProfileId;
  label: string;
  description: string;
  machineType: MachineType | null;
}

interface ExactSpec {
  id: WorkNodeProfileId;
  label: string;
  description: string;
  cpu: number;
  memory_gb: number;
  category: string;
}

const CPU_TIERS: ExactSpec[] = [
  {
    id: "small",
    label: "Small",
    description: "Exploratory work and light data wrangling",
    cpu: 4,
    memory_gb: 16,
    category: "standard",
  },
  {
    id: "medium",
    label: "Medium",
    description: "General-purpose analysis",
    cpu: 8,
    memory_gb: 32,
    category: "standard",
  },
  {
    id: "large",
    label: "Large",
    description: "Larger datasets, more RAM headroom",
    cpu: 8,
    memory_gb: 64,
    category: "high-memory",
  },
  {
    id: "xlarge",
    label: "X Large",
    description: "Single-cell integration, multi-sample workflows",
    cpu: 16,
    memory_gb: 128,
    category: "high-memory",
  },
  {
    id: "2xlarge",
    label: "XX Large",
    description: "Very large or multi-sample integration",
    cpu: 32,
    memory_gb: 256,
    category: "high-memory",
  },
];

function findExact(machineTypes: MachineType[], spec: ExactSpec): MachineType | null {
  return (
    machineTypes.find(
      (m) =>
        m.category === spec.category && m.cpu === spec.cpu && m.memory_gb === spec.memory_gb
    ) ?? null
  );
}

function smallestInCategory(machineTypes: MachineType[], category: string): MachineType | null {
  const items = machineTypes.filter((m) => m.category === category);
  if (items.length === 0) return null;
  return items.reduce((min, m) => (m.cpu * m.memory_gb < min.cpu * min.memory_gb ? m : min));
}

function largestInCategory(machineTypes: MachineType[], category: string): MachineType | null {
  const items = machineTypes.filter((m) => m.category === category);
  if (items.length === 0) return null;
  return items.reduce((max, m) => (m.cpu * m.memory_gb > max.cpu * max.memory_gb ? m : max));
}

export function resolveWorkNodeProfiles(machineTypes: MachineType[]): WorkNodeProfile[] {
  const tiers: WorkNodeProfile[] = CPU_TIERS.map((spec) => ({
    id: spec.id,
    label: spec.label,
    description: spec.description,
    machineType: findExact(machineTypes, spec),
  }));
  const gpu: WorkNodeProfile = {
    id: "gpu",
    label: "GPU (default)",
    description: "scVI, rapids-singlecell, deep learning",
    machineType: smallestInCategory(machineTypes, "gpu"),
  };
  const hmem: WorkNodeProfile = {
    id: "hmem",
    label: "High memory (default)",
    description: "Maximum RAM, extreme memory workloads",
    machineType: largestInCategory(machineTypes, "high-memory"),
  };
  return [...tiers, gpu, hmem];
}
