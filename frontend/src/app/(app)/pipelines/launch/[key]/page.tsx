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
import { Button } from "@/components/ui/Button";
import { SamplesheetInputs } from "@/components/pipelines/SamplesheetInputs";
import { ParameterForm } from "@/components/pipelines/ParameterForm";
import { detectProtocol, pipelineAcceptsProtocol } from "@/components/pipelines/protocolDetection";
import { LaunchBlockedNotice } from "@/components/pipelines/LaunchBlockedNotice";
import { PerSampleValueGrid, type PerSampleValues } from "@/components/pipelines/PerSampleValueGrid";
import { SamplesheetColumnEditor } from "@/components/pipelines/SamplesheetColumnEditor";
import { SamplesheetReview } from "@/components/pipelines/SamplesheetReview";
import type {
  PipelineCatalog,
  Experiment,
  ExperimentListResponse,
  SampleBrief,
  PipelineRunLaunchRequest,
  PipelineRun,
  PipelineRunPreflight,
  DeclaredColumn,
} from "@/lib/types";

/** The wizard's steps, named rather than numbered. "Values" appears only when
 *  the pipeline declares columns bioAF may not fill, so the position of every
 *  later step depends on the pipeline: a number would mean two different things
 *  on two different launches. */
type StepKey = "experiment" | "samples" | "values" | "parameters" | "review";

const STEP_LABELS: Record<StepKey, string> = {
  experiment: "Experiment",
  samples: "Samples",
  values: "Values",
  parameters: "Parameters",
  review: "Review",
};

