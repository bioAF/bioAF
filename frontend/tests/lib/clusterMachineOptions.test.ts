import {
  INTERACTIVE_MACHINE_OPTIONS,
  PIPELINE_MACHINE_OPTIONS,
  formatMachineOptionLabel,
} from "@/lib/clusterMachineOptions";

describe("INTERACTIVE_MACHINE_OPTIONS", () => {
  it("offers e2 (high-availability) variants alongside n2", () => {
    const values = INTERACTIVE_MACHINE_OPTIONS.map((o) => o.value);
    // e2-standard-{4,8} and e2-highmem-8 must be present so users have a
    // route off the deprioritized n2 family when GCE stocks out.
    expect(values).toEqual(expect.arrayContaining(["e2-standard-4", "e2-standard-8", "e2-highmem-8"]));
    // Original n2 variants stay available for users who explicitly want them.
    expect(values).toEqual(expect.arrayContaining(["n2-standard-4", "n2-standard-8", "n2-highmem-8"]));
  });

  it("recommends e2-standard-8 (predictable scheduling, same 8 vCPU / 32 GB as n2-standard-8)", () => {
    const recommended = INTERACTIVE_MACHINE_OPTIONS.filter((o) => o.recommended);
    expect(recommended.map((o) => o.value)).toEqual(["e2-standard-8"]);
  });

  it("groups e2 options before their n2 equivalents (recommended first)", () => {
    const values = INTERACTIVE_MACHINE_OPTIONS.map((o) => o.value);
    expect(values.indexOf("e2-standard-4")).toBeLessThan(values.indexOf("n2-standard-4"));
    expect(values.indexOf("e2-standard-8")).toBeLessThan(values.indexOf("n2-standard-8"));
    expect(values.indexOf("e2-highmem-8")).toBeLessThan(values.indexOf("n2-highmem-8"));
  });
});

describe("PIPELINE_MACHINE_OPTIONS", () => {
  it("keeps the existing n2-highmem variants", () => {
    const values = PIPELINE_MACHINE_OPTIONS.map((o) => o.value);
    expect(values).toEqual(expect.arrayContaining(["n2-highmem-8", "n2-highmem-16", "n2-highmem-32"]));
  });
});

describe("formatMachineOptionLabel", () => {
  it("prefixes the GCE machine type so users can see what they're scheduling on", () => {
    const label = formatMachineOptionLabel({
      value: "n2-standard-8",
      label: "8 vCPU / 32 GB RAM",
      description: "General-purpose analysis",
    });
    expect(label).toBe("n2-standard-8 - 8 vCPU / 32 GB RAM - General-purpose analysis");
  });

  it("marks the recommended option inline", () => {
    const label = formatMachineOptionLabel({
      value: "e2-standard-8",
      label: "8 vCPU / 32 GB RAM",
      description: "General-purpose analysis (high availability)",
      recommended: true,
    });
    expect(label).toBe(
      "e2-standard-8 - 8 vCPU / 32 GB RAM - General-purpose analysis (high availability) (recommended)",
    );
  });
});
