"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useEffect, useState } from "react";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { TemplateSheetImportModal } from "@/components/experiments/TemplateSheetImportModal";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { NamingProfileSelect } from "@/components/naming/NamingProfileSelect";
import type { ExperimentTemplate, TemplateCreateRequest } from "@/lib/types";
import { useToast } from "@/components/shared/Toast";
import { ErrorState } from "@/components/shared/ErrorState";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

const STANDARD_SAMPLE_FIELDS = [
  "organism",
  "tissue_type",
  "donor_source",
  "treatment_condition",
  "chemistry_version",
  "sample_batch_code",
  "sequencing_batch_code",
  "molecule_type",
  "library_prep_method",
  "library_layout",
];

export default function ExperimentTemplatesPage() {
  const toast = useToast();
  const [templates, setTemplates] = useState<ExperimentTemplate[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showSheetImport, setShowSheetImport] = useState(false);

  const [form, setForm] = useState<TemplateCreateRequest>({
    name: "",
    description: null,
    required_fields_json: { sample_fields: [], experiment_fields: [] },
    custom_fields_schema_json: { fields: [] },
    naming_profile_id: null,
  });

  useEffect(() => {
    loadTemplates();
  }, []);

  async function loadTemplates() {
    try {
      const data = await api.get<ExperimentTemplate[]>("/api/templates");
      setTemplates(data);
      setLoadError(null);
    } catch (e) {
      logError("loading experiment templates", e);
      setLoadError(loadFailureMessage("Templates"));
    } finally {
      setLoading(false);
    }
  }

  function handleToggleRequiredField(field: string) {
    const requiredFields = (form.required_fields_json as Record<string, string[]>) || { sample_fields: [] };
    const sampleFields = requiredFields.sample_fields || [];
    const updated = sampleFields.includes(field)
      ? sampleFields.filter((f: string) => f !== field)
      : [...sampleFields, field];
    setForm({
      ...form,
      required_fields_json: { ...requiredFields, sample_fields: updated },
    });
  }

  function handleAddCustomField() {
    const schema = (form.custom_fields_schema_json as { fields: Array<{ name: string; type: string; required: boolean }> }) || { fields: [] };
    setForm({
      ...form,
      custom_fields_schema_json: {
        fields: [...schema.fields, { name: "", type: "string", required: false }],
      },
    });
  }

  function handleRemoveCustomField(index: number) {
    const schema = (form.custom_fields_schema_json as { fields: Array<{ name: string; type: string; required: boolean }> }) || { fields: [] };
    setForm({
      ...form,
      custom_fields_schema_json: {
        fields: schema.fields.filter((_: unknown, i: number) => i !== index),
      },
    });
  }

  function handleCustomFieldChange(index: number, key: string, value: string | boolean) {
    const schema = (form.custom_fields_schema_json as { fields: Array<Record<string, unknown>> }) || { fields: [] };
    const fields = [...schema.fields];
    fields[index] = { ...fields[index], [key]: value };
    setForm({ ...form, custom_fields_schema_json: { fields } });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      if (editingId) {
        await api.patch(`/api/templates/${editingId}`, form);
      } else {
        await api.post("/api/templates", form);
      }
      setShowForm(false);
      setEditingId(null);
      setForm({ name: "", description: null, required_fields_json: { sample_fields: [], experiment_fields: [] }, custom_fields_schema_json: { fields: [] }, naming_profile_id: null });
      loadTemplates();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the template.");
    }
  }

  function handleEdit(t: ExperimentTemplate) {
    setForm({
      name: t.name,
      description: t.description,
      required_fields_json: t.required_fields_json || { sample_fields: [], experiment_fields: [] },
      custom_fields_schema_json: t.custom_fields_schema_json || { fields: [] },
      naming_profile_id: t.naming_profile_id ?? null,
    });
    setEditingId(t.id);
    setShowForm(true);
  }

  function handleSheetImportApply(result: {
    requiredSampleFields: string[];
    customFields: { name: string; type: string; required: boolean }[];
  }) {
    const requiredFields = (form.required_fields_json as Record<string, string[]>) || { sample_fields: [] };
    setForm({
      ...form,
      required_fields_json: { ...requiredFields, sample_fields: result.requiredSampleFields },
      custom_fields_schema_json: { fields: result.customFields },
    });
  }

  const requiredSampleFields = ((form.required_fields_json as Record<string, string[]>)?.sample_fields) || [];
  const customFields = ((form.custom_fields_schema_json as { fields: Array<{ name: string; type: string; required: boolean }> })?.fields) || [];

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Experiment Templates</h1>
          <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
            Reusable field definitions that pre-fill an experiment&apos;s metadata and sample structure when you register a new one.
          </p>
        </div>
        <Button
          onClick={() => { setShowForm(!showForm); setEditingId(null); }}>
          {showForm ? "Cancel" : "Create Template"}
        </Button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 mb-6 max-w-2xl space-y-4">
          <h2 className="text-lg font-semibold">{editingId ? "Edit Template" : "New Template"}</h2>

          <div>
            <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
            <input id="name"
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label htmlFor="description" className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea id="description"
              value={form.description ?? ""}
              onChange={(e) => setForm({ ...form, description: e.target.value || null })}
              rows={2}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            />
          </div>

          <NamingProfileSelect
            id="tmpl-naming-profile"
            label="Default naming profile (optional)"
            hint="Experiments created from this template inherit this default. They can override it per-experiment."
            emptyLabel="No default profile"
            value={form.naming_profile_id ?? null}
            onChange={(v) => setForm({ ...form, naming_profile_id: v })}
          />

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-700">Required Sample Fields</label>
              <button
                type="button"
                onClick={() => setShowSheetImport(true)}
                className="text-sm text-bioaf-600 hover:text-bioaf-700"
              >
                Import from Google Sheet
              </button>
            </div>
            <div className="flex flex-wrap gap-3">
              {STANDARD_SAMPLE_FIELDS.map((field) => (
                <label key={field} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={requiredSampleFields.includes(field)}
                    onChange={() => handleToggleRequiredField(field)}
                  />
                  {field.replace(/_/g, " ")}
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label htmlFor="custom-fields" className="block text-sm font-medium text-gray-700">Custom Fields</label>
              <button type="button" onClick={handleAddCustomField} className="text-sm text-bioaf-600 hover:text-bioaf-700">
                + Add Field
              </button>
            </div>
            {customFields.map((cf, i) => (
              <div key={i} className="flex gap-2 mb-2">
                <input id="custom-fields"
                  placeholder="Field name"
                  value={cf.name}
                  onChange={(e) => handleCustomFieldChange(i, "name", e.target.value)}
                  className="border rounded px-2 py-1 text-sm flex-1"
                />
                <select aria-label="Type"
                  value={cf.type}
                  onChange={(e) => handleCustomFieldChange(i, "type", e.target.value)}
                  className="border rounded px-2 py-1 text-sm"
                >
                  <option value="string">Text</option>
                  <option value="number">Number</option>
                  <option value="date">Date</option>
                </select>
                <label className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={cf.required}
                    onChange={(e) => handleCustomFieldChange(i, "required", e.target.checked)}
                  />
                  Req
                </label>
                <button type="button" onClick={() => handleRemoveCustomField(i)} className="text-red-500 text-sm">Remove</button>
              </div>
            ))}
          </div>

          <button type="submit" className="bg-bioaf-600 text-white px-6 py-2 rounded-md hover:bg-bioaf-700">
            {editingId ? "Update" : "Create"} Template
          </button>
        </form>
      )}

      {loading ? (
        <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
      ) : loadError ? (
        <ErrorState message={loadError} onRetry={() => loadTemplates()} />
      ) : templates.length === 0 ? (
        <Card padding="none" className="p-12 text-center">
          <p className="text-gray-500">No templates yet. Create one to standardize experiment registration.</p>
        </Card>
      ) : (
        <div className="grid gap-4">
          {templates.map((t) => (
            <div key={t.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">{t.name}</h3>
                  {t.description && <p className="text-sm text-gray-500">{t.description}</p>}
                  <p className="text-xs text-gray-500 mt-1">
                    Created by {t.created_by?.name || t.created_by?.email || NOT_SET} on {new Date(t.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  onClick={() => handleEdit(t)}
                  className="text-sm text-bioaf-600 hover:text-bioaf-700"
                >
                  Edit
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showSheetImport && (
        <TemplateSheetImportModal
          onClose={() => setShowSheetImport(false)}
          onApply={handleSheetImportApply}
          existingRequiredSampleFields={requiredSampleFields}
          existingCustomFields={customFields}
        />
      )}
    </main>
  );
}
