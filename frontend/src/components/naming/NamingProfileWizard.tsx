"use client";

import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type {
  NamingProfileDelimiter,
  NamingProfileTestResult,
  SegmentDateFormat,
  SegmentDefinition,
  SegmentFieldType,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Types local to the wizard
// ---------------------------------------------------------------------------

interface TemplateField {
  name: string;
  type: SegmentFieldType;
}

interface ExperimentTemplateOption {
  id: number;
  name: string;
  custom_fields_schema_json?: {
    fields?: TemplateField[];
  };
}

interface SystemChipSpec {
  label: string;
  ariaLabel: string;
  identifier: string;
  fieldName: string;
  fieldType: SegmentFieldType;
  padding: number;
}

const SYSTEM_CHIPS: SystemChipSpec[] = [
  {
    label: "Project Code",
    ariaLabel: "project code chip",
    identifier: "PRJ",
    fieldName: "ProjectCode",
    fieldType: "number",
    padding: 2,
  },
  {
    label: "Experiment Code",
    ariaLabel: "experiment code chip",
    identifier: "EXP",
    fieldName: "ExperimentCode",
    fieldType: "number",
    padding: 2,
  },
  {
    label: "Sample ID",
    ariaLabel: "sample id chip",
    identifier: "SMP",
    fieldName: "SampleID",
    fieldType: "number",
    padding: 2,
  },
];

const DATE_FORMATS: SegmentDateFormat[] = ["YYYYMMDD", "YYYY-MM-DD", "YYMMDD"];

const PADDING_DEFAULT = 2;
const PADDING_MIN = 0;
const PADDING_MAX = 3;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  onSave: () => void;
  onCancel: () => void;
}

