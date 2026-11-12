"use client";

import { NOT_SET } from "@/lib/placeholders";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ExperimentStatusBadge } from "@/components/experiments/ExperimentStatusBadge";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { AssaySelect } from "@/components/shared/AssaySelect";
import { ExtensibleVocabularySelect } from "@/components/shared/ExtensibleVocabularySelect";
import { NamingProfileSelect } from "@/components/naming/NamingProfileSelect";
import type { ExperimentDetail, ExperimentStatus, NamingProfile } from "@/lib/types";
import { DEFAULTABLE_FIELDS } from "./experimentForms";
import type { OverviewEditor } from "./useOverviewEditor";

const STATUSES: ExperimentStatus[] = [
  "registered",
  "library_prep",
  "sequencing",
  "fastq_uploaded",
  "processing",
  "pipeline_complete",
  "reviewed",
  "analysis",
  "complete",
];

export function ExperimentOverviewTab({
  experiment,
  editor,
  namingProfiles,
  onStatusChange,
}: {
  experiment: ExperimentDetail;
  editor: OverviewEditor;
  namingProfiles: NamingProfile[];
  onStatusChange: (status: string) => void;
}) {
  const draft = editor.draft;
  const form = draft?.form ?? {};

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Experiment Details</h2>
          {!draft && (
            <button onClick={() => editor.start(experiment)} className="text-sm text-bioaf-600 hover:underline">Edit</button>
          )}
        </div>

        {draft ? (
          <div className="space-y-3">
            <div>
              <label htmlFor="name" className="block text-sm text-ink-subtle mb-1">Name</label>
              <input id="name" value={form.name ?? ""} onChange={(e) => editor.setForm({ name: e.target.value })} className="w-full border rounded px-3 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-sm text-ink-subtle mb-1">Design Type</label>
              <ExtensibleVocabularySelect
                fieldName="design_type"
                value={form.design_type ?? null}
                onChange={(v) => editor.setForm({ design_type: v })}
                placeholder="Select design type..."
                className="w-full border rounded px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label htmlFor="hypothesis" className="block text-sm text-ink-subtle mb-1">Hypothesis</label>
              <textarea id="hypothesis" value={form.hypothesis ?? ""} onChange={(e) => editor.setForm({ hypothesis: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
            </div>
            <div>
              <label htmlFor="description" className="block text-sm text-ink-subtle mb-1">Description</label>
              <textarea id="description" value={form.description ?? ""} onChange={(e) => editor.setForm({ description: e.target.value || null })} rows={3} className="w-full border rounded px-3 py-1.5 text-sm" />
            </div>
            {(() => {
              const templateProfile =
                experiment.template_naming_profile_id != null
                  ? namingProfiles.find((p) => p.id === experiment.template_naming_profile_id)
                  : null;
              const overriding = form.naming_profile_id != null;
              const hint = overriding
                ? "Override: this experiment uses the selected profile."
                : templateProfile
                  ? `Inherited from template '${experiment.template_name}': ${templateProfile.name}.`
                  : "No profile set on this experiment or its template.";
              return (
                <NamingProfileSelect
                  id="exp-naming-profile"
                  label="Naming profile (override)"
                  hint={hint}
                  emptyLabel={templateProfile ? "Inherit from template" : "No profile"}
                  value={form.naming_profile_id ?? null}
                  onChange={(v) => editor.setForm({ naming_profile_id: v })}
                  profiles={namingProfiles}
                />
              );
            })()}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="start-date" className="block text-sm text-ink-subtle mb-1">Start Date</label>
                <input id="start-date" type="date" value={form.start_date ?? ""} onChange={(e) => editor.setForm({ start_date: e.target.value || null })} className="w-full border rounded px-3 py-1.5 text-sm" />
              </div>
              <div>
                <label htmlFor="expected-samples" className="block text-sm text-ink-subtle mb-1">Expected Samples</label>
                <input id="expected-samples" type="number" min={0} value={form.expected_sample_count ?? ""} onChange={(e) => editor.setForm({ expected_sample_count: e.target.value ? Number(e.target.value) : null })} className="w-full border rounded px-3 py-1.5 text-sm" />
              </div>
            </div>
            <div className="border-t pt-3 mt-3">
              <h3 className="text-sm font-medium text-ink-muted mb-2">Sample Field Defaults</h3>
              <p className="text-xs text-ink-subtle mb-2">Default values applied to new samples. Per-sample values override these.</p>
              <div className="space-y-2">
                {DEFAULTABLE_FIELDS.map((field) => {
                  const current = draft.fieldDefaults.find((d) => d.field_name === field.name);
                  return (
                    <div key={field.name} className="grid grid-cols-3 gap-2 items-center">
                      <span className="text-xs text-gray-600">{field.label}</span>
                      {field.type === "assay" ? (
                        <AssaySelect
                          value={current?.default_value ?? null}
                          onChange={(v) => editor.setFieldDefault(field.name, v, current?.is_required ?? null)}
                          placeholder="Default..."
                        />
                      ) : field.type === "vocabulary" ? (
                        <VocabularySelect
                          fieldName={field.name}
                          value={current?.default_value ?? null}
                          onChange={(v) => editor.setFieldDefault(field.name, v, current?.is_required ?? null)}
                          placeholder={`Default...`}
                        />
                      ) : (
                        <input aria-label="Default"
                          value={current?.default_value ?? ""}
                          onChange={(e) => editor.setFieldDefault(field.name, e.target.value || null, current?.is_required ?? null)}
                          placeholder="Default..."
                          className="border rounded px-2 py-1 text-sm"
                        />
                      )}
                      <label className="flex items-center gap-1 text-xs text-ink-subtle">
                        <input
                          type="checkbox"
                          checked={current?.is_required ?? false}
                          onChange={(e) => editor.setFieldDefault(field.name, current?.default_value ?? null, e.target.checked || null)}
                          className="rounded border-gray-300"
                        />
                        Required
                      </label>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="border-t pt-3 mt-3">
              <h3 className="text-sm font-medium text-ink-muted mb-2">Custom Fields</h3>
              <div className="space-y-2">
                {draft.customFields.map((cf, idx) => (
                  <div key={idx} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] gap-2 items-center">
                    <input aria-label="Field name"
                      value={cf.field_name}
                      onChange={(e) => editor.setCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, field_name: e.target.value } : f))}
                      placeholder="Field name"
                      className="w-full border rounded px-2 py-1 text-sm"
                    />
                    <div className="min-w-0">
                      <input aria-label="Custom field value"
                        value={cf.field_value}
                        onChange={(e) => editor.setCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, field_value: e.target.value } : f))}
                        placeholder="Value"
                        className="w-full border rounded px-2 py-1 text-sm"
                      />
                    </div>
                    <div className="flex items-center gap-2 whitespace-nowrap">
                      <label className="flex items-center gap-1 text-xs text-gray-600">
                        <input
                          type="checkbox"
                          checked={cf.is_required}
                          onChange={(e) => editor.setCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, is_required: e.target.checked } : f))}
                          className="rounded border-gray-300"
                        />
                        Required
                      </label>
                      <button
                        type="button"
                        onClick={() => editor.setCustomFields((prev) => prev.filter((_, i) => i !== idx))}
                        className="text-red-400 hover:text-red-600 text-xs ml-1"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => editor.setCustomFields((prev) => [...prev, { field_name: "", field_value: "", is_required: false }])}
                  className="text-sm text-bioaf-600 hover:underline"
                >
                  + Add Field
                </button>
              </div>
            </div>
            {editor.error && <p className="text-red-600 text-sm">{editor.error}</p>}
            <div className="flex gap-2 pt-1">
              <Button size="sm" onClick={() => void editor.save()}>Save</Button>
              <button onClick={editor.cancel} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
            </div>
          </div>
        ) : (
          <dl className="space-y-3">
            <div><dt className="text-sm text-ink-subtle">Project</dt><dd className="text-sm">{experiment.project?.name || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Template</dt><dd className="text-sm">{experiment.template_name || NOT_SET}</dd></div>
            <div>
              <dt className="text-sm text-ink-subtle">Naming Profile</dt>
              <dd className="text-sm">
                {(() => {
                  const eff = experiment.effective_naming_profile_id;
                  if (eff == null) return NOT_SET;
                  const profile = namingProfiles.find((p) => p.id === eff);
                  const label = profile?.name ?? `#${eff}`;
                  const overriding = experiment.naming_profile_id != null;
                  return `${label}${overriding ? " (override)" : " (from template)"}`;
                })()}
              </dd>
            </div>
            <div><dt className="text-sm text-ink-subtle">Design Type</dt><dd className="text-sm">{experiment.design_type || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Owner</dt><dd className="text-sm">{experiment.owner?.name || experiment.owner?.email || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Hypothesis</dt><dd className="text-sm">{experiment.hypothesis || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Description</dt><dd className="text-sm">{experiment.description || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Start Date</dt><dd className="text-sm">{experiment.start_date || NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Expected Samples</dt><dd className="text-sm">{experiment.expected_sample_count ?? NOT_SET}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Actual Samples</dt><dd className="text-sm">{experiment.sample_count}</dd></div>
            <div><dt className="text-sm text-ink-subtle">Created</dt><dd className="text-sm">{new Date(experiment.created_at).toLocaleString()}</dd></div>
          </dl>
        )}
      </Card>

      <Card>
        <h2 className="text-lg font-semibold mb-4">Status</h2>
        <div className="space-y-3">
          <div className="flex items-center gap-4">
            <ExperimentStatusBadge status={experiment.status} />
            <select
              aria-label="Change experiment status"
              onChange={(e) => { if (e.target.value) onStatusChange(e.target.value); e.target.value = ""; }}
              className="border border-gray-300 rounded-md px-3 py-1.5 text-sm"
              defaultValue=""
            >
              <option value="" disabled>Advance status...</option>
              {STATUSES.filter((s) => s !== experiment.status).map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
            </select>
          </div>
        </div>

        {experiment.custom_fields.length > 0 && (
          <>
            <h3 className="text-md font-semibold mt-6 mb-3">Custom Fields</h3>
            {experiment.template_name && (
              <p className="text-xs text-ink-subtle mb-3">Controlled by template: {experiment.template_name}</p>
            )}
            <dl className="space-y-2">
              {experiment.custom_fields.map((cf) => (
                <div key={cf.id} className="flex items-center gap-2">
                  <dt className="text-sm text-ink-subtle">{cf.field_name}</dt>
                  <dd className="text-sm text-ink-subtle">{cf.field_value || NOT_SET}</dd>
                  {cf.is_required && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">required</span>}
                </div>
              ))}
            </dl>
          </>
        )}

        {experiment.field_defaults.length > 0 && (
          <>
            <h3 className="text-md font-semibold mt-6 mb-3">Sample Field Defaults</h3>
            <p className="text-xs text-ink-subtle mb-3">Applied to new samples unless overridden per-sample.</p>
            <dl className="space-y-2">
              {experiment.field_defaults.map((fd) => {
                const label = DEFAULTABLE_FIELDS.find((f) => f.name === fd.field_name)?.label ?? fd.field_name;
                return (
                  <div key={fd.id} className="flex items-center gap-2">
                    <dt className="text-sm text-ink-subtle">{label}</dt>
                    <dd className="text-sm text-gray-600">{fd.default_value || NOT_SET}</dd>
                    {fd.is_required && <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">required</span>}
                  </div>
                );
              })}
            </dl>
          </>
        )}
      </Card>
    </div>
  );
}
