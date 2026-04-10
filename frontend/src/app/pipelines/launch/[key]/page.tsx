"use client";

import { useEffect, useState } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import type {
  PipelineCatalog,
  Experiment,
  ExperimentListResponse,
  SampleBrief,
  PipelineRunLaunchRequest,
  PipelineRun,
  ParameterSchema,
} from "@/lib/types";

type Step = 1 | 2 | 3 | 4;

const CHEMISTRY_TO_PROTOCOL: Record<string, string> = {
  "v1": "10XV1",
  "v2": "10XV2",
  "v3": "10XV3",
  "v3.1": "10XV3",
  "nextgem v3.1": "10XV3",
  "nextgem v3": "10XV3",
  "10x chromium 3' v1": "10XV1",
  "10x chromium 3' v2": "10XV2",
  "10x chromium 3' v3": "10XV3",
  "10x chromium 3' v3.1": "10XV3",
  "10x chromium 5' v1": "10XV1",
  "10x chromium 5' v2": "10XV2",
  "10x chromium 5' v3": "10XV3",
};

function detectProtocol(samples: SampleBrief[]): string | null {
  const versions = new Set(
    samples
      .map((s) => s.chemistry_version?.trim().toLowerCase())
      .filter(Boolean) as string[],
  );
  if (versions.size !== 1) return null;
  const version = [...versions][0];
  return CHEMISTRY_TO_PROTOCOL[version] || null;
}

