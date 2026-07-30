import { getValidationStatus } from "./validationStatus";

test("a null confidence with no classification is Could Not Reproduce", () => {
  expect(getValidationStatus(null).key).toBe("could_not_reproduce");
});

test("the confidence bands still resolve highest-first", () => {
  expect(getValidationStatus(100).key).toBe("fully_validated");
  expect(getValidationStatus(60).key).toBe("possibly_validated");
  expect(getValidationStatus(0).key).toBe("very_unlikely");
});

test("the partially_reproduced classification renders a precise 'Partially Reproduced' status", () => {
  // The classification wins over the confidence band: even with a caution-band fallback number, a
  // partially_reproduced study reads as the precise factual label, not a probabilistic hedge.
  const s = getValidationStatus(60, "partially_reproduced");
  expect(s.key).toBe("partially_reproduced");
  expect(s.label).toBe("Partially Reproduced");
  expect(s.tone).toBe("caution");
  expect(s.needsHumanReview).toBe(true);
});

test("a non-partial classification falls through to the confidence band", () => {
  expect(getValidationStatus(100, "validated").key).toBe("fully_validated");
});
