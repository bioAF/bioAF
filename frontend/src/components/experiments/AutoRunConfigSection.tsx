"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  AutoRunConfig,
  AutoRunConfigCreate,
  AutoRunConfigUpdate,
  PipelineCatalog,
  PipelineCatalogListResponse,
  ParameterSchema,
} from "@/lib/types";

export function AutoRunConfigSection({ experimentId }: { experimentId: number }) {
  const [configs, setConfigs] = useState<AutoRunConfig[]>([]);
  const [pipelines, setPipelines] = useState<PipelineCatalog[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<AutoRunConfig | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);

  useEffect(() => {
    loadConfigs();
  }, [experimentId]);

  async function loadConfigs() {
    try {
      const data = await api.get<AutoRunConfig[]>(`/api/experiments/${experimentId}/auto-runs`);
      setConfigs(data);
    } catch {} finally { setLoading(false); }
  }

  async function handleToggle(config: AutoRunConfig) {
    try {
      await api.patch<AutoRunConfig>(
        `/api/experiments/${experimentId}/auto-runs/${config.id}`,
        { enabled: !config.enabled },
      );
      loadConfigs();
    } catch {}
  }

  async function handleDelete(configId: number) {
    try {
      await api.delete(`/api/experiments/${experimentId}/auto-runs/${configId}`);
      setDeleteConfirm(null);
      loadConfigs();
    } catch {}
  }

  function openCreate() {
    setEditingConfig(null);
    setShowModal(true);
  }

  function openEdit(config: AutoRunConfig) {
    setEditingConfig(config);
    setShowModal(true);
  }

  function handleSaved() {
    setShowModal(false);
    setEditingConfig(null);
    loadConfigs();
  }

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-md font-semibold text-gray-700">Auto-Run Configurations</h3>
        <button
          onClick={openCreate}
          className="bg-bioaf-600 text-white px-4 py-1.5 rounded-md text-sm hover:bg-bioaf-700"
        >
          Configure Auto-Run
        </button>
      </div>

      {loading ? (
        <div className="bg-white rounded-lg shadow p-8 text-center text-gray-400">Loading...</div>
      ) : configs.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <p className="text-gray-400 text-sm">
            No auto-run configurations. Configure one to automatically run pipelines
            when sample files arrive.
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pipeline</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Delay</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Parameters</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {configs.map((c) => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm font-medium">{c.pipeline_key}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {c.delay_minutes === 0 ? "Immediate" : `${c.delay_minutes} min`}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {c.parameters && Object.keys(c.parameters).length > 0
                      ? `${Object.keys(c.parameters).length} overrides`
                      : "Defaults"}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleToggle(c)}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                        c.enabled ? "bg-green-500" : "bg-gray-300"
                      }`}
                    >
                      <span
                        className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                          c.enabled ? "translate-x-4.5" : "translate-x-0.5"
                        }`}
                      />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-sm space-x-2">
                    <button onClick={() => openEdit(c)} className="text-bioaf-600 hover:underline">Edit</button>
                    {deleteConfirm === c.id ? (
                      <>
                        <button onClick={() => handleDelete(c.id)} className="text-red-600 hover:underline">Confirm</button>
                        <button onClick={() => setDeleteConfirm(null)} className="text-gray-500 hover:underline">Cancel</button>
                      </>
                    ) : (
                      <button onClick={() => setDeleteConfirm(c.id)} className="text-red-600 hover:underline">Delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <AutoRunConfigModal
          experimentId={experimentId}
          existingConfig={editingConfig}
          onClose={() => { setShowModal(false); setEditingConfig(null); }}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}


// ---- Modal ----

function AutoRunConfigModal({
  experimentId,
  existingConfig,
  onClose,
  onSaved,
}: {
  experimentId: number;
  existingConfig: AutoRunConfig | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!existingConfig;

  const [step, setStep] = useState<1 | 2 | 3>(isEdit ? 2 : 1);
  const [pipelines, setPipelines] = useState<PipelineCatalog[]>([]);
  const [selectedPipelineKey, setSelectedPipelineKey] = useState(existingConfig?.pipeline_key || "");
  const [selectedPipeline, setSelectedPipeline] = useState<PipelineCatalog | null>(null);
  const [userParams, setUserParams] = useState<Record<string, unknown>>(
    (existingConfig?.parameters as Record<string, unknown>) || {},
  );
  const [delayMinutes, setDelayMinutes] = useState(existingConfig?.delay_minutes ?? 0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    loadPipelines();
  }, []);

  useEffect(() => {
    if (selectedPipelineKey) {
      loadPipelineDetail(selectedPipelineKey);
    }
  }, [selectedPipelineKey]);

  async function loadPipelines() {
    try {
      const data = await api.get<PipelineCatalogListResponse>("/api/pipelines");
      setPipelines(data.pipelines);
      if (existingConfig) {
        const match = data.pipelines.find((p) => p.pipeline_key === existingConfig.pipeline_key);
        if (match) setSelectedPipeline(match);
      }
    } catch {}
  }

  async function loadPipelineDetail(key: string) {
    try {
      const data = await api.get<PipelineCatalog>(`/api/pipelines/${encodeURIComponent(key)}`);
      setSelectedPipeline(data);
      if (!isEdit && data.default_params) {
        setUserParams({ ...data.default_params });
      }
    } catch {}
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    try {
      if (isEdit && existingConfig) {
        const update: AutoRunConfigUpdate = {
          parameters: userParams,
          delay_minutes: delayMinutes,
        };
        await api.patch(`/api/experiments/${experimentId}/auto-runs/${existingConfig.id}`, update);
      } else {
        const create: AutoRunConfigCreate = {
          pipeline_key: selectedPipelineKey,
          parameters: userParams,
          delay_minutes: delayMinutes,
        };
        await api.post(`/api/experiments/${experimentId}/auto-runs`, create);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save");
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-lg font-semibold">
              {isEdit ? "Edit Auto-Run Configuration" : "Configure Auto-Run"}
            </h2>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
          </div>

          {/* Step indicator */}
          <div className="flex items-center gap-2 mb-6">
            {[1, 2, 3].map((s) => (
              <div key={s} className="flex items-center gap-2">
                <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium ${
                  s === step ? "bg-bioaf-600 text-white" : s < step ? "bg-green-500 text-white" : "bg-gray-200 text-gray-500"
                }`}>{s}</div>
                <span className="text-xs text-gray-500">
                  {s === 1 ? "Pipeline" : s === 2 ? "Parameters" : "Schedule"}
                </span>
                {s < 3 && <div className="w-6 h-px bg-gray-300" />}
              </div>
            ))}
          </div>

          {/* Step 1: Select Pipeline */}
          {step === 1 && (
            <div>
              <label className="text-sm text-gray-600 mb-2 block">Select Pipeline</label>
              <select
                value={selectedPipelineKey}
                onChange={(e) => setSelectedPipelineKey(e.target.value)}
                className="w-full border rounded-md px-3 py-2 text-sm"
              >
                <option value="">Choose a pipeline...</option>
                {pipelines.map((p) => (
                  <option key={p.pipeline_key} value={p.pipeline_key}>
                    {p.name} {p.version ? `v${p.version}` : ""}
                  </option>
                ))}
              </select>
              {selectedPipeline && (
                <p className="text-xs text-gray-400 mt-2">{selectedPipeline.description || ""}</p>
              )}
              <div className="mt-6 flex justify-end">
                <button
                  onClick={() => setStep(2)}
                  disabled={!selectedPipelineKey}
                  className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}

          {/* Step 2: Configure Parameters */}
          {step === 2 && (
            <div>
              {selectedPipeline?.parameter_schema?.definitions ? (
                <ModalParameterForm
                  schema={selectedPipeline.parameter_schema}
                  defaultParams={selectedPipeline.default_params || {}}
                  values={userParams}
                  onChange={setUserParams}
                />
              ) : (
                <div className="text-sm text-gray-500">
                  <p className="mb-3">No parameter schema available. Enter parameters as JSON:</p>
                  <textarea
                    value={JSON.stringify(userParams, null, 2)}
                    onChange={(e) => { try { setUserParams(JSON.parse(e.target.value)); } catch {} }}
                    className="w-full h-32 border rounded px-3 py-2 font-mono text-xs"
                  />
                </div>
              )}
              <div className="mt-6 flex justify-between">
                {!isEdit && <button onClick={() => setStep(1)} className="border px-6 py-2 rounded-md text-sm">Back</button>}
                {isEdit && <div />}
                <button onClick={() => setStep(3)} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700">Next</button>
              </div>
            </div>
          )}

          {/* Step 3: Schedule & Review */}
          {step === 3 && (
            <div>
              <div className="mb-6">
                <label className="text-sm text-gray-600 block mb-1">Delay after sample completion (minutes)</label>
                <input
                  type="number"
                  min={0}
                  value={delayMinutes}
                  onChange={(e) => setDelayMinutes(Math.max(0, Number(e.target.value)))}
                  className="w-32 border rounded px-3 py-1.5 text-sm"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Pipeline will launch this many minutes after all expected files for a sample
                  are verified. Set to 0 for immediate launch.
                </p>
              </div>

              <div className="border-t pt-4">
                <h3 className="text-sm font-medium text-gray-700 mb-3">Summary</h3>
                <dl className="space-y-2 text-sm">
                  <div><dt className="text-gray-500 inline">Pipeline:</dt> <dd className="inline font-medium">{selectedPipelineKey}</dd></div>
                  <div><dt className="text-gray-500 inline">Delay:</dt> <dd className="inline">{delayMinutes === 0 ? "Immediate" : `${delayMinutes} minutes`}</dd></div>
                  <div>
                    <dt className="text-gray-500 inline">Parameter overrides:</dt>
                    <dd className="inline">
                      {selectedPipeline?.default_params
                        ? ` ${Object.entries(userParams).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify((selectedPipeline.default_params || {})[k])).length} non-default`
                        : ` ${Object.keys(userParams).length} set`}
                    </dd>
                  </div>
                </dl>
              </div>

              {error && <p className="text-red-600 text-sm mt-3">{error}</p>}

              <div className="mt-6 flex justify-between">
                <button onClick={() => setStep(2)} className="border px-6 py-2 rounded-md text-sm">Back</button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="bg-green-600 text-white px-8 py-2 rounded-md text-sm hover:bg-green-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : isEdit ? "Save Changes" : "Save Auto-Run Configuration"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


// Simplified parameter form for the modal (replicates the launch wizard pattern)
function ModalParameterForm({
  schema,
  defaultParams,
  values,
  onChange,
}: {
  schema: ParameterSchema;
  defaultParams: Record<string, unknown>;
  values: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  const managedParams = new Set(["input", "outdir"]);

  function setValue(key: string, val: unknown) {
    onChange({ ...values, [key]: val });
  }

  const groups = Object.entries(schema.definitions || {});

  return (
    <div className="space-y-4">
      {groups.map(([groupKey, group]) => {
        if (!group.properties) return null;
        const entries = Object.entries(group.properties).filter(
          ([k, prop]) => !managedParams.has(k) && !prop.hidden,
        );
        if (entries.length === 0) return null;

        return (
          <div key={groupKey}>
            <h4 className="font-medium text-xs text-gray-600 mb-2">{group.title || groupKey}</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {entries.map(([paramKey, prop]) => {
                const value = values[paramKey] ?? prop.default ?? defaultParams[paramKey];
                const label = paramKey.replace(/_/g, " ");

                if (prop.enum) {
                  return (
                    <div key={paramKey}>
                      <label className="text-xs text-gray-500">{label}</label>
                      <select value={String(value ?? "")} onChange={(e) => setValue(paramKey, e.target.value)} className="w-full border rounded px-3 py-1.5 text-sm">
                        <option value="">--</option>
                        {prop.enum.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
                      </select>
                    </div>
                  );
                }

                if (prop.type === "boolean") {
                  return (
                    <div key={paramKey} className="flex items-center gap-2">
                      <input type="checkbox" checked={Boolean(value)} onChange={(e) => setValue(paramKey, e.target.checked)} />
                      <label className="text-sm">{label}</label>
                    </div>
                  );
                }

                if (prop.type === "number" || prop.type === "integer") {
                  return (
                    <div key={paramKey}>
                      <label className="text-xs text-gray-500">{label}</label>
                      <input type="number" value={value != null ? String(value) : ""} onChange={(e) => setValue(paramKey, e.target.value ? Number(e.target.value) : null)} className="w-full border rounded px-3 py-1.5 text-sm" />
                    </div>
                  );
                }

                return (
                  <div key={paramKey}>
                    <label className="text-xs text-gray-500">{label}</label>
                    <input type="text" value={String(value ?? "")} onChange={(e) => setValue(paramKey, e.target.value)} className="w-full border rounded px-3 py-1.5 text-sm" />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
