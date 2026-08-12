import { useState } from "react";
import type { ParameterSchema } from "@/lib/types";

type ParamGroup = NonNullable<ParameterSchema["definitions"]>[string];
type ParamProp = NonNullable<ParamGroup["properties"]>[string];

/** The parameter groups a schema declares, under either spelling.
 *
 *  JSON Schema 2020-12 renamed `definitions` to `$defs`, and the current nf-core
 *  template emits `$defs`. Reading only `definitions` dropped the form entirely
 *  for 13 of the 20 most popular pipelines (rnaseq, sarek and scrnaseq among
 *  them), and every pipeline that adopts the current template would join them. */
export function parameterGroups(schema: ParameterSchema | null): [string, ParamGroup][] {
  const groups = schema?.$defs ?? schema?.definitions;
  return groups ? Object.entries(groups) : [];
}

/** Auto-generated parameter form from the pipeline's nextflow_schema.json. */
export function ParameterForm({
  schema,
  defaultParams,
  values,
  onChange,
}: {
  schema: ParameterSchema | null;
  defaultParams: Record<string, unknown>;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const groups = parameterGroups(schema);

  if (groups.length === 0) {
    return (
      <div className="text-sm text-gray-500">
        <p className="mb-3">No parameter schema available. Enter parameters as JSON:</p>
        <textarea
          aria-label="Pipeline parameters as JSON"
          value={JSON.stringify(values, null, 2)}
          onChange={(e) => {
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              // Typing JSON means passing through invalid states on the way to a
              // valid one. Reporting each keystroke would be noise, not help.
            }
          }}
          className="w-full h-40 border rounded px-3 py-2 font-mono text-xs"
        />
      </div>
    );
  }

  // Managed by the launcher itself; showing them invites a user to fight it.
  const managedParams = new Set(["input", "outdir"]);

  function setValue(key: string, val: unknown) {
    onChange({ ...values, [key]: val });
  }

  return (
    <div className="space-y-6">
      {groups.map(([groupKey, group]) => {
        if (!group.properties) return null;
        const entries = Object.entries(group.properties).filter(
          ([k, prop]) => !managedParams.has(k) && !prop.hidden,
        );
        const advancedEntries = Object.entries(group.properties).filter(
          ([k, prop]) => !managedParams.has(k) && prop.hidden,
        );

        if (entries.length === 0 && advancedEntries.length === 0) return null;

        const field = ([paramKey, prop]: [string, ParamProp]) => (
          <ParameterField
            key={paramKey}
            paramKey={paramKey}
            prop={prop}
            required={group.required?.includes(paramKey)}
            value={values[paramKey] ?? prop.default ?? defaultParams[paramKey]}
            onChange={(v) => setValue(paramKey, v)}
          />
        );

        return (
          <div key={groupKey}>
            <h3 className="font-medium text-sm text-gray-700 mb-3">{group.title || groupKey}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{entries.map(field)}</div>
            {advancedEntries.length > 0 && (
              <details className="mt-3">
                <summary className="text-sm text-gray-500 cursor-pointer">
                  Advanced ({advancedEntries.length} params)
                </summary>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">{advancedEntries.map(field)}</div>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ParameterField({
  paramKey,
  prop,
  required,
  value,
  onChange,
}: {
  paramKey: string;
  prop: ParamProp;
  required?: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = paramKey.replace(/_/g, " ");
  // Per-parameter, because a schema group renders dozens of these. The previous
  // hardcoded ids ("lbl-page-1") repeated across every field of the same kind,
  // so aria-labelledby resolved to whichever duplicate came first.
  const fieldId = `param-${paramKey}`;

  if (prop.enum) {
    return (
      <div>
        <label htmlFor={fieldId} className="text-xs text-gray-500">
          {label}
          {required ? " *" : ""}
        </label>
        <select
          id={fieldId}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-3 py-1.5 text-sm"
        >
          <option value="">--</option>
          {prop.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
        {prop.description && <p className="text-xs text-gray-500 mt-0.5">{prop.description}</p>}
      </div>
    );
  }

  if (prop.type === "boolean") {
    return (
      <div className="flex items-center gap-2">
        <input
          id={fieldId}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <label htmlFor={fieldId} className="text-sm">
          {label}
          {required ? " *" : ""}
        </label>
        {prop.description && <span className="text-xs text-gray-500">({prop.description})</span>}
      </div>
    );
  }

  if (prop.type === "number" || prop.type === "integer") {
    return (
      <div>
        <label htmlFor={fieldId} className="text-xs text-gray-500">
          {label}
          {required ? " *" : ""}
        </label>
        <input
          id={fieldId}
          type="number"
          value={value != null ? String(value) : ""}
          min={prop.minimum}
          max={prop.maximum}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
          className="w-full border rounded px-3 py-1.5 text-sm"
        />
        {prop.description && <p className="text-xs text-gray-500 mt-0.5">{prop.description}</p>}
      </div>
    );
  }

  return (
    <div>
      <label htmlFor={fieldId} className="text-xs text-gray-500">
        {label}
        {required ? " *" : ""}
      </label>
      <input
        id={fieldId}
        type="text"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border rounded px-3 py-1.5 text-sm"
      />
      {prop.description && <p className="text-xs text-gray-500 mt-0.5">{prop.description}</p>}
    </div>
  );
}
