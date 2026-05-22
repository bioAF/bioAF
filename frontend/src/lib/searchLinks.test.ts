import { searchHitHref, searchHitTypeLabel } from "./searchLinks";

describe("searchHitHref", () => {
  it("links an experiment hit to the experiment", () => {
    expect(searchHitHref({ entity_type: "experiment", entity_id: 5, name: "Exp" })).toBe(
      "/experiments/5",
    );
  });

  it("links a sample hit to its experiment's Samples tab", () => {
    expect(
      searchHitHref({ entity_type: "sample", entity_id: 9, name: "S-1", experiment_id: 5 }),
    ).toBe("/experiments/5?tab=samples");
  });

  it("links a pipeline run hit to the run detail page", () => {
    expect(searchHitHref({ entity_type: "pipeline_run", entity_id: 12, name: "run" })).toBe(
      "/pipelines/runs/12",
    );
  });

  it("links a file hit to the Data & Files page focused on the file", () => {
    expect(searchHitHref({ entity_type: "file", entity_id: 77, name: "f.fastq" })).toBe(
      "/data/files?file=77",
    );
  });
});

describe("searchHitTypeLabel", () => {
  it("gives a friendly label per type", () => {
    expect(searchHitTypeLabel("experiment")).toBe("Experiment");
    expect(searchHitTypeLabel("pipeline_run")).toBe("Run");
    expect(searchHitTypeLabel("file")).toBe("File");
    expect(searchHitTypeLabel("sample")).toBe("Sample");
  });
});
