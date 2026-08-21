import type { SamplesheetInputSpec } from "@/lib/types";

/** Required samplesheet columns bioAF cannot derive from the samples themselves.
 *
 *  These are NOT pipeline parameters: they are columns of the pipeline's own
 *  samplesheet contract (assets/schema_input.json), so they never appear in
 *  nextflow_schema.json and the parameter form below cannot surface them.
 *
 *  Only values that are constant across the whole run reach this component, so
 *  one answer fills every row. Options come from the pipeline's schema, so what
 *  is offered cannot drift from what it accepts.
 *
 *  Leaving one empty blocks the launch rather than falling back to a default,
 *  which is why the copy says the pipeline NEEDS these rather than presenting
 *  them as optional tuning. */
export function SamplesheetInputs({
  specs,
  values,
  onChange,
}: {
  specs: SamplesheetInputSpec[];
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  if (specs.length === 0) return null;

  return (
    <div className="mb-6 p-3 border border-amber-200 bg-amber-50 rounded">
      <h3 className="font-medium text-sm text-gray-700 mb-1">Required by this pipeline</h3>
      <p className="text-xs text-gray-600 mb-3">
        This pipeline needs these values for every sample, and bioAF cannot read them from your
        samples. The run cannot start until they are set.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {specs.map((spec) => {
          const label = spec.name.replace(/_/g, " ");
          const current = values[spec.parameter];
          const answered = current !== undefined && current !== "";
          const fieldId = `samplesheet-input-${spec.name}`;

          return (
            <div key={spec.name}>
              <label htmlFor={fieldId} className="text-xs text-gray-500">
                {label}
                {!answered && spec.required && (
                  <span className="ml-1 text-amber-700 font-medium">(required)</span>
                )}
              </label>
              {spec.allowed_values.length > 0 ? (
                <select
                  id={fieldId}
                  value={String(current ?? "")}
                  onChange={(e) => onChange({ ...values, [spec.parameter]: e.target.value })}
                  className="w-full border rounded px-3 py-1.5 text-sm"
                >
                  <option value="">--</option>
                  {spec.allowed_values.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={fieldId}
                  type="text"
                  value={String(current ?? "")}
                  onChange={(e) => onChange({ ...values, [spec.parameter]: e.target.value })}
                  className="w-full border rounded px-3 py-1.5 text-sm"
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
