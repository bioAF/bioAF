import {
  bulkUpdate,
  newSampleDraft,
  newSamplePayload,
  overviewPayload,
  sampleEditPayload,
  withFieldDefault,
} from "./experimentForms";
import type { ExperimentDetail } from "@/lib/types";

test("a field default with neither value nor requirement is removed", () => {
  const prev = [{ field_name: "organism", default_value: "human", is_required: null }];
  expect(withFieldDefault(prev, "organism", null, null)).toEqual([]);
  expect(withFieldDefault(prev, "organism", "mouse", true)).toEqual([
    { field_name: "organism", default_value: "mouse", is_required: true },
  ]);
  expect(withFieldDefault([], "tissue_type", null, null)).toEqual([]);
  expect(withFieldDefault([], "tissue_type", null, true)).toEqual([
    { field_name: "tissue_type", default_value: null, is_required: true },
  ]);
});

test("overview custom fields are trimmed and blank names dropped", () => {
  const payload = overviewPayload({
    form: { name: "E" },
    fieldDefaults: [],
    customFields: [
      { field_name: " site ", field_value: " A ", is_required: true },
      { field_name: "  ", field_value: "x", is_required: false },
    ],
  });
  expect(payload).toEqual({
    name: "E",
    field_defaults: [],
    custom_fields: [{ field_name: "site", field_value: "A", field_type: "string", is_required: true }],
  });
});

test("a new sample starts from defaults; blank custom fields are left out", () => {
  const experiment = {
    field_defaults: [
      { field_name: "organism", default_value: "human", is_required: null },
      { field_name: "tissue_type", default_value: null, is_required: true },
    ],
    custom_fields: [{ field_name: "site", field_value: null, is_required: false }],
  } as unknown as ExperimentDetail;
  const draft = newSampleDraft(experiment);
  expect(draft.form).toEqual({ organism: "human" });
  expect(draft.customFields).toEqual({ site: "" });
  expect(newSamplePayload({ cell_count: 0 }, draft.customFields)).toEqual({ cell_count: 0, custom_fields: undefined });
  expect(newSamplePayload({}, { site: "A" })).toEqual({ custom_fields: [{ field_name: "site", field_value: "A" }] });
});

test("an edited sample always sends its custom fields, even none", () => {
  expect(sampleEditPayload({ organism: "x" }, { site: " " })).toEqual({ organism: "x", custom_fields: [] });
});

test("a bulk update keeps only filled fields, and nothing filled is no update", () => {
  expect(bulkUpdate({ organism: "", tissue_type: "Liver", assay: null })).toEqual({ tissue_type: "Liver" });
  expect(bulkUpdate({ organism: "" })).toBeNull();
});