export function NamingProfileWizard({ onSave, onCancel }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [delimiter, setDelimiter] = useState<NamingProfileDelimiter>("_");
  const [stripExtension, setStripExtension] = useState(true);
  const [segments, setSegments] = useState<SegmentDefinition[]>([]);
  const [templates, setTemplates] = useState<ExperimentTemplateOption[]>([]);
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [customFields, setCustomFields] = useState<TemplateField[]>([]);
  const [testFilename, setTestFilename] = useState("");
  const [testResult, setTestResult] = useState<NamingProfileTestResult | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .get<ExperimentTemplateOption[]>("/api/experiments/templates")
      .then(setTemplates)
      .catch(() => {
        // Template selection is optional; failure is non-blocking.
      });
  }, []);

  const templateFields: TemplateField[] = useMemo(() => {
    if (templateId === null) return [];
    const t = templates.find((t) => t.id === templateId);
    return t?.custom_fields_schema_json?.fields ?? [];
  }, [templateId, templates]);

  // ----- Validation -------------------------------------------------------
  const duplicateIdentifierError: string | null = useMemo(() => {
    const seen = new Map<string, string>();
    for (const seg of segments) {
      if (seg.identifier == null) continue;
      const key = seg.identifier.toLowerCase();
      if (seen.has(key)) {
        return `Identifier '${seg.identifier}' is used by more than one segment`;
      }
      seen.set(key, seg.identifier);
    }
    return null;
  }, [segments]);

  const dateDelimiterClash: boolean = useMemo(
    () =>
      delimiter === "-" &&
      segments.some(
        (s) => s.field_type === "date" && s.date_format === "YYYY-MM-DD",
      ),
    [delimiter, segments],
  );

  const canSave =
    segments.length > 0 && duplicateIdentifierError == null && !saving;

  // ----- Mutators ---------------------------------------------------------
  function addSegment(seg: Omit<SegmentDefinition, "position">) {
    setSegments((prev) => [...prev, { ...seg, position: prev.length }]);
  }

  function addSystemChip(chip: SystemChipSpec) {
    addSegment({
      identifier: chip.identifier,
      field_name: chip.fieldName,
      field_type: chip.fieldType,
      padding: chip.padding,
      date_format: null,
      is_system_chip: true,
    });
  }

  function addTemplateField(f: TemplateField) {
    const identifier = f.type === "date" ? null : defaultIdentifierFor(f.name);
    addSegment({
      identifier,
      field_name: f.name,
      field_type: f.type,
      padding: f.type === "number" ? PADDING_DEFAULT : null,
      date_format: f.type === "date" ? "YYYYMMDD" : null,
      is_system_chip: false,
    });
  }

  function addDateSegment() {
    addSegment({
      identifier: null,
      field_name: "RunDate",
      field_type: "date",
      padding: null,
      date_format: "YYYYMMDD",
      is_system_chip: false,
    });
  }

  function addCustomFieldThenSegment(fieldName: string, fieldType: SegmentFieldType) {
    setCustomFields((prev) =>
      prev.some((f) => f.name === fieldName) ? prev : [...prev, { name: fieldName, type: fieldType }],
    );
    addSegment({
      identifier: fieldType === "date" ? null : defaultIdentifierFor(fieldName),
      field_name: fieldName,
      field_type: fieldType,
      padding: fieldType === "number" ? PADDING_DEFAULT : null,
      date_format: fieldType === "date" ? "YYYYMMDD" : null,
      is_system_chip: false,
    });
  }

  function updateSegment(idx: number, patch: Partial<SegmentDefinition>) {
    setSegments((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    );
  }

  function removeSegment(idx: number) {
    setSegments((prev) =>
      prev.filter((_, i) => i !== idx).map((s, i) => ({ ...s, position: i })),
    );
  }

  function moveSegment(idx: number, delta: -1 | 1) {
    setSegments((prev) => {
      const next = idx + delta;
      if (next < 0 || next >= prev.length) return prev;
      const copy = [...prev];
      [copy[idx], copy[next]] = [copy[next], copy[idx]];
      return copy.map((s, i) => ({ ...s, position: i }));
    });
  }

  // ----- Actions ----------------------------------------------------------
  async function handleTest() {
    if (segments.length === 0 || !testFilename.trim()) return;
    setError("");
    try {
      const results = await api.post<NamingProfileTestResult[]>(
        "/api/naming-profiles/test",
        {
          filenames: [testFilename.trim()],
          delimiter,
          strip_extension: stripExtension,
          segments,
        },
      );
      setTestResult(results[0] ?? null);
    } catch {
      setError("Test parse failed.");
    }
  }

  async function handleSave() {
    if (!canSave) return;
    setError("");
    setSaving(true);
    try {
      await api.post("/api/naming-profiles", {
        name: name.trim() || "Untitled profile",
        description: description.trim() || null,
        delimiter,
        strip_extension: stripExtension,
        segments,
        experiment_template_id: templateId,
      });
      onSave();
    } catch {
      setError("Failed to save profile. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  // ----- Render -----------------------------------------------------------
  return (
    <div className="bg-white border rounded-lg p-6 mb-6 space-y-6">
      <h2 className="text-lg font-semibold">Create Naming Profile</h2>
      <p className="text-sm text-gray-600">
        Tell bioAF how your team encodes information in filenames. bioAF
        reads filenames; it never renames them.
      </p>

      {/* Template picker */}
      <section>
        <label htmlFor="np-template" className="block text-sm font-medium text-gray-700 mb-1">
          Experiment template (optional)
        </label>
        <select
          id="np-template"
          aria-label="Experiment template"
          value={templateId ?? ""}
          onChange={(e) => setTemplateId(e.target.value ? Number(e.target.value) : null)}
          className="w-full border rounded-lg px-3 py-2 text-sm"
        >
          <option value="">No template</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </section>

      {/* Basics */}
      <section className="grid grid-cols-2 gap-4">
        <div>
          <label htmlFor="np-name" className="block text-sm font-medium text-gray-700 mb-1">
            Profile name
          </label>
          <input
            id="np-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm"
            placeholder="Team A profile"
          />
        </div>
        <div>
          <label htmlFor="np-description" className="block text-sm font-medium text-gray-700 mb-1">
            Description
          </label>
          <input
            id="np-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label htmlFor="np-delimiter" className="block text-sm font-medium text-gray-700 mb-1">
            Delimiter
          </label>
          <select
            id="np-delimiter"
            value={delimiter}
            onChange={(e) => setDelimiter(e.target.value as NamingProfileDelimiter)}
            className="w-full border rounded-lg px-3 py-2 text-sm"
          >
            <option value="_">Underscore (_)</option>
            <option value="-">Hyphen (-)</option>
          </select>
        </div>
        <div className="flex items-center gap-2 pt-6">
          <input
            id="np-strip"
            type="checkbox"
            checked={stripExtension}
            onChange={(e) => setStripExtension(e.target.checked)}
          />
          <label htmlFor="np-strip" className="text-sm text-gray-700">
            Strip file extension
          </label>
        </div>
      </section>

      {/* Available fields panel */}
      <section>
        <h3 className="text-sm font-medium text-gray-700 mb-2">Available fields</h3>

        <div className="text-xs text-gray-500 mb-1">System chips (always available)</div>
        <div className="flex flex-wrap gap-2 mb-3">
          {SYSTEM_CHIPS.map((chip) => (
            <button
              key={chip.identifier}
              type="button"
              aria-label={chip.ariaLabel}
              onClick={() => addSystemChip(chip)}
              className="px-3 py-1 rounded-full border border-bioaf-200 bg-bioaf-50 text-sm text-bioaf-800 hover:bg-bioaf-100"
            >
              + {chip.label}
            </button>
          ))}
        </div>

        {templateFields.length > 0 && (
          <>
            <div className="text-xs text-gray-500 mb-1">Template fields</div>
            <div className="flex flex-wrap gap-2 mb-3">
              {templateFields.map((f) => (
                <button
                  key={f.name}
                  type="button"
                  onClick={() => addTemplateField(f)}
                  className="px-3 py-1 rounded-full border border-gray-200 bg-white text-sm text-gray-800 hover:bg-gray-50"
                >
                  + {f.name} <span className="text-gray-400">({f.type})</span>
                </button>
              ))}
            </div>
          </>
        )}

        {customFields.length > 0 && (
          <>
            <div className="text-xs text-gray-500 mb-1">Custom fields (this profile)</div>
            <div className="flex flex-wrap gap-2 mb-3">
              {customFields.map((f) => (
                <span key={f.name} className="px-3 py-1 rounded-full border border-gray-200 bg-gray-50 text-sm text-gray-800">
                  {f.name} <span className="text-gray-400">({f.type})</span>
                </span>
              ))}
            </div>
          </>
        )}

        <NewFieldInline onAdd={addCustomFieldThenSegment} />

        <div className="mt-3">
          <button
            type="button"
            onClick={addDateSegment}
            aria-label="add date segment"
            className="text-sm text-bioaf-700 hover:text-bioaf-800"
          >
            + Add date segment
          </button>
        </div>
      </section>

      {/* Segments list */}
      <section>
        <h3 className="text-sm font-medium text-gray-700 mb-2">Segments</h3>
        {segments.length === 0 ? (
          <p className="text-sm text-gray-500 italic">
            Add at least one segment from the panel above.
          </p>
        ) : (
          <ul data-testid="segments-list" className="space-y-2">
            {segments.map((seg, idx) => (
              <li
                key={`${seg.field_name}-${idx}`}
                className="border rounded-lg p-3 text-sm bg-gray-50 flex items-center gap-3"
              >
                <span className="font-mono text-xs text-gray-500 w-6">{idx + 1}</span>
                <div className="flex-1">
                  <div className="font-medium text-gray-800">{seg.field_name}</div>
                  <div className="text-xs text-gray-500">
                    {seg.field_type}
                    {seg.is_system_chip && " · system chip"}
                  </div>
                </div>
                {seg.field_type !== "date" && (
                  <label className="text-xs text-gray-600">
                    Identifier
                    <input
                      value={seg.identifier ?? ""}
                      onChange={(e) =>
                        updateSegment(idx, { identifier: e.target.value.toUpperCase() })
                      }
                      maxLength={4}
                      aria-label={`identifier-${idx}`}
                      className="ml-2 w-16 border rounded px-2 py-1 text-xs uppercase"
                    />
                  </label>
                )}
                {seg.field_type === "number" && (
                  <label className="text-xs text-gray-600">
                    Padding
                    <input
                      type="number"
                      value={seg.padding ?? PADDING_DEFAULT}
                      onChange={(e) =>
                        updateSegment(idx, {
                          padding: clamp(Number(e.target.value), PADDING_MIN, PADDING_MAX),
                        })
                      }
                      min={PADDING_MIN}
                      max={PADDING_MAX}
                      aria-label={`padding-${idx}`}
                      className="ml-2 w-14 border rounded px-2 py-1 text-xs"
                    />
                  </label>
                )}
                {seg.field_type === "date" && (
                  <label className="text-xs text-gray-600">
                    Date format
                    <select
                      value={seg.date_format ?? "YYYYMMDD"}
                      onChange={(e) =>
                        updateSegment(idx, {
                          date_format: e.target.value as SegmentDateFormat,
                        })
                      }
                      aria-label="date format"
                      className="ml-2 border rounded px-2 py-1 text-xs"
                    >
                      {DATE_FORMATS.map((f) => (
                        <option key={f} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <button
                  type="button"
                  onClick={() => moveSegment(idx, -1)}
                  aria-label={`move-up-${idx}`}
                  disabled={idx === 0}
                  className="text-gray-400 hover:text-gray-700 disabled:opacity-30"
                >
                  ▲
                </button>
                <button
                  type="button"
                  onClick={() => moveSegment(idx, 1)}
                  aria-label={`move-down-${idx}`}
                  disabled={idx === segments.length - 1}
                  className="text-gray-400 hover:text-gray-700 disabled:opacity-30"
                >
                  ▼
                </button>
                <button
                  type="button"
                  onClick={() => removeSegment(idx)}
                  aria-label={`remove-segment-${idx}`}
                  className="text-red-500 hover:text-red-700 text-xs"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        {duplicateIdentifierError && (
          <div className="mt-2 text-sm text-red-600">{duplicateIdentifierError}</div>
        )}
        {dateDelimiterClash && (
          <div className="mt-2 text-sm text-amber-700">
            Your date format shares its separator with the profile delimiter.
            bioAF will recombine bare-digit tokens to recover the date, but
            this can produce unexpected results if your filenames contain
            other 4-2-2 digit runs.
          </div>
        )}
      </section>

      {/* Test field */}
      <section>
        <label
          htmlFor="np-test"
          className="block text-sm font-medium text-gray-700 mb-1"
        >
          Test against a real filename
        </label>
        <div className="flex gap-2">
          <input
            id="np-test"
            value={testFilename}
            onChange={(e) => setTestFilename(e.target.value)}
            placeholder="Paste a real filename..."
            className="flex-1 border rounded-lg px-3 py-2 font-mono text-sm"
          />
          <button
            type="button"
            onClick={handleTest}
            className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
          >
            Parse
          </button>
        </div>
        {testResult && (
          <div data-testid="parse-result" className="mt-3 border rounded-lg p-3 bg-gray-50 text-sm">
            <div className="font-medium mb-1">Parsed</div>
            {Object.keys(testResult.parsed).length === 0 ? (
              <div className="italic text-gray-500">No fields recognized.</div>
            ) : (
              <ul className="space-y-1">
                {Object.entries(testResult.parsed).map(([k, v]) => (
                  <li key={k} className="font-mono text-xs">
                    <span className="font-semibold">{k}</span>: {v}
                  </li>
                ))}
              </ul>
            )}
            {testResult.unrecognized.length > 0 && (
              <>
                <div className="font-medium mt-2 mb-1">Unrecognized</div>
                <ul className="space-y-1">
                  {testResult.unrecognized.map((t) => (
                    <li key={t} className="font-mono text-xs text-gray-600">
                      {t}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {testResult.warnings.length > 0 && (
              <>
                <div className="font-medium mt-2 mb-1">Warnings</div>
                <ul className="space-y-1 text-amber-700 text-xs">
                  {testResult.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </section>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      <div className="flex gap-2 pt-2 border-t">
        <button
          type="button"
          onClick={handleSave}
          disabled={!canSave}
          className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save profile"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultIdentifierFor(name: string): string {
  const ascii = name.replace(/[^A-Za-z]/g, "");
  return (ascii.slice(0, 3) || "X").toUpperCase();
}

function clamp(n: number, min: number, max: number): number {
  if (Number.isNaN(n)) return min;
  return Math.max(min, Math.min(max, n));
}

interface NewFieldInlineProps {
  onAdd: (name: string, type: SegmentFieldType) => void;
}

function NewFieldInline({ onAdd }: NewFieldInlineProps) {
  const [open, setOpen] = useState(false);
  const [fieldName, setFieldName] = useState("");
  const [fieldType, setFieldType] = useState<SegmentFieldType>("string");

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm text-bioaf-700 hover:text-bioaf-800"
      >
        + Create new field
      </button>
    );
  }

  return (
    <div className="flex flex-wrap items-end gap-2 p-3 border border-dashed rounded-lg bg-gray-50">
      <label className="text-xs text-gray-600">
        Name
        <input
          value={fieldName}
          onChange={(e) => setFieldName(e.target.value)}
          aria-label="new field name"
          className="ml-2 border rounded px-2 py-1 text-sm"
        />
      </label>
      <label className="text-xs text-gray-600">
        Type
        <select
          value={fieldType}
          onChange={(e) => setFieldType(e.target.value as SegmentFieldType)}
          aria-label="new field type"
          className="ml-2 border rounded px-2 py-1 text-sm"
        >
          <option value="string">string</option>
          <option value="number">number</option>
          <option value="date">date</option>
        </select>
      </label>
      <button
        type="button"
        disabled={!fieldName.trim()}
        onClick={() => {
          onAdd(fieldName.trim(), fieldType);
          setFieldName("");
          setOpen(false);
        }}
        className="px-3 py-1 bg-bioaf-600 text-white rounded text-sm disabled:opacity-50"
      >
        Add field
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="px-3 py-1 border border-gray-300 rounded text-sm text-gray-700"
      >
        Cancel
      </button>
    </div>
  );
}
