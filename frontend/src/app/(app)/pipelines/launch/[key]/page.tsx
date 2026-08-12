"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useToast } from "@/components/shared/Toast";
import { useConfirm } from "@/hooks/useConfirm";
import { useEffect, useState } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { ErrorState } from "@/components/shared/ErrorState";
import { api, ApiError } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { Card } from "@/components/ui/Card";
import { SamplesheetInputs } from "@/components/pipelines/SamplesheetInputs";
import { ParameterForm } from "@/components/pipelines/ParameterForm";
import { detectProtocol, pipelineAcceptsProtocol } from "@/components/pipelines/protocolDetection";
import { LaunchBlockedNotice } from "@/components/pipelines/LaunchBlockedNotice";
import type {
  PipelineCatalog,
  Experiment,
  ExperimentListResponse,
  SampleBrief,
  PipelineRunLaunchRequest,
  PipelineRun,
  PipelineRunPreflight,
} from "@/lib/types";

type Step = 1 | 2 | 3 | 4;

export default function PipelineLauncherPage() {
  const router = useRouter();
  const toast = useToast();
  const confirm = useConfirm();
  const params = useParams();
  const searchParams = useSearchParams();
  const pipelineKey = decodeURIComponent(params.key as string);
  const preselectedExperimentId = searchParams.get("experiment");

  const [pipeline, setPipeline] = useState<PipelineCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
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
  const [preflight, setPreflight] = useState<PipelineRunPreflight | null>(null);

  useEffect(() => {
    loadData();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, pipelineKey]);

  useEffect(() => {
    if (selectedExperimentId && pipeline) loadSamples(selectedExperimentId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedExperimentId, pipeline]);

  // Ask whether this run could start, while the user can still change the
  // answer. The same checks run at launch; this only moves when they are heard.
  useEffect(() => {
    if (!selectedExperimentId || !pipeline) return;
    let cancelled = false;
    (async () => {
      try {
        const result = await api.post<PipelineRunPreflight>("/api/pipeline-runs/preflight", {
          pipeline_key: pipelineKey,
          experiment_id: selectedExperimentId,
          sample_ids: selectedSampleIds.length > 0 ? selectedSampleIds : null,
          parameters: userParams,
        });
        if (!cancelled) setPreflight(result);
      } catch (e) {
        // A preflight that cannot run must not block a launch that would work.
        logError("checking whether this pipeline can run", e);
        if (!cancelled) setPreflight(null);
      }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedExperimentId, pipeline, selectedSampleIds, userParams]);

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
      setLoadError(null);
    } catch (e) {
      // A 404 is a real "no such pipeline" and keeps its own wording. Anything
      // else means we do not know, and must not be reported as absence.
      if (e instanceof ApiError && e.status === 404) {
        setLoadError(null);
      } else {
        logError("loading the pipeline", e);
        setLoadError(loadFailureMessage("This pipeline"));
      }
    } finally { setLoading(false); }
  }

  async function loadSamples(experimentId: number) {
    try {
      const data = await api.get<SampleBrief[]>(`/api/experiments/${experimentId}/samples`);
      setSamples(data);
      setSelectedSampleIds(data.map((s) => s.id));
      // Auto-detect the 10x protocol from sample chemistry, but only offer it to
      // a pipeline whose own schema declares that parameter and accepts that
      // value. Injecting it blindly sent `protocol: 10XV3` to every pipeline,
      // including ones with no such parameter (sarek) and one whose parameter of
      // the same name means the input sample type (nanoseq).
      const protocol = detectProtocol(data);
      const accepted = pipelineAcceptsProtocol(pipeline?.parameter_schema ?? null, protocol);
      setDetectedProtocol(accepted ? protocol : null);
      if (accepted && protocol) {
        setUserParams((prev) => ({ ...prev, protocol }));
      }
    } catch (e) {
      logError("loading samples", e);
      toast.error(loadFailureMessage("Samples"));
    }
  }

  async function handleLaunch(dropSamplesWithoutFiles = false) {
    if (!selectedExperimentId || !pipeline) return;
    setLaunching(true);
    try {
      const request: PipelineRunLaunchRequest = {
        pipeline_key: pipelineKey,
        experiment_id: selectedExperimentId,
        sample_ids: selectedSampleIds.length > 0 ? selectedSampleIds : null,
        parameters: userParams,
        drop_samples_without_files: dropSamplesWithoutFiles,
      };
      const run = await api.post<PipelineRun>("/api/pipeline-runs", request);
      router.push(`/pipelines/runs/${run.id}`);
    } catch (err) {
      // The pipeline needs per-sample input files; some selected samples have
      // none. Offer to drop them and run with the rest instead of failing.
      if (
        err instanceof ApiError &&
        err.code === "samples_missing_files" &&
        !dropSamplesWithoutFiles
      ) {
        const offending =
          (err.details?.samples_without_files as
            | { id: number; external_id: string | null }[]
            | undefined) ?? [];
        const names = offending
          .map((s) => s.external_id || `sample ${s.id}`)
          .join(", ");
        setLaunching(false);
        // A dialog can show the sample list as its own line; the native confirm
        // could only jam it into one string with escaped newlines.
        const dropThem = await confirm({
          title: "Some samples have no input files",
          message: (
            <>
              <p>These samples have no linked input files and cannot run:</p>
              <p className="font-medium text-gray-900">{names}</p>
              <p>Drop them and launch with the remaining samples?</p>
            </>
          ),
          confirmLabel: "Drop and launch",
          cancelLabel: "Do not launch",
        });
        if (dropThem) {
          await handleLaunch(true);
        }
        return;
      }
      toast.error(err instanceof Error ? err.message : "Launch failed");
      setLaunching(false);
    }
  }

  function toggleSample(id: number) {
    setSelectedSampleIds((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  }

  if (!loading && !pipeline && loadError) {
    return (
      <main className="flex-1 flex items-center justify-center">
        <ErrorState
          message={loadError}
          onRetry={() => { setLoading(true); void loadData(); }}
        />
      </main>
    );
  }

  if (!loading && !pipeline) {
    return (
      <main className="flex-1 flex items-center justify-center"><p className="text-gray-500">Pipeline not found</p></main>
    );
  }

  const selectedExperiment = experiments.find((e) => e.id === selectedExperimentId);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {loading ? (
        <ContentLoading />
      ) : pipeline && (
      <>
      <div className="flex items-center gap-4 mb-6">
        <button onClick={() => router.push("/pipelines/catalog")} className="text-gray-500 hover:text-gray-700">← Back</button>
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
        <Card className="max-w-2xl">
          <h2 className="text-lg font-semibold mb-4">Select Experiment</h2>
          <select
            aria-label="Select experiment"
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
        </Card>
      )}

      {/* Step 2: Select Samples */}
      {step === 2 && (
        <Card>
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
                <th scope="col" className="px-4 py-3 w-10"></th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Sample ID</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">QC</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {samples.map((s) => (
                <tr key={s.id} className={s.qc_status === "fail" ? "bg-red-50" : ""}>
                  <td className="px-4 py-3">
                    <input type="checkbox" aria-label={`Select sample ${s.external_id || `#${s.id}`}`} checked={selectedSampleIds.includes(s.id)} onChange={() => toggleSample(s.id)} />
                  </td>
                  <td className="px-4 py-3 text-sm">{s.external_id || `#${s.id}`}</td>
                  <td className="px-4 py-3 text-sm">{s.organism || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm">{s.tissue_type || NOT_SET}</td>
                  <td className="px-4 py-3 text-sm">
                    {s.qc_status === "fail" && <span className="text-red-600 font-medium">FAIL</span>}
                    {s.qc_status === "warning" && <span className="text-yellow-600">Warning</span>}
                    {s.qc_status === "pass" && <span className="text-green-600">Pass</span>}
                    {!s.qc_status && NOT_SET}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-4 flex justify-between">
            <button onClick={() => setStep(1)} className="border px-6 py-2 rounded-md text-sm">Back</button>
            <button onClick={() => setStep(3)} disabled={selectedSampleIds.length === 0} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50">Next</button>
          </div>
        </Card>
      )}

      {/* Step 3: Configure Parameters */}
      {step === 3 && (
        <Card>
          <h2 className="text-lg font-semibold mb-4">Configure Parameters</h2>
          {detectedProtocol && (
            <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded text-sm">
              Protocol auto-detected as <span className="font-semibold">{detectedProtocol}</span> from sample chemistry version.
            </div>
          )}
          {/* Explains 10x Chromium barcode/UMI layout, so it belongs only to a
              pipeline that actually takes a 10x protocol. It used to render for
              every pipeline, which put a scRNA-seq primer on top of sarek. */}
          {pipelineAcceptsProtocol(pipeline.parameter_schema, detectedProtocol) && <ProtocolInfo />}
          <SamplesheetInputs
            specs={pipeline.samplesheet_inputs || []}
            values={userParams}
            onChange={setUserParams}
          />
          {/* Shown here as well as on Review: a user who cannot run this at all
              should not spend time filling in parameters first. */}
          <LaunchBlockedNotice preflight={preflight} />
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
        </Card>
      )}

      {/* Step 4: Review & Launch */}
      {step === 4 && (
        <Card className="max-w-2xl">
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
                  <span className="text-gray-500">All defaults</span>
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
          <LaunchBlockedNotice preflight={preflight} />
          <div className="flex justify-between">
            <button onClick={() => setStep(3)} className="border px-6 py-2 rounded-md text-sm">Back</button>
            <button
              onClick={() => handleLaunch()}
              disabled={launching || preflight?.can_launch === false}
              className="bg-green-600 text-white px-8 py-2 rounded-md text-sm hover:bg-green-700 disabled:opacity-50"
            >
              {launching ? "Launching..." : "Launch Pipeline"}
            </button>
          </div>
        </Card>
      )}
      </>
      )}
    </main>
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

