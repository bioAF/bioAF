"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { ExtensibleVocabularySelect } from "@/components/shared/ExtensibleVocabularySelect";
import type {
  Experiment,
  ExperimentCreateRequest,
  ExperimentTemplate,
  FieldDefaultValue,
  ProjectListResponse,
  SampleCreateRequest,
} from "@/lib/types";

export default function NewExperimentPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [templates, setTemplates] = useState<ExperimentTemplate[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [form, setForm] = useState<ExperimentCreateRequest>({
    name: "",
    project_id: null,
    template_id: null,
    design_type: null,
    hypothesis: null,
    description: null,
    start_date: new Date().toISOString().split("T")[0],
    expected_sample_count: null,
  });

  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [fieldDefaults, setFieldDefaults] = useState<FieldDefaultValue[]>([]);
  const [showFieldDefaults, setShowFieldDefaults] = useState(false);
  const [customFieldValues, setCustomFieldValues] = useState<Record<string, string>>({});
  const [extraCustomFields, setExtraCustomFields] = useState<{ name: string; value: string; required: boolean }[]>([]);

  const DEFAULTABLE_FIELDS = [
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
  ];

  function updateFieldDefault(fieldName: string, value: string | null, isRequired: boolean | null) {
    setFieldDefaults((prev) => {
      const existing = prev.find((d) => d.field_name === fieldName);
      if (existing) {
        if (!value && isRequired === null) {
          return prev.filter((d) => d.field_name !== fieldName);
        }
        return prev.map((d) => d.field_name === fieldName ? { ...d, default_value: value, is_required: isRequired } : d);
      }
      if (value || isRequired !== null) {
        return [...prev, { field_name: fieldName, default_value: value, is_required: isRequired }];
      }
      return prev;
    });
  }

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    Promise.all([
      api.get<ProjectListResponse>("/api/projects"),
      api.get<ExperimentTemplate[]>("/api/templates"),
    ]).then(([projData, templateData]) => {
      setProjects(projData.projects.map((p) => ({ id: p.id, name: p.name })));
      setTemplates(templateData);
    }).catch(() => {});
  }, [router]);

  const selectedTemplate = templates.find((t) => t.id === form.template_id);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      setError("Name is required");
      return;
    }
    setSubmitting(true);
    setError("");

    try {
      const templateFields = Object.entries(customFieldValues)
        .filter(([, v]) => v.trim())
        .map(([name, value]) => ({
          field_name: name,
          field_value: value,
          field_type: "string",
        }));
      const userFields = extraCustomFields
        .filter((f) => f.name.trim())
        .map((f) => ({
          field_name: f.name.trim(),
          field_value: f.value.trim(),
          field_type: "string",
          is_required: f.required,
        }));
      const allCustomFields = [...templateFields, ...userFields];
      const payload = {
        ...form,
        field_defaults: fieldDefaults.length > 0 ? fieldDefaults : undefined,
        custom_fields: allCustomFields.length > 0 ? allCustomFields : undefined,
      };
      const experiment = await api.post<Experiment>("/api/experiments", payload);

      if (csvFile) {
        await api.upload(`/api/experiments/${experiment.id}/samples/upload`, csvFile);
      }

      router.push(`/experiments/${experiment.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create experiment");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">New Experiment</h1>

          <form onSubmit={handleSubmit} className="max-w-2xl space-y-6">
            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
                {error}
              </div>
            )}

            <div className="bg-white rounded-lg shadow p-6 space-y-4">
              <h2 className="text-lg font-semibold">Basics</h2>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Template (optional)</label>
                <select
                  value={form.template_id ?? ""}
                  onChange={(e) => setForm({ ...form, template_id: e.target.value ? Number(e.target.value) : null })}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  <option value="">No template</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                <input
                  type="text"
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Project</label>
                <select
                  value={form.project_id ?? ""}
                  onChange={(e) => setForm({ ...form, project_id: e.target.value ? Number(e.target.value) : null })}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  <option value="">No project</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Design Type</label>
                <ExtensibleVocabularySelect
                  fieldName="design_type"
                  value={form.design_type ?? null}
                  onChange={(v) => setForm({ ...form, design_type: v })}
                  placeholder="Select design type..."
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Hypothesis</label>
                <textarea
                  value={form.hypothesis ?? ""}
                  onChange={(e) => setForm({ ...form, hypothesis: e.target.value || null })}
                  rows={3}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={form.description ?? ""}
                  onChange={(e) => setForm({ ...form, description: e.target.value || null })}
                  rows={3}
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
                  <input
                    type="date"
                    value={form.start_date ?? ""}
                    onChange={(e) => setForm({ ...form, start_date: e.target.value || null })}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Expected Sample Count</label>
                  <input
                    type="number"
                    min={0}
                    value={form.expected_sample_count ?? ""}
                    onChange={(e) => setForm({ ...form, expected_sample_count: e.target.value ? Number(e.target.value) : null })}
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  />
                </div>
              </div>
            </div>

            <div className="bg-white rounded-lg shadow p-6 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Sample Field Defaults</h2>
                  <p className="text-sm text-gray-500 mt-1">
                    Set default values applied to all samples in this experiment. Per-sample values still override these.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowFieldDefaults(!showFieldDefaults)}
                  className="text-sm text-bioaf-600 hover:underline"
                >
                  {showFieldDefaults ? "Hide" : "Configure"}
                </button>
              </div>

              {showFieldDefaults && (
                <div className="space-y-3 pt-2">
                  {DEFAULTABLE_FIELDS.map((field) => {
                    const current = fieldDefaults.find((d) => d.field_name === field.name);
                    return (
                      <div key={field.name} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] gap-3 items-center">
                        <label className="text-sm text-gray-700 truncate">{field.label}</label>
                        <div className="min-w-0">
                          {field.type === "vocabulary" ? (
                            <VocabularySelect
                              fieldName={field.name}
                              value={current?.default_value ?? null}
                              onChange={(v) => updateFieldDefault(field.name, v, current?.is_required ?? null)}
                              placeholder={`Default...`}
                              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                            />
                          ) : (
                            <input
                              type="text"
                              value={current?.default_value ?? ""}
                              onChange={(e) => updateFieldDefault(field.name, e.target.value || null, current?.is_required ?? null)}
                              placeholder="Default..."
                              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                            />
                          )}
                        </div>
                        <label className="flex items-center gap-2 text-sm text-gray-600 whitespace-nowrap">
                          <input
                            type="checkbox"
                            checked={current?.is_required ?? false}
                            onChange={(e) => updateFieldDefault(field.name, current?.default_value ?? null, e.target.checked || null)}
                            className="rounded border-gray-300"
                          />
                          Required
                        </label>
                      </div>
                    );
                  })}

                  {selectedTemplate?.custom_fields_schema_json &&
                    (selectedTemplate.custom_fields_schema_json as { fields?: Array<{ name: string; type: string; required?: boolean }> })?.fields?.map((field) => (
                      <div key={`tmpl-${field.name}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] gap-3 items-center">
                        <label className="text-sm text-gray-700 truncate">{field.name} {field.required && "*"}</label>
                        <div className="min-w-0">
                          <input
                            type={field.type === "number" ? "number" : "text"}
                            value={customFieldValues[field.name] ?? ""}
                            onChange={(e) => setCustomFieldValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                            placeholder="Default..."
                            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                          />
                        </div>
                        <span className="text-xs text-gray-400 whitespace-nowrap">from template</span>
                      </div>
                    ))
                  }

                  {extraCustomFields.map((field, idx) => (
                    <div key={`extra-${idx}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto] gap-3 items-center">
                      <input
                        type="text"
                        value={field.name}
                        onChange={(e) => setExtraCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, name: e.target.value } : f))}
                        placeholder="Field name..."
                        className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                      />
                      <div className="min-w-0">
                        <input
                          type="text"
                          value={field.value}
                          onChange={(e) => setExtraCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, value: e.target.value } : f))}
                          placeholder="Default..."
                          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                        />
                      </div>
                      <div className="flex items-center gap-2 whitespace-nowrap">
                        <label className="flex items-center gap-1 text-sm text-gray-600">
                          <input
                            type="checkbox"
                            checked={field.required}
                            onChange={(e) => setExtraCustomFields((prev) => prev.map((f, i) => i === idx ? { ...f, required: e.target.checked } : f))}
                            className="rounded border-gray-300"
                          />
                          Required
                        </label>
                        <button
                          type="button"
                          onClick={() => setExtraCustomFields((prev) => prev.filter((_, i) => i !== idx))}
                          className="text-red-400 hover:text-red-600 text-xs ml-1"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}

                  <button
                    type="button"
                    onClick={() => setExtraCustomFields((prev) => [...prev, { name: "", value: "", required: false }])}
                    className="text-sm text-bioaf-600 hover:underline"
                  >
                    + Add Field
                  </button>
                </div>
              )}
            </div>

            <div className="bg-white rounded-lg shadow p-6 space-y-4">
              <h2 className="text-lg font-semibold">Initial Samples (optional)</h2>
              <p className="text-sm text-gray-500">
                Upload a CSV/TSV file to add samples when the experiment is created.
              </p>
              <input
                type="file"
                accept=".csv,.tsv,.txt"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                className="block text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:font-semibold file:bg-bioaf-50 file:text-bioaf-700 hover:file:bg-bioaf-100"
              />
              {csvFile && (
                <p className="text-sm text-gray-600">
                  Selected: {csvFile.name} ({(csvFile.size / 1024).toFixed(1)} KB)
                </p>
              )}
            </div>

            <div className="flex gap-4">
              <button
                type="submit"
                disabled={submitting}
                className="bg-bioaf-600 text-white px-6 py-2 rounded-md hover:bg-bioaf-700 disabled:opacity-50"
              >
                {submitting ? "Creating..." : "Create Experiment"}
              </button>
              <button
                type="button"
                onClick={() => router.back()}
                className="border border-gray-300 px-6 py-2 rounded-md hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </form>
        </main>
      </div>
    </div>
  );
}
