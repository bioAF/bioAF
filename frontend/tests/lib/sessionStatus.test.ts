import { formatSessionStatusLabel, formatLinkedTo } from "@/lib/sessionStatus";

describe("formatSessionStatusLabel", () => {
  it("falls back to the raw status for non-failure states", () => {
    expect(formatSessionStatusLabel({ status: "running", failure_reason: null })).toBe("running");
    expect(formatSessionStatusLabel({ status: "starting", failure_reason: null })).toBe("starting");
    expect(formatSessionStatusLabel({ status: "stopped", failure_reason: null })).toBe("stopped");
  });

  it("returns 'Resource Failure' for failed sessions with failure_reason=resource_exhausted", () => {
    expect(
      formatSessionStatusLabel({ status: "failed", failure_reason: "resource_exhausted" }),
    ).toBe("Resource Failure");
  });

  it("returns 'Image Pull Failed' for failed sessions with image_pull_failed", () => {
    expect(formatSessionStatusLabel({ status: "failed", failure_reason: "image_pull_failed" })).toBe(
      "Image Pull Failed",
    );
  });

  it("returns 'Out of Memory' for failed sessions with oom_killed", () => {
    expect(formatSessionStatusLabel({ status: "failed", failure_reason: "oom_killed" })).toBe(
      "Out of Memory",
    );
  });

  it("returns 'Quota Exceeded' for failed sessions with quota_exceeded", () => {
    expect(formatSessionStatusLabel({ status: "failed", failure_reason: "quota_exceeded" })).toBe(
      "Quota Exceeded",
    );
  });

  it("returns 'Failed' for failed sessions when failure_reason is null or unknown", () => {
    expect(formatSessionStatusLabel({ status: "failed", failure_reason: null })).toBe("Failed");
    expect(formatSessionStatusLabel({ status: "failed", failure_reason: "unknown" })).toBe("Failed");
  });
});

describe("formatLinkedTo", () => {
  it("returns 'Experiment: <name>' when experiment is set", () => {
    expect(
      formatLinkedTo({
        experiment: { id: 1, name: "EXP-001" },
        project: null,
      }),
    ).toBe("Experiment: EXP-001");
  });

  it("returns 'Project: <name>' when project is set", () => {
    expect(
      formatLinkedTo({
        experiment: null,
        project: { id: 2, name: "Project Alpha" },
      }),
    ).toBe("Project: Project Alpha");
  });

  it("prefers Experiment over Project when both are set (a session can only be tied to one)", () => {
    expect(
      formatLinkedTo({
        experiment: { id: 1, name: "EXP-001" },
        project: { id: 2, name: "Project Alpha" },
      }),
    ).toBe("Experiment: EXP-001");
  });

  it("returns null when neither is set so callers can render a placeholder", () => {
    expect(formatLinkedTo({ experiment: null, project: null })).toBeNull();
  });

  it("tolerates undefined inputs (e.g. legacy responses without `project` field)", () => {
    expect(formatLinkedTo({})).toBeNull();
  });
});
