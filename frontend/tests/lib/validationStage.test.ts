import { getValidationStage, VALIDATION_HAPPY_PATH } from "@/lib/validationStage";

describe("getValidationStage", () => {
  it("places requested at the first happy-path step", () => {
    const s = getValidationStage("requested");
    expect(s.label).toBe("Requested");
    expect(s.kind).toBe("in_progress");
    expect(s.step).toBe(1);
    expect(s.totalSteps).toBe(VALIDATION_HAPPY_PATH.length);
  });

  it("places running mid-pipeline as in_progress with its step index", () => {
    const s = getValidationStage("running");
    expect(s.label).toBe("Running analysis");
    expect(s.kind).toBe("in_progress");
    expect(s.step).toBe(7);
  });

  it("marks the plan_ready gate as awaiting_review (a human approves the plan)", () => {
    const s = getValidationStage("plan_ready");
    expect(s.kind).toBe("awaiting_review");
    expect(s.step).toBe(4);
  });

  it("marks the comparing gate as awaiting_review (a human classifies)", () => {
    const s = getValidationStage("comparing");
    expect(s.kind).toBe("awaiting_review");
    expect(s.step).toBe(VALIDATION_HAPPY_PATH.length);
    expect(s.description.length).toBeGreaterThan(0);
  });

  it("reports plan_declined as an off-path declined terminal (no step)", () => {
    const s = getValidationStage("plan_declined");
    expect(s.kind).toBe("declined");
    expect(s.step).toBeNull();
  });

  it("reports error as an off-path error terminal (no step)", () => {
    const s = getValidationStage("error");
    expect(s.kind).toBe("error");
    expect(s.step).toBeNull();
  });

  it("reports classified with the classified kind (the page renders a badge, not a stage)", () => {
    const s = getValidationStage("classified");
    expect(s.kind).toBe("classified");
    expect(s.step).toBeNull();
  });

  it("is total: an unknown state falls back without throwing and carries no step", () => {
    const s = getValidationStage("some_future_state");
    expect(s.step).toBeNull();
    expect(s.label.length).toBeGreaterThan(0);
  });

  it("every happy-path state has a non-empty label and description", () => {
    for (const step of VALIDATION_HAPPY_PATH) {
      const s = getValidationStage(step.state);
      expect(s.label.length).toBeGreaterThan(0);
      expect(s.description.length).toBeGreaterThan(0);
    }
  });
});
