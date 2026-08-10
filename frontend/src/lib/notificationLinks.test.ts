import { notificationHref, NotificationLink } from "./notificationLinks";

function notif(event_type: string, metadata: Record<string, unknown>): NotificationLink {
  return { event_type, metadata_json: metadata };
}

describe("notificationHref", () => {
  it("sends a 'results ready' (pipeline completed) notification to the run's QC dashboard", () => {
    expect(
      notificationHref(notif("pipeline.completed", { entity_type: "pipeline_run", entity_id: 42 })),
    ).toBe("/results/qc-dashboards?run=42");
  });

  it("sends qc.results_ready to the QC dashboard using the run id in metadata", () => {
    expect(
      notificationHref(notif("qc.results_ready", { entity_type: "qc_dashboard", pipeline_run_id: 7 })),
    ).toBe("/results/qc-dashboards?run=7");
  });

  it("sends a failed pipeline notification to that run's detail page", () => {
    expect(
      notificationHref(notif("pipeline.failed", { entity_type: "pipeline_run", entity_id: 9 })),
    ).toBe("/pipelines/runs/9");
  });

  it("sends a pipeline run review to the reviewed run", () => {
    expect(
      notificationHref(notif("pipeline.run_reviewed", { entity_type: "pipeline_run_review", entity_id: 3, pipeline_run_id: 88 })),
    ).toBe("/pipelines/runs/88");
  });

  it("sends an experiment notification to that experiment", () => {
    expect(
      notificationHref(notif("experiment.status_changed", { entity_type: "experiment", entity_id: 12 })),
    ).toBe("/experiments/12");
  });

  it("sends a project notification to that project", () => {
    expect(
      notificationHref(notif("unclaimed.entity", { entity_type: "project", entity_id: 5 })),
    ).toBe("/projects/5");
  });

  it("sends a sample notification to its experiment's Samples tab", () => {
    expect(
      notificationHref(notif("sample.created", { entity_type: "sample", entity_id: 1, experiment_id: 30 })),
    ).toBe("/experiments/30?tab=samples");
  });

  it("sends a file that belongs to an experiment to that experiment's Files tab", () => {
    expect(
      notificationHref(notif("data.uploaded", { entity_type: "file", entity_id: 1, experiment_id: 30 })),
    ).toBe("/experiments/30?tab=files");
  });

  it("sends a standalone uploaded file to the Data & Files page focused on the file", () => {
    expect(
      notificationHref(notif("data.uploaded", { entity_type: "file", entity_id: 77 })),
    ).toBe("/data/files?file=77");
  });

  it("sends a cataloged ingest event to the resulting file on the Data & Files page", () => {
    expect(
      notificationHref(notif("files.cataloged", { entity_type: "ingest_event", entity_id: 4, file_id: 200 })),
    ).toBe("/data/files?file=200");
  });

  it("sends a reference dataset notification to that dataset", () => {
    expect(
      notificationHref(notif("reference.deprecated", { entity_type: "reference_dataset", entity_id: 6 })),
    ).toBe("/data/references/6");
  });

  it("maps entity types whose page is a single destination", () => {
    expect(notificationHref(notif("component.health_down", { entity_type: "component", entity_id: 1 }))).toBe(
      "/infrastructure/components",
    );
    expect(notificationHref(notif("backup.failure", { entity_type: "backup", entity_id: 1 }))).toBe(
      "/infrastructure/backup",
    );
    expect(notificationHref(notif("work_node.launched", { entity_type: "work_node", entity_id: 1 }))).toBe(
      "/workbench/work-nodes",
    );
    expect(notificationHref(notif("session.idle", { entity_type: "notebook_session", entity_id: 1 }))).toBe(
      "/notebooks",
    );
    expect(
      notificationHref(notif("literature.review_run_completed", { entity_type: "literature_review_run", entity_id: 1 })),
    ).toBe("/lab-knowledge/literature");
  });

  it("returns null when there is no usable destination", () => {
    expect(notificationHref(notif("budget.threshold_80", {}))).toBeNull();
    expect(notificationHref(notif("data.uploaded", { entity_type: "file" }))).toBeNull();
    expect(notificationHref(notif("something.unknown", { entity_type: "mystery", entity_id: 1 }))).toBeNull();
  });

  it("tolerates a missing or null metadata bag", () => {
    expect(notificationHref({ event_type: "x" })).toBeNull();
    expect(notificationHref({ event_type: "x", metadata_json: null })).toBeNull();
  });
});
