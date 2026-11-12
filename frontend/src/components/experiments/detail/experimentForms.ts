/**
 * The experiment detail page's form decisions: what a draft starts from and what is sent. Kept
 * apart from the views so a component and its controller cannot hold two copies of a rule.
 */
import type {
  ExperimentDetail,
  ExperimentUpdateRequest,
  FieldDefaultValue,
  Sample,
  SampleCreateRequest,
  SampleUpdateRequest,
} from "@/lib/types";

export const DEFAULTABLE_FIELDS = [
  { name: "organism", label: "Organism", type: "text" as const },
  { name: "tissue_type", label: "Tissue Type", type: "text" as const },
  { name: "donor_source", label: "Donor ID", type: "text" as const },
  { name: "treatment_condition", label: "Treatment Condition", type: "text" as const },
  { name: "chemistry_version", label: "Chemistry Version", type: "text" as const },
  { name: "sample_batch_code", label: "Sample Batch", type: "text" as const },
  { name: "sequencing_batch_code", label: "Sequencing Batch", type: "text" as const },
  { name: "molecule_type", label: "Molecule Type", type: "vocabulary" as const },
  { name: "library_prep_method", label: "Library Prep Method", type: "vocabulary" as const },
  { name: "library_layout", label: "Library Layout", type: "vocabulary" as const },
  { name: "assay", label: "Assay", type: "assay" as const },
];

export interface EditableCustomField {
  field_name: string;
  field_value: string;
  is_required: boolean;
}

export interface OverviewDraft {
  form: ExperimentUpdateRequest;
  fieldDefaults: FieldDefaultValue[];
  customFields: EditableCustomField[];
}

export function overviewDraftFrom(experiment: ExperimentDetail): OverviewDraft {
  return {
    form: {
      name: experiment.name,
      hypothesis: experiment.hypothesis,
      description: experiment.description,
      start_date: experiment.start_date,
      expected_sample_count: experiment.expected_sample_count,
      design_type: experiment.design_type,
      naming_profile_id: experiment.naming_profile_id,
    },
    fieldDefaults: experiment.field_defaults.map((fd) => ({
      field_name: fd.field_name,
      default_value: fd.default_value,
      is_required: fd.is_required,
    })),
    customFields: experiment.custom_fields.map((cf) => ({
      field_name: cf.field_name,
      field_value: cf.field_value ?? "",
      is_required: cf.is_required,
    })),
  };
}

/** Sets one field default; a default with no value and no requirement is removed. */
export function withFieldDefault(
  prev: FieldDefaultValue[],
  fieldName: string,
  value: string | null,
  isRequired: boolean | null,
): FieldDefaultValue[] {
  const existing = prev.find((d) => d.field_name === fieldName);
  if (existing) {
    if (!value && isRequired === null) {
      return prev.filter((d) => d.field_name !== fieldName);
    }
    return prev.map((d) => (d.field_name === fieldName ? { ...d, default_value: value, is_required: isRequired } : d));
  }
  if (value || isRequired !== null) {
    return [...prev, { field_name: fieldName, default_value: value, is_required: isRequired }];
  }
  return prev;
}

export function overviewPayload(draft: OverviewDraft) {
  const customFields = draft.customFields
    .filter((f) => f.field_name.trim())
    .map((f) => ({
      field_name: f.field_name.trim(),
      field_value: f.field_value.trim(),
      field_type: "string",
      is_required: f.is_required,
    }));
  return { ...draft.form, field_defaults: draft.fieldDefaults, custom_fields: customFields };
}

/** A new sample starts from the experiment's field defaults and custom field values. */
export function newSampleDraft(experiment: ExperimentDetail): {
  form: SampleCreateRequest;
  customFields: Record<string, string>;
} {
  const prefill: Record<string, string> = {};
  for (const fd of experiment.field_defaults) {
    if (fd.default_value) prefill[fd.field_name] = fd.default_value;
  }
  const customFields: Record<string, string> = {};
  for (const cf of experiment.custom_fields) {
    customFields[cf.field_name] = cf.field_value ?? "";
  }
  return { form: prefill as unknown as SampleCreateRequest, customFields };
}

function filledCustomFields(values: Record<string, string>) {
  return Object.entries(values)
    .filter(([, v]) => v.trim())
    .map(([name, value]) => ({ field_name: name, field_value: value }));
}

export function newSamplePayload(form: SampleCreateRequest, customFields: Record<string, string>) {
  const cf = filledCustomFields(customFields);
  return { ...form, custom_fields: cf.length > 0 ? cf : undefined };
}

export function sampleEditDraft(sample: Sample): { form: SampleUpdateRequest; customFields: Record<string, string> } {
  const customFields: Record<string, string> = {};
  for (const cf of sample.custom_fields ?? []) {
    customFields[cf.field_name] = cf.field_value ?? "";
  }
  return {
    form: {
      external_id: sample.external_id,
      organism: sample.organism,
      tissue_type: sample.tissue_type,
      donor_source: sample.donor_source,
      treatment_condition: sample.treatment_condition,
      chemistry_version: sample.chemistry_version,
      viability_pct: sample.viability_pct,
      cell_count: sample.cell_count,
      molecule_type: sample.molecule_type,
      library_prep_method: sample.library_prep_method,
      library_layout: sample.library_layout,
      assay: sample.assay,
      sample_batch_code: sample.sample_batch?.name ?? null,
      sequencing_batch_code: sample.sequencing_batch?.code ?? null,
    },
    customFields,
  };
}

export function sampleEditPayload(form: SampleUpdateRequest, customFields: Record<string, string>) {
  return { ...form, custom_fields: filledCustomFields(customFields) };
}

/** Only the fields the user filled in; null when there is nothing to update. */
export function bulkUpdate(form: SampleUpdateRequest): SampleUpdateRequest | null {
  const update: SampleUpdateRequest = {};
  for (const [key, val] of Object.entries(form)) {
    if (val !== undefined && val !== null && val !== "") {
      (update as Record<string, unknown>)[key] = val;
    }
  }
  return Object.keys(update).length === 0 ? null : update;
}
