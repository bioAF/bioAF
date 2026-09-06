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

// ---- plan_7 step 4: the deposit route ----

describe("the deposit route", () => {
  it("names the acquisition step for a person, not for the state machine", () => {
    const stage = getValidationStage("acquiring_processed");
    expect(stage.label).toBe("Fetching deposited data");
    expect(stage.kind).toBe("in_progress");
  });

  it("names the inspection step", () => {
    const stage = getValidationStage("inspecting_deposit");
    expect(stage.label).toBe("Checking deposited data");
    expect(stage.kind).toBe("in_progress");
  });

  it("gives the deposit states a position on their own path", () => {
    // A deposit-route study is not 5 of 9 steps through the pipeline path; it is on a shorter one.
    // Reporting it against the pipeline's total would tell a scientist it has further to go than it
    // does, and the two routes genuinely have different lengths.
    const stage = getValidationStage("acquiring_processed");
    expect(stage.step).not.toBeNull();
    expect(stage.totalSteps).toBeLessThan(getValidationStage("acquiring_data").totalSteps);
  });

  it("leaves the pipeline path untouched", () => {
    // The regression guard: every pipeline stage keeps its label, its position and its total.
    const acquiring = getValidationStage("acquiring_data");
    expect(acquiring.label).toBe("Fetching data");
    expect(acquiring.step).toBe(5);
    expect(acquiring.totalSteps).toBe(9);
    expect(getValidationStage("comparing").label).toBe("Awaiting review");
    expect(getValidationStage("plan_ready").kind).toBe("awaiting_review");
  });
});
