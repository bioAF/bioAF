"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ExperimentTemplate, TemplateCreateRequest } from "@/lib/types";

const STANDARD_SAMPLE_FIELDS = [
  "organism",
  "tissue_type",
  "donor_source",
  "treatment_condition",
  "chemistry_version",
];

export default function TemplatesPage() {
  const router = useRouter();
  const [templates, setTemplates] = useState<ExperimentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [viewingTemplate, setViewingTemplate] = useState<ExperimentTemplate | null>(null);

  const [form, setForm] = useState<TemplateCreateRequest>({
    name: "",
    description: null,
    required_fields_json: { sample_fields: [], experiment_fields: [] },
    custom_fields_schema_json: { fields: [] },
  });

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadTemplates();
  }, [router]);

  async function loadTemplates() {
    try {
      const data = await api.get<ExperimentTemplate[]>("/api/templates");
      setTemplates(data);
    } catch {} finally {
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
      setForm({ name: "", description: null, required_fields_json: { sample_fields: [], experiment_fields: [] }, custom_fields_schema_json: { fields: [] } });
      loadTemplates();
    } catch {}
  }

  function handleEdit(t: ExperimentTemplate) {
    setForm({
      name: t.name,
      description: t.description,
      required_fields_json: t.required_fields_json || { sample_fields: [], experiment_fields: [] },
      custom_fields_schema_json: t.custom_fields_schema_json || { fields: [] },
    });
    setEditingId(t.id);
    setShowForm(true);
  }

  const requiredSampleFields = ((form.required_fields_json as Record<string, string[]>)?.sample_fields) || [];
  const customFields = ((form.custom_fields_schema_json as { fields: Array<{ name: string; type: string; required: boolean }> })?.fields) || [];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Experiment Templates</h1>
            <button
              onClick={() => { setShowForm(!showForm); setEditingId(null); }}
              className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
            >
              {showForm ? "Cancel" : "Create Template"}
            </button>
          </div>

          {showForm && (
            <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 mb-6 max-w-2xl space-y-4">
              <h2 className="text-lg font-semibold">{editingId ? "Edit Template" : "New Template"}</h2>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={form.description ?? ""}
                  onChange={(e) => setForm({ ...form, description: e.target.value || null })}
                  rows={2}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Required Sample Fields</label>
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
                  <label className="block text-sm font-medium text-gray-700">Custom Fields</label>
                  <button type="button" onClick={handleAddCustomField} className="text-sm text-bioaf-600 hover:text-bioaf-700">
                    + Add Field
                  </button>
                </div>
                {customFields.map((cf, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input
                      placeholder="Field name"
                      value={cf.name}
                      onChange={(e) => handleCustomFieldChange(i, "name", e.target.value)}
                      className="border rounded px-2 py-1 text-sm flex-1"
                    />
                    <select
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
          ) : templates.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-12 text-center">
              <p className="text-gray-400">No templates yet. Create one to standardize experiment registration.</p>
            </div>
          ) : (
            <div className="grid gap-4">
              {templates.map((t) => (
                <div key={t.id} className="bg-white rounded-lg shadow p-4 hover:bg-gray-50 cursor-pointer" onClick={() => setViewingTemplate(t)}>
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold">{t.name}</h3>
                      {t.description && <p className="text-sm text-gray-500">{t.description}</p>}
                      <p className="text-xs text-gray-400 mt-1">
                        Created by {t.created_by?.name || t.created_by?.email || "—"} on {new Date(t.created_at).toLocaleDateString()}
                      </p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleEdit(t); }}
                      className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50"
                    >
                      Edit
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {viewingTemplate && (
            <DetailModal
              title={viewingTemplate.name}
              onClose={() => setViewingTemplate(null)}
              fields={[
                { label: "Description", value: viewingTemplate.description },
                { label: "Created By", value: viewingTemplate.created_by?.name || viewingTemplate.created_by?.email },
                { label: "Created", value: new Date(viewingTemplate.created_at).toLocaleDateString() },
                {
                  label: "Required Sample Fields",
                  value: (() => {
                    const fields = (viewingTemplate.required_fields_json as Record<string, string[]>)?.sample_fields;
                    return fields?.length ? fields.join(", ") : "None";
                  })(),
                },
                {
                  label: "Custom Fields",
                  value: (() => {
                    const schema = viewingTemplate.custom_fields_schema_json as { fields?: Array<{ name: string; type: string }> } | null;
                    const fields = schema?.fields;
                    return fields?.length ? fields.map((f) => `${f.name} (${f.type})`).join(", ") : "None";
                  })(),
                },
              ]}
              actions={
                <button
                  onClick={() => { setViewingTemplate(null); handleEdit(viewingTemplate); }}
                  className="px-3 py-1.5 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50"
                >
                  Edit
                </button>
              }
            />
          )}
        </main>
      </div>
    </div>
  );
}
