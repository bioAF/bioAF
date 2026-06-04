export interface ClusterMachineOption {
  value: string;
  label: string;
  description: string;
  recommended?: boolean;
}

// e2 entries land before n2 equivalents so the dropdown surfaces the
// high-availability family first. e2-standard-8 is marked recommended because
// the n2 family has repeatedly stocked out in us-central1-a for interactive
// pool scale-ups; see local/gke-capacity/gke-capacity-issue.md.
export const INTERACTIVE_MACHINE_OPTIONS: ClusterMachineOption[] = [
  {
    value: "e2-standard-4",
    label: "4 vCPU / 16 GB RAM",
    description: "Light analysis (high availability)",
  },
  {
    value: "e2-standard-8",
    label: "8 vCPU / 32 GB RAM",
    description: "General-purpose analysis (high availability)",
    recommended: true,
  },
  {
    value: "e2-highmem-8",
    label: "8 vCPU / 64 GB RAM",
    description: "Large datasets (high availability)",
  },
  {
    value: "n2-standard-4",
    label: "4 vCPU / 16 GB RAM",
    description: "Light analysis",
  },
  {
    value: "n2-standard-8",
    label: "8 vCPU / 32 GB RAM",
    description: "General-purpose analysis",
  },
  {
    value: "n2-highmem-8",
    label: "8 vCPU / 64 GB RAM",
    description: "Large datasets",
  },
  {
    value: "n2-highmem-16",
    label: "16 vCPU / 128 GB RAM",
    description: "Very large datasets",
  },
  {
    value: "n2-standard-32",
    label: "32 vCPU / 128 GB RAM",
    description: "Compute-intensive analysis",
  },
  {
    value: "n2-highmem-32",
    label: "32 vCPU / 256 GB RAM",
    description: "Extreme memory workloads",
  },
];

export const PIPELINE_MACHINE_OPTIONS: ClusterMachineOption[] = [
  {
    value: "n2-highmem-8",
    label: "8 vCPU / 64 GB RAM",
    description: "Small pipelines",
  },
  {
    value: "n2-highmem-16",
    label: "16 vCPU / 128 GB RAM",
    description: "Standard pipelines",
    recommended: true,
  },
  {
    value: "n2-highmem-32",
    label: "32 vCPU / 256 GB RAM",
    description: "Large or multi-sample pipelines",
  },
];

export function formatMachineOptionLabel(opt: ClusterMachineOption): string {
  const base = `${opt.value} - ${opt.label} - ${opt.description}`;
  return opt.recommended ? `${base} (recommended)` : base;
}