export default function PipelineLauncherPage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const pipelineKey = decodeURIComponent(params.key as string);
  const preselectedExperimentId = searchParams.get("experiment");

  const [pipeline, setPipeline] = useState<PipelineCatalog | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [samples, setSamples] = useState<SampleBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [launching, setLaunching] = useState(false);

  const [step, setStep] = useState<Step>(1);
  const [selectedExperimentId, setSelectedExperimentId] = useState<number | null>(
    preselectedExperimentId ? Number(preselectedExperimentId) : null,
  );
  const [selectedSampleIds, setSelectedSampleIds] = useState<number[]>([]);
  const [userParams, setUserParams] = useState<Record<string, unknown>>({});

  const [detectedProtocol, setDetectedProtocol] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadData();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, pipelineKey]);

  useEffect(() => {
    if (selectedExperimentId && pipeline) loadSamples(selectedExperimentId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedExperimentId, pipeline]);

  async function loadData() {
    try {
      const [pipelineData, expData] = await Promise.all([
        api.get<PipelineCatalog>(`/api/pipelines/${encodeURIComponent(pipelineKey)}`),
        api.get<ExperimentListResponse>("/api/experiments?page_size=100"),
      ]);
      setPipeline(pipelineData);
      setExperiments(expData.experiments);
      if (pipelineData.default_params) {
        setUserParams({ ...pipelineData.default_params });
      }
    } catch {} finally { setLoading(false); }
  }

  async function loadSamples(experimentId: number) {
    try {
      const data = await api.get<SampleBrief[]>(`/api/experiments/${experimentId}/samples`);
      setSamples(data);
      setSelectedSampleIds(data.map((s) => s.id));
      // Auto-detect protocol from sample chemistry_version
      const protocol = detectProtocol(data);
      setDetectedProtocol(protocol);
      if (protocol) {
        setUserParams((prev) => ({ ...prev, protocol }));
      }
    } catch {}
  }

  async function handleLaunch() {
    if (!selectedExperimentId || !pipeline) return;
    setLaunching(true);
    try {
      const request: PipelineRunLaunchRequest = {
        pipeline_key: pipelineKey,
        experiment_id: selectedExperimentId,
        sample_ids: selectedSampleIds.length > 0 ? selectedSampleIds : null,
        parameters: userParams,
      };
      const run = await api.post<PipelineRun>("/api/pipeline-runs", request);
      router.push(`/pipelines/runs/${run.id}`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Launch failed");
      setLaunching(false);
    }
  }

  function toggleSample(id: number) {
    setSelectedSampleIds((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  }

  if (!loading && !pipeline) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center"><p className="text-gray-500">Pipeline not found</p></main>
        </div>
      </div>
    );
  }

  const selectedExperiment = experiments.find((e) => e.id === selectedExperimentId);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : pipeline && (
          <>
          <div className="flex items-center gap-4 mb-6">
            <button onClick={() => router.push("/pipelines")} className="text-gray-500 hover:text-gray-700">← Back</button>
            <h1 className="text-2xl font-bold">Launch {pipeline.name}</h1>
          </div>

          {/* Step indicator */}
          <div className="flex items-center gap-2 mb-8">
            {[1, 2, 3, 4].map((s) => (
              <div key={s} className="flex items-center gap-2">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  s === step ? "bg-bioaf-600 text-white" : s < step ? "bg-green-500 text-white" : "bg-gray-200 text-gray-500"
                }`}>{s}</div>
                <span className="text-sm text-gray-500">
                  {s === 1 ? "Experiment" : s === 2 ? "Samples" : s === 3 ? "Parameters" : "Review"}
                </span>
                {s < 4 && <div className="w-8 h-px bg-gray-300" />}
              </div>
            ))}
          </div>

          {/* Step 1: Select Experiment */}
          {step === 1 && (
            <div className="bg-white rounded-lg shadow p-6 max-w-2xl">
              <h2 className="text-lg font-semibold mb-4">Select Experiment</h2>
              <select
                value={selectedExperimentId ?? ""}
                onChange={(e) => setSelectedExperimentId(Number(e.target.value) || null)}
                className="w-full border rounded-md px-3 py-2 text-sm"
              >
                <option value="">Choose an experiment...</option>
                {experiments.map((exp) => (
                  <option key={exp.id} value={exp.id}>{exp.name} ({exp.sample_count} samples, {exp.status})</option>
                ))}
              </select>
              <div className="mt-4 flex justify-end">
                <button
                  onClick={() => setStep(2)}
                  disabled={!selectedExperimentId}
                  className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
                >Next</button>
              </div>
            </div>
          )}

          {/* Step 2: Select Samples */}
          {step === 2 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Select Samples</h2>
              <div className="mb-3 flex items-center gap-4">
                <label className="text-sm">
                  <input
                    type="checkbox"
                    checked={selectedSampleIds.length === samples.length}
                    onChange={() => setSelectedSampleIds(selectedSampleIds.length === samples.length ? [] : samples.map((s) => s.id))}
                    className="mr-2"
                  />
                  Select All ({samples.length})
                </label>
                <span className="text-sm text-gray-500">{selectedSampleIds.length} selected</span>
              </div>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 w-10"></th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Sample ID</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">QC</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {samples.map((s) => (
                    <tr key={s.id} className={s.qc_status === "fail" ? "bg-red-50" : ""}>
                      <td className="px-4 py-3">
                        <input type="checkbox" checked={selectedSampleIds.includes(s.id)} onChange={() => toggleSample(s.id)} />
                      </td>
                      <td className="px-4 py-3 text-sm">{s.sample_id_unique || `#${s.id}`}</td>
                      <td className="px-4 py-3 text-sm">{s.organism || "—"}</td>
                      <td className="px-4 py-3 text-sm">{s.tissue_type || "—"}</td>
                      <td className="px-4 py-3 text-sm">
                        {s.qc_status === "fail" && <span className="text-red-600 font-medium">FAIL</span>}
                        {s.qc_status === "warning" && <span className="text-yellow-600">Warning</span>}
                        {s.qc_status === "pass" && <span className="text-green-600">Pass</span>}
                        {!s.qc_status && "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-4 flex justify-between">
                <button onClick={() => setStep(1)} className="border px-6 py-2 rounded-md text-sm">Back</button>
                <button onClick={() => setStep(3)} disabled={selectedSampleIds.length === 0} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50">Next</button>
              </div>
            </div>
          )}

          {/* Step 3: Configure Parameters */}
          {step === 3 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-lg font-semibold mb-4">Configure Parameters</h2>
              {detectedProtocol && (
                <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded text-sm">
                  Protocol auto-detected as <span className="font-semibold">{detectedProtocol}</span> from sample chemistry version.
                </div>
              )}
              <ProtocolInfo />
              <ParameterForm
                schema={pipeline.parameter_schema}
                defaultParams={pipeline.default_params || {}}
                values={userParams}
                onChange={setUserParams}
              />
              <div className="mt-4 flex justify-between">
                <button onClick={() => setStep(2)} className="border px-6 py-2 rounded-md text-sm">Back</button>
                <button onClick={() => setStep(4)} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700">Next</button>
              </div>
            </div>
          )}

          {/* Step 4: Review & Launch */}
          {step === 4 && (
            <div className="bg-white rounded-lg shadow p-6 max-w-2xl">
              <h2 className="text-lg font-semibold mb-4">Review & Launch</h2>
              <dl className="space-y-3 mb-6">
                <div><dt className="text-sm text-gray-500">Pipeline</dt><dd className="text-sm font-medium">{pipeline.name} v{pipeline.version}</dd></div>
                <div><dt className="text-sm text-gray-500">Experiment</dt><dd className="text-sm">{selectedExperiment?.name}</dd></div>
                <div><dt className="text-sm text-gray-500">Samples</dt><dd className="text-sm">{selectedSampleIds.length} selected</dd></div>
                <div>
                  <dt className="text-sm text-gray-500">Non-default Parameters</dt>
                  <dd className="text-sm">
                    {Object.entries(userParams).filter(([k, v]) => {
                      const def = (pipeline.default_params || {})[k];
                      return JSON.stringify(v) !== JSON.stringify(def);
                    }).length === 0 ? (
                      <span className="text-gray-400">All defaults</span>
                    ) : (
                      <ul className="list-disc ml-4 mt-1">
                        {Object.entries(userParams).filter(([k, v]) => {
                          const def = (pipeline.default_params || {})[k];
                          return JSON.stringify(v) !== JSON.stringify(def);
                        }).map(([k, v]) => <li key={k}><span className="font-mono text-xs">{k}</span>: {String(v)}</li>)}
                      </ul>
                    )}
                  </dd>
                </div>
              </dl>
              <div className="flex justify-between">
                <button onClick={() => setStep(3)} className="border px-6 py-2 rounded-md text-sm">Back</button>
                <button onClick={handleLaunch} disabled={launching} className="bg-green-600 text-white px-8 py-2 rounded-md text-sm hover:bg-green-700 disabled:opacity-50">
                  {launching ? "Launching..." : "Launch Pipeline"}
                </button>
              </div>
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}

// Auto-generated parameter form from nextflow_schema.json
function ParameterForm({
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
  const [showAdvanced, setShowAdvanced] = useState(false);

  if (!schema?.definitions) {
    return (
      <div className="text-sm text-gray-500">
        <p className="mb-3">No parameter schema available. Enter parameters as JSON:</p>
        <textarea
          value={JSON.stringify(values, null, 2)}
          onChange={(e) => { try { onChange(JSON.parse(e.target.value)); } catch {} }}
          className="w-full h-40 border rounded px-3 py-2 font-mono text-xs"
        />
      </div>
    );
  }

  const managedParams = new Set(["input", "outdir"]);

  function setValue(key: string, val: unknown) {
    onChange({ ...values, [key]: val });
  }

  const groups = Object.entries(schema.definitions);

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

        return (
          <div key={groupKey}>
            <h3 className="font-medium text-sm text-gray-700 mb-3">{group.title || groupKey}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {entries.map(([paramKey, prop]) => (
                <ParameterField
                  key={paramKey}
                  paramKey={paramKey}
                  prop={prop}
                  required={group.required?.includes(paramKey)}
                  value={values[paramKey] ?? prop.default ?? defaultParams[paramKey]}
                  onChange={(v) => setValue(paramKey, v)}
                />
              ))}
            </div>
            {advancedEntries.length > 0 && (
              <details className="mt-3">
                <summary className="text-sm text-gray-500 cursor-pointer">Advanced ({advancedEntries.length} params)</summary>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                  {advancedEntries.map(([paramKey, prop]) => (
                    <ParameterField
                      key={paramKey}
                      paramKey={paramKey}
                      prop={prop}
                      required={group.required?.includes(paramKey)}
                      value={values[paramKey] ?? prop.default ?? defaultParams[paramKey]}
                      onChange={(v) => setValue(paramKey, v)}
                    />
                  ))}
                </div>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ProtocolInfo() {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700"
      >
        <span className="w-4 h-4 rounded-full bg-gray-200 text-gray-500 text-[10px] font-bold flex items-center justify-center">i</span>
        <span>About the protocol parameter</span>
        <span className="text-xs">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="mt-2 p-3 bg-blue-50 border border-blue-200 rounded text-xs text-gray-700 space-y-2">
          <p>The <strong>protocol</strong> parameter tells the aligner how to parse barcode and UMI sequences from Read 1. It must match the 10x Chromium chemistry used during library preparation:</p>
          <ul className="list-disc ml-4 space-y-1">
            <li><strong>10XV1</strong> -- 10x Chromium Single Cell 3&apos; v1 (14bp barcode + 10bp UMI = 24bp R1)</li>
            <li><strong>10XV2</strong> -- 10x Chromium Single Cell 3&apos; v2 (16bp barcode + 10bp UMI = 26bp R1)</li>
            <li><strong>10XV3</strong> -- 10x Chromium Single Cell 3&apos; v3 or v3.1 (16bp barcode + 12bp UMI = 28bp R1)</li>
          </ul>
          <p>If your samples have a chemistry version set, bioAF will auto-detect the correct protocol. You can override it in the parameters below.</p>
          <p>A mismatch between protocol and actual chemistry will cause the aligner to fail with a barcode length error.</p>
        </div>
      )}
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
  prop: NonNullable<NonNullable<ParameterSchema["definitions"]>[string]["properties"]>[string];
  required?: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const label = paramKey.replace(/_/g, " ");

  if (prop.enum) {
    return (
      <div>
        <label className="text-xs text-gray-500">{label}{required ? " *" : ""}</label>
        <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} className="w-full border rounded px-3 py-1.5 text-sm">
          <option value="">--</option>
          {prop.enum.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
        {prop.description && <p className="text-xs text-gray-400 mt-0.5">{prop.description}</p>}
      </div>
    );
  }

  if (prop.type === "boolean") {
    return (
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <label className="text-sm">{label}{required ? " *" : ""}</label>
        {prop.description && <span className="text-xs text-gray-400">({prop.description})</span>}
      </div>
    );
  }

  if (prop.type === "number" || prop.type === "integer") {
    return (
      <div>
        <label className="text-xs text-gray-500">{label}{required ? " *" : ""}</label>
        <input
          type="number"
          value={value != null ? String(value) : ""}
          min={prop.minimum}
          max={prop.maximum}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
          className="w-full border rounded px-3 py-1.5 text-sm"
        />
        {prop.description && <p className="text-xs text-gray-400 mt-0.5">{prop.description}</p>}
      </div>
    );
  }

  // Default: string
  return (
    <div>
      <label className="text-xs text-gray-500">{label}{required ? " *" : ""}</label>
      <input
        type="text"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border rounded px-3 py-1.5 text-sm"
      />
      {prop.description && <p className="text-xs text-gray-400 mt-0.5">{prop.description}</p>}
    </div>
  );
}
