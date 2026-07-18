import { getValidationStatus } from "@/lib/validationStatus";

describe("getValidationStatus", () => {
  it("maps 100% to Fully Validated with no human review", () => {
    const s = getValidationStatus(100);
    expect(s.key).toBe("fully_validated");
    expect(s.label).toBe("Fully Validated");
    expect(s.needsHumanReview).toBe(false);
    expect(s.tone).toBe("positive");
  });

  it("maps the 75-99 band to Likely Validated (needs review); 75 and 99.9 both land here", () => {
    for (const c of [75, 80, 99, 99.9]) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("likely_validated");
      expect(s.label).toBe("Likely Validated");
      expect(s.needsHumanReview).toBe(true);
    }
  });

  it("maps the 55-<75 band to Possibly Validated (needs review)", () => {
    for (const c of [55, 60, 74.9] as const) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("possibly_validated");
      expect(s.label).toBe("Possibly Validated");
      expect(s.needsHumanReview).toBe(true);
    }
  });

  it("maps the 25-<55 band to Questionable (needs review); the 54-55 gap resolves here", () => {
    for (const c of [25, 40, 54, 54.9] as const) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("questionable");
      expect(s.label).toBe("Questionable");
      expect(s.needsHumanReview).toBe(true);
    }
  });

  it("maps the 5-<25 band to Unlikely (no review)", () => {
    for (const c of [5, 15, 24.9] as const) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("unlikely");
      expect(s.label).toBe("Unlikely");
      expect(s.needsHumanReview).toBe(false);
      expect(s.tone).toBe("negative");
    }
  });

  it("maps the 0-<5 band to Very Unlikely (no review)", () => {
    for (const c of [0, 2, 4.9] as const) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("very_unlikely");
      expect(s.label).toBe("Very Unlikely");
      expect(s.needsHumanReview).toBe(false);
    }
  });

  it("treats a null/undefined confidence as Could Not Reproduce (validation could not run)", () => {
    for (const c of [null, undefined]) {
      const s = getValidationStatus(c);
      expect(s.key).toBe("could_not_reproduce");
      expect(s.label).toBe("Could Not Reproduce");
      expect(s.needsHumanReview).toBe(false);
      expect(s.tone).toBe("neutral");
    }
  });

  it("clamps out-of-range confidence into [0,100]", () => {
    expect(getValidationStatus(150).key).toBe("fully_validated");
    expect(getValidationStatus(-10).key).toBe("very_unlikely");
  });

  it("treats NaN as Could Not Reproduce rather than a silent band", () => {
    expect(getValidationStatus(Number.NaN).key).toBe("could_not_reproduce");
  });

  it("every status carries a human-readable description", () => {
    for (const c of [100, 90, 60, 40, 10, 1, null]) {
      expect(getValidationStatus(c).description.length).toBeGreaterThan(0);
    }
  });
});