const SAVE_SCOPES: { value: string; label: string }[] = [
  { value: "experiment", label: "this experiment" },
  { value: "project", label: "this project" },
  { value: "organization", label: "the whole organisation" },
];

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

  const [step, setStep] = useState<StepKey>("experiment");
  const [selectedExperimentId, setSelectedExperimentId] = useState<number | null>(
    preselectedExperimentId ? Number(preselectedExperimentId) : null,
  );
  const [selectedSampleIds, setSelectedSampleIds] = useState<number[]>([]);
  const [userParams, setUserParams] = useState<Record<string, unknown>>({});
  // What the scientist stated per sample, keyed by sample id. Sent to the
  // preflight as well as the launch, so the block clears as they answer rather
  // than at the end.
  const [sampleValues, setSampleValues] = useState<PerSampleValues>({});
  const [saveScope, setSaveScope] = useState("experiment");
  const [savingDesign, setSavingDesign] = useState(false);
  // The columns declared for a pipeline that publishes no contract. Null until
  // the preflight has said what is in force, so an empty editor is never
  // mistaken for a declaration of no columns.
  const [declaredColumns, setDeclaredColumns] = useState<DeclaredColumn[] | null>(null);

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
          sample_values: sampleValues,
          // Only once the editor holds something. Null is "we have not been told
          // yet", which is the state of the very first preflight, and it must
          // reach the server as SILENCE so the saved design stands. Sending []
          // there would preview a generic sheet for an experiment that has a
          // declaration saved, and the editor would then adopt that emptiness.
          ...(declaredColumns !== null ? { columns: declaredColumns } : {}),
        });
        if (cancelled) return;
        setPreflight(result);
        // Adopted once, and never again while the scientist is editing: a
        // later preflight must not overwrite the column they are part way
        // through changing.
        setDeclaredColumns((held) => held ?? result.prefill?.columns ?? []);
      } catch (e) {
        // A preflight that cannot run must not block a launch that would work.
        logError("checking whether this pipeline can run", e);
        if (!cancelled) setPreflight(null);
      }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // `declaredColumns` is here so that editing a column re-previews the sheet.
  // Without it the Values step could change the declaration and the Review step
  // would still show the sheet from before the edit. It cannot loop: the adopt
  // below returns the held array unchanged once it is non-null.
  }, [selectedExperimentId, pipeline, selectedSampleIds, userParams, sampleValues, declaredColumns]);

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
        sample_values: sampleValues,
        drop_samples_without_files: dropSamplesWithoutFiles,
        // What the review step just showed. Saving is a separate, deliberate
        // act (design 02 section 4) and this does not perform it: the columns
        // bind THIS run only, and the next one reads whatever is saved.
        ...(declaredColumns !== null ? { columns: declaredColumns } : {}),
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

  /** A cell corrected on the review step. It becomes a stated value for this
   *  run, which is what the preview then regenerates from, so the table always
   *  shows the sheet that would actually be submitted. */
  function correctCell(sampleId: number, column: string, value: string) {
    setSampleValues((prev) => {
      const row = { ...(prev[String(sampleId)] ?? {}) };
      if (value) row[column] = value;
      else delete row[column];
      const next = { ...prev, [String(sampleId)]: row };
      if (Object.keys(row).length === 0) delete next[String(sampleId)];
      return next;
    });
  }

  /** Saving the design is deliberate at every rung (design section 4). Nothing
   *  is promoted by launching, so a one-off accommodation never becomes what the
   *  next person inherits. */
  async function saveDesign() {
    if (!selectedExperimentId) return;
    setSavingDesign(true);
    try {
      await api.post("/api/samplesheet-mappings", {
        pipeline_key: pipelineKey,
        scope: saveScope,
        experiment_id: selectedExperimentId,
        project_id: experiments.find((e) => e.id === selectedExperimentId)?.project?.id ?? null,
        values: sampleValues,
        ...(preflight?.declaration?.declarable ? { columns: declaredColumns ?? [] } : {}),
      });
      toast.success("Saved for next time");
    } catch (e) {
      logError("saving these values for next time", e);
      toast.error(
        e instanceof ApiError && e.status === 403
          ? "Saving for the whole organisation needs an administrator."
          : "Could not save these values. The launch is unaffected.",
      );
    } finally {
      setSavingDesign(false);
    }
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

  // The Values step exists only when this pipeline declares columns bioAF may
  // not fill. Asking for nothing would be a step that always says "nothing to
  // do here", and hiding it when it IS needed strands the user on a Launch
  // button that never enables.
  const perSampleInputs = preflight?.per_sample_inputs ?? [];
  // A pipeline that publishes no contract has a Values step whatever it asks
  // for, because declaring the columns is the only way its sheet becomes
  // anything other than bioAF's standard three.
  const declarable = preflight?.declaration?.declarable ?? false;
  const steps: StepKey[] = [
    "experiment",
    "samples",
    ...(perSampleInputs.length > 0 || declarable ? (["values"] as StepKey[]) : []),
    "parameters",
    "review",
  ];
  const stepIndex = Math.max(0, steps.indexOf(step));
  const goNext = () => setStep(steps[Math.min(stepIndex + 1, steps.length - 1)]);
  const goBack = () => setStep(steps[Math.max(stepIndex - 1, 0)]);
  const selectedSamples = samples.filter((s) => selectedSampleIds.includes(s.id));

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
        {steps.map((key, index) => (
          <div key={key} className="flex items-center gap-2">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
              index === stepIndex ? "bg-bioaf-600 text-white" : index < stepIndex ? "bg-green-500 text-white" : "bg-gray-200 text-gray-500"
            }`}>{index + 1}</div>
            <span className="text-sm text-gray-500">{STEP_LABELS[key]}</span>
            {index < steps.length - 1 && <div className="w-8 h-px bg-gray-300" />}
          </div>
        ))}
      </div>

      {/* Step 1: Select Experiment */}
      {step === "experiment" && (
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
              onClick={goNext}
              disabled={!selectedExperimentId}
              className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
            >Next</button>
          </div>
        </Card>
      )}

      {/* Step 2: Select Samples */}
      {step === "samples" && (
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
            <button onClick={goBack} className="border px-6 py-2 rounded-md text-sm">Back</button>
            <button onClick={goNext} disabled={selectedSampleIds.length === 0} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50">Next</button>
          </div>
        </Card>
      )}

      {/* Step: the values this pipeline needs per sample, which bioAF may not
          guess. A dedicated step after sample selection keeps "which samples"
          separate from "what values", and it appears only when there is
          something to ask. */}
      {step === "values" && (
        <Card>
          <h2 className="text-lg font-semibold mb-4">Values for each sample</h2>
          {declarable && (
            <SamplesheetColumnEditor
              columns={declaredColumns ?? []}
              fileTypes={preflight?.declaration?.file_types ?? []}
              customFields={preflight?.declaration?.custom_fields ?? []}
              onChange={setDeclaredColumns}
            />
          )}
          <PerSampleValueGrid
            specs={perSampleInputs}
            samples={selectedSamples}
            values={sampleValues}
            onChange={setSampleValues}
            prefill={preflight?.prefill ?? null}
          />
          <LaunchBlockedNotice preflight={preflight} />
          <div className="flex items-center gap-2 mb-4 text-sm">
            <span className="text-gray-500">Save these values for</span>
            <select
              aria-label="Save these values for"
              value={saveScope}
              onChange={(e) => setSaveScope(e.target.value)}
              className="border rounded-md px-2 py-1 text-sm"
            >
              {SAVE_SCOPES.map((scope) => (
                <option key={scope.value} value={scope.value}>{scope.label}</option>
              ))}
            </select>
            <button
              onClick={saveDesign}
              disabled={
                savingDesign ||
                (Object.keys(sampleValues).length === 0 && (declaredColumns ?? []).length === 0)
              }
              className="border px-3 py-1 rounded-md text-sm hover:bg-gray-100 disabled:opacity-50"
            >
              {savingDesign ? "Saving..." : "Save for next time"}
            </button>
          </div>
          <div className="flex justify-between">
            <button onClick={goBack} className="border px-6 py-2 rounded-md text-sm">Back</button>
            <Button onClick={goNext}>Next</Button>
          </div>
        </Card>
      )}

      {/* Step 3: Configure Parameters */}
      {step === "parameters" && (
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
            <button onClick={goBack} className="border px-6 py-2 rounded-md text-sm">Back</button>
            <button onClick={goNext} className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700">Next</button>
          </div>
        </Card>
      )}

      {/* Step 4: Review & Launch */}
      {step === "review" && (
        <Card>
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
          {/* The sheet itself, not a summary of it. bioAF resolves a file column
              by matching the schema's own pattern, and a regex match is not
              proof of the right file, so this is the last place a wrong
              resolution can be caught. */}
          <SamplesheetReview preview={preflight?.samplesheet ?? null} onCorrect={correctCell} />
          <LaunchBlockedNotice preflight={preflight} />
          <div className="flex justify-between">
            <button onClick={goBack} className="border px-6 py-2 rounded-md text-sm">Back</button>
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

