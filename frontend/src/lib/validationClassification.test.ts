import { VALIDATION_CLASSIFICATIONS, classificationLabel, classificationTone } from "./validationClassification";

test("partially_reproduced is a first-class bucket option", () => {
  const opt = VALIDATION_CLASSIFICATIONS.find((c) => c.value === "partially_reproduced");
  expect(opt).toBeDefined();
  expect(opt?.label).toBe("Partially reproduced");
});

test("partially_reproduced carries a caution tone (needs a human)", () => {
  expect(classificationTone("partially_reproduced")).toBe("caution");
});

test("known buckets keep their labels", () => {
  expect(classificationLabel("validated")).toBe("Validated");
  expect(classificationLabel("partially_reproduced")).toBe("Partially reproduced");
});
