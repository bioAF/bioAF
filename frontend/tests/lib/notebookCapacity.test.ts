import { notebookSupportForMachine } from "@/lib/notebookCapacity";

describe("notebookSupportForMachine", () => {
  it("n2-standard-4 supports only small", () => {
    const r = notebookSupportForMachine("n2-standard-4");
    expect(r.supported).toEqual(["small"]);
    expect(r.topLabel).toBe("Small");
    expect(r.supportSentence).toBe("Supports only small notebooks");
  });

  it("n2-standard-8 supports small and medium", () => {
    const r = notebookSupportForMachine("n2-standard-8");
    expect(r.supported).toEqual(["small", "medium"]);
    expect(r.topLabel).toBe("Medium");
    expect(r.supportSentence).toBe("Supports small and medium notebooks");
  });

  it("n2-highmem-8 (same 8-CPU ceiling) also tops out at medium", () => {
    const r = notebookSupportForMachine("n2-highmem-8");
    expect(r.supported).toEqual(["small", "medium"]);
    expect(r.topLabel).toBe("Medium");
  });

  it("n2-highmem-16 supports small, medium, and large", () => {
    const r = notebookSupportForMachine("n2-highmem-16");
    expect(r.supported).toEqual(["small", "medium", "large"]);
    expect(r.topLabel).toBe("Large");
    expect(r.supportSentence).toBe("Supports small, medium, and large notebooks");
  });

  it("n2-highmem-32 unlocks every notebook tier", () => {
    const r = notebookSupportForMachine("n2-highmem-32");
    expect(r.supported).toEqual(["small", "medium", "large", "xlarge", "2xlarge"]);
    expect(r.topLabel).toBe("XX Large");
    expect(r.supportSentence).toBe("Supports all notebook sizes");
  });

  it("returns an empty result for an unparsable machine type", () => {
    const r = notebookSupportForMachine("not-a-machine");
    expect(r.supported).toEqual([]);
    expect(r.topLabel).toBeNull();
    expect(r.supportSentence).toBe("");
  });
});
