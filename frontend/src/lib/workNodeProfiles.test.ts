import { resolveWorkNodeProfiles, WORK_NODE_PROFILE_ORDER } from "./workNodeProfiles";
import type { MachineType } from "./types";

const fullCatalog: MachineType[] = [
  { name: "e2-standard-4", category: "standard", cpu: 4, memory_gb: 16, gpu: null, description: "Light analysis" },
  { name: "e2-standard-8", category: "standard", cpu: 8, memory_gb: 32, gpu: null, description: "General" },
  { name: "n2-standard-4", category: "standard", cpu: 4, memory_gb: 16, gpu: null, description: "Light analysis" },
  { name: "n2-standard-8", category: "standard", cpu: 8, memory_gb: 32, gpu: null, description: "General" },
  { name: "e2-highmem-8", category: "high-memory", cpu: 8, memory_gb: 64, gpu: null, description: "Larger datasets" },
  { name: "n2-highmem-8", category: "high-memory", cpu: 8, memory_gb: 64, gpu: null, description: "Larger datasets" },
  { name: "n2-highmem-16", category: "high-memory", cpu: 16, memory_gb: 128, gpu: null, description: "Multi-sample" },
  { name: "n2-highmem-32", category: "high-memory", cpu: 32, memory_gb: 256, gpu: null, description: "Extreme" },
  { name: "n1-standard-8-nvidia-tesla-t4", category: "gpu", cpu: 8, memory_gb: 30, gpu: "T4", description: "Entry GPU" },
  { name: "n1-standard-16-nvidia-tesla-v100", category: "gpu", cpu: 16, memory_gb: 60, gpu: "V100", description: "Heavy GPU" },
];

describe("resolveWorkNodeProfiles", () => {
  it("returns 7 profiles in fixed order", () => {
    const profiles = resolveWorkNodeProfiles(fullCatalog);
    expect(profiles).toHaveLength(7);
    expect(profiles.map((p) => p.id)).toEqual(WORK_NODE_PROFILE_ORDER);
  });

  it("maps each CPU tier to a matching machine type from the catalog", () => {
    const profiles = resolveWorkNodeProfiles(fullCatalog);
    const byId = Object.fromEntries(profiles.map((p) => [p.id, p]));
    expect(byId.small.machineType?.name).toBe("e2-standard-4");
    expect(byId.medium.machineType?.name).toBe("e2-standard-8");
    expect(byId.large.machineType?.name).toBe("e2-highmem-8");
    expect(byId.xlarge.machineType?.name).toBe("n2-highmem-16");
    expect(byId["2xlarge"].machineType?.name).toBe("n2-highmem-32");
  });

  it("GPU profile resolves to the smallest (cheapest) GPU machine type", () => {
    const profiles = resolveWorkNodeProfiles(fullCatalog);
    const gpu = profiles.find((p) => p.id === "gpu");
    expect(gpu?.machineType?.name).toBe("n1-standard-8-nvidia-tesla-t4");
  });

  it("HMEM profile resolves to the largest high-memory machine type", () => {
    const profiles = resolveWorkNodeProfiles(fullCatalog);
    const hmem = profiles.find((p) => p.id === "hmem");
    expect(hmem?.machineType?.name).toBe("n2-highmem-32");
  });

  it("if catalog has no GPU, GPU profile is unavailable (machineType=null)", () => {
    const noGpu = fullCatalog.filter((m) => m.category !== "gpu");
    const profiles = resolveWorkNodeProfiles(noGpu);
    const gpu = profiles.find((p) => p.id === "gpu");
    expect(gpu?.machineType).toBeNull();
  });

  it("if catalog has no high-memory, HMEM profile is unavailable", () => {
    const noHmem = fullCatalog.filter((m) => m.category !== "high-memory");
    const profiles = resolveWorkNodeProfiles(noHmem);
    const hmem = profiles.find((p) => p.id === "hmem");
    expect(hmem?.machineType).toBeNull();
  });

  it("CPU tiers without exact cpu/memory match fall back to null", () => {
    const sparse: MachineType[] = [
      { name: "e2-standard-4", category: "standard", cpu: 4, memory_gb: 16, gpu: null, description: "Light" },
    ];
    const profiles = resolveWorkNodeProfiles(sparse);
    const byId = Object.fromEntries(profiles.map((p) => [p.id, p]));
    expect(byId.small.machineType?.name).toBe("e2-standard-4");
    expect(byId.medium.machineType).toBeNull();
    expect(byId.large.machineType).toBeNull();
  });
});
