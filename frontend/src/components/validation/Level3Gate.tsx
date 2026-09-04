"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { type Arm, type ManifestSample, SampleManifestPicker, sampleId } from "./SampleManifestPicker";

// Shapes mirror the backend plan surface (ReproductionPlanResponse.differential_design / finding_claim).
export interface Contrast {
  // The cutoffs THIS contrast was reported at. A paper states them per finding: DEGs at a |log2FC|
  // and an adjusted p, differential binding usually on the adjusted p alone.
  assay?: string | null;
  thresholds?: { log2fc: number | null; padj: number | null } | null;
  name?: string | null;
  test_condition?: string | null;
  reference_condition?: string | null;
  test_samples: string[];
  reference_samples: string[];
  // Optional matched-pairs design: {sample_id: subject/block label}. Empty for the default unpaired
  // design. When present the DE run models `~ subject + condition` (ADR-069 item #2).
  subjects?: Record<string, string> | null;
}

export interface DifferentialDesign {
  // Which contrast this run reproduces, and who decided. A paper reports one per finding across
  // every assay it ran; the plan runs one pipeline. Absent on plans written before this existed.
  selected_contrast?: {
    contrast_index: number | null;
    decided_by: string;
    model?: string | null;
    confidence?: number | null;
    reason?: string | null;
  } | null;
  contrasts: Contrast[];
  thresholds: { log2fc: number | null; padj: number | null };
}

export interface FindingClaim {
  kind: string;
  namespace: string;
  confirmed: boolean;
  source_locator?: string | null;
  thresholds?: { log2fc: number | null; padj: number | null };
  finding_set: {
    n_sig: number;
    n_up: number;
    n_down: number;
    namespace: string;
    parse_notes: string[];
    entities: Array<{ id: string; direction?: string | null }>;
  };
  // Set when the alias list could not identify the table's columns. Carries the header and the
  // roles still to fill, which is a question a scientist can answer; "could not locate
  // chrom/start/end columns" is not.
  needs_column_mapping?: { header: string[]; roles: string[] } | null;
  // What was decided, once something resolved them. `decided_by` is "model" in autonomous mode or
  // "human" when the picker below posted it.
  column_mapping?: {
    columns: Record<string, string>;
    decided_by: string;
    model?: string | null;
    confidence?: number | null;
    reason?: string | null;
  } | null;
}

function parseList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function numOrNull(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isNaN(n) ? null : n;
}

// Matched-pairs input: the human enters one `sample=subject` per line (or comma-separated). Parse to a
// {sample: label} map; format the reverse for pre-filling an already-saved pairing.
function parseSubjects(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of s.split(/[\n,]/)) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    const key = part.slice(0, eq).trim();
    const label = part.slice(eq + 1).trim();
    if (key && label) out[key] = label;
  }
  return out;
}

function formatSubjects(m?: Record<string, string> | null): string {
  if (!m) return "";
  return Object.entries(m)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

const norm = (s?: string | null) => (s ?? "").trim().toLowerCase();

const KIND_LABELS: Record<string, string> = {
  gene: "Gene set (RNA-seq / scRNA-seq DE)",
  interval: "Interval set (ATAC/ChIP DA)",
};

// Pre-group the fetched samples so the scientist confirms rather than constructs: an accession the
// extractor already placed in an arm wins; otherwise a condition that matches the extractor's
// test/reference condition text places it; otherwise it starts excluded for the human to assign.
function seedAssignments(samples: ManifestSample[], design?: DifferentialDesign | null): Record<string, Arm> {
  const primary = design?.contrasts?.[0];
  const testSet = new Set((primary?.test_samples ?? []).map(norm));
  const refSet = new Set((primary?.reference_samples ?? []).map(norm));
  const testCond = norm(primary?.test_condition);
  const refCond = norm(primary?.reference_condition);
  const out: Record<string, Arm> = {};
  for (const s of samples) {
    const id = sampleId(s);
    if (!id) continue;
    const keys = [id, s.run_accession, s.sample_accession].map(norm);
    const cond = norm(s.condition);
    if (keys.some((k) => k && testSet.has(k))) out[id] = "test";
    else if (keys.some((k) => k && refSet.has(k))) out[id] = "reference";
    else if (testCond && cond.includes(testCond)) out[id] = "test";
    else if (refCond && cond.includes(refCond)) out[id] = "reference";
    else out[id] = "exclude";
  }
  return out;
}

/**
 * C1-gate Level-3 controls (ADR-069 / spec-08). At `plan_ready`, before spending compute, the human
 * (1) ratifies/corrects the paper's differential design (the extractor's sample labels rarely match
 * the analysis matrix's column names) and (2) confirms the paper's deposited ground-truth result set
 * by pasting its DEG/DA table. Both are optional: a paper with no differential finding stays Level-2.
 */
export function Level3Gate({
  studyId,
  design,
  claim,
  supportedFindingKinds,
  onChanged,
}: {
  studyId: number;
  design?: DifferentialDesign | null;
  claim?: FindingClaim | null;
  // Which finding kinds this plan's pipeline actually has a Level-3 route for, computed server-side
  // from the wiring. Undefined on a response rendered before the field existed, which degrades to
  // offering both rather than to an empty gate.
  supportedFindingKinds?: string[] | null;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const contrasts = design?.contrasts ?? [];
  const chosen = design?.selected_contrast?.contrast_index;
  const [contrastIndex, setContrastIndex] = useState(
    typeof chosen === "number" && chosen >= 0 && chosen < contrasts.length ? chosen : 0,
  );
  const primary: Contrast = contrasts[contrastIndex] ?? { test_samples: [], reference_samples: [] };
  // A contrast's own cutoffs, falling back to the paper-level pair for plans written before
  // thresholds moved onto contrasts.
  const th = primary.thresholds ?? design?.thresholds;

  const [name, setName] = useState(primary.name ?? "");
  const [testCondition, setTestCondition] = useState(primary.test_condition ?? "");
  const [refCondition, setRefCondition] = useState(primary.reference_condition ?? "");
  const [testSamples, setTestSamples] = useState((primary.test_samples ?? []).join(", "));
  const [refSamples, setRefSamples] = useState((primary.reference_samples ?? []).join(", "));
  const [subjectsText, setSubjectsText] = useState(formatSubjects(primary.subjects));
  const [lfc, setLfc] = useState(th?.log2fc != null ? String(th.log2fc) : "");
  const [padj, setPadj] = useState(th?.padj != null ? String(th.padj) : "");

  // A pipeline with no Level-3 route offers nothing; one with a single route offers only that.
  const kinds = supportedFindingKinds ?? ["gene", "interval"];
  const [kind, setKind] = useState<"gene" | "interval">(
    (claim?.kind as "gene" | "interval") ?? ((kinds[0] as "gene" | "interval") ?? "gene"),
  );
  const [tableText, setTableText] = useState("");
  const [source, setSource] = useState(claim?.source_locator ?? "");

  const [busy, setBusy] = useState<null | "design" | "claim" | "fetch">(null);
  // Role -> column name, filled in by the picker when the header was not recognised.
  const [columnMap, setColumnMap] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [fetchMsg, setFetchMsg] = useState<string | null>(null);

  // Sample picker (recognition over accession typing). When the study's real sample manifest resolves,
  // it replaces the blind free-text sample inputs; on failure the gate degrades to free text.
  const [manifest, setManifest] = useState<ManifestSample[]>([]);
  const [manifestReason, setManifestReason] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<Record<string, Arm>>({});
  const [pickerSubjects, setPickerSubjects] = useState<Record<string, string>>({});
  const [manualIds, setManualIds] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<{ samples?: ManifestSample[]; unavailable_reason?: string | null }>(
          `/api/validation-studies/${studyId}/sample-manifest`,
        );
        if (cancelled) return;
        const samples = res.samples ?? [];
        setManifest(samples);
        setManifestReason(res.unavailable_reason ?? null);
        if (samples.length > 0) setAssignments(seedAssignments(samples, design));
      } catch (e) {
        if (!cancelled) setManifestReason(e instanceof Error ? e.message : "Could not load the sample list.");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyId]);

  if (!canAccess("lit_validation", "approve")) return null;

  const usingPicker = manifest.length > 0;
  const base = `/api/validation-studies/${studyId}`;
  const input = "w-full rounded border border-gray-300 px-2 py-1 text-sm";
  const btn = "rounded px-4 py-2 text-sm font-medium disabled:opacity-50";

  async function run(which: "design" | "claim", action: () => Promise<unknown>) {
    setBusy(which);
    setError(null);
    try {
      onChanged(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  }

  function pickerArms(): { test: string[]; reference: string[]; subjects: Record<string, string> } {
    const ids = [...manifest.map(sampleId).filter(Boolean), ...manualIds];
    const test = ids.filter((id) => assignments[id] === "test");
    const reference = ids.filter((id) => assignments[id] === "reference");
    const subjects: Record<string, string> = {};
    for (const id of ids) {
      const label = (pickerSubjects[id] ?? "").trim();
      if (label && assignments[id] !== "exclude") subjects[id] = label;
    }
    return { test, reference, subjects };
  }

  function saveDesign() {
    const picked = usingPicker ? pickerArms() : null;
    const payload = {
      contrasts: [
        {
          name: name.trim() || null,
          test_condition: testCondition.trim() || null,
          reference_condition: refCondition.trim() || null,
          test_samples: picked ? picked.test : parseList(testSamples),
          reference_samples: picked ? picked.reference : parseList(refSamples),
          subjects: picked ? picked.subjects : parseSubjects(subjectsText),
        },
      ],
      thresholds: { log2fc: numOrNull(lfc), padj: numOrNull(padj) },
      // Which contrast of the original list this edit is of. The server saves one contrast, so
      // without this it cannot tell a ratified model choice from one a person overrode.
      selected_contrast_index: contrastIndex,
    };
    return run("design", () => api.put(`${base}/differential-design`, payload));
  }

  function chooseContrast(i: number) {
    const c = contrasts[i];
    const t = c?.thresholds ?? design?.thresholds;
    setContrastIndex(i);
    setName(c?.name ?? "");
    setTestCondition(c?.test_condition ?? "");
    setRefCondition(c?.reference_condition ?? "");
    setTestSamples((c?.test_samples ?? []).join(", "));
    setRefSamples((c?.reference_samples ?? []).join(", "));
    setLfc(t?.log2fc != null ? String(t.log2fc) : "");
    setPadj(t?.padj != null ? String(t.padj) : "");
  }

  function confirmSet(map?: Record<string, string>) {
    const chosen = map ?? (Object.keys(columnMap).length ? columnMap : undefined);
    return run("claim", () =>
      api.post(`${base}/finding-set`, {
        kind,
        table_text: tableText,
        source_locator: source.trim() || null,
        ...(chosen ? { column_map: chosen } : {}),
      }),
    );
  }

  async function autoFetch() {
    setBusy("fetch");
    setError(null);
    setFetchMsg(null);
    try {
      const res = await api.get<{
        candidates: Array<{ filename: string; source: string; n_sig: number; table_text?: string }>;
      }>(`${base}/finding-set/candidates?kind=${kind}`);
      const cands = res.candidates ?? [];
      if (cands.length === 0) {
        setFetchMsg(
          "No deposited result table found in GEO (usual case). Paste the paper's table below; it is typically in the journal supplementary.",
        );
        return;
      }
      const top = cands[0];
      if (top.table_text) setTableText(top.table_text);
      if (top.filename) setSource(top.filename);
      setFetchMsg(
        `Found ${cands.length} candidate(s); pre-filled from ${top.filename} (${top.n_sig} significant). Review, then confirm.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Auto-fetch failed.");
    } finally {
      setBusy(null);
    }
  }

  const fs = claim?.finding_set;

  return (
    <div className="space-y-5 rounded border border-gray-200 bg-gray-50 p-4">
      <div>
        <h3 className="text-sm font-semibold text-gray-800">Level 3: reproduce the paper&apos;s finding</h3>
        <p className="mt-1 text-xs text-gray-500">
          Optional. Confirm the paper&apos;s differential design and its deposited result set before approving, and
          the validation will check whether the reported finding reproduces. Leave blank for a QC-only paper.
        </p>
      </div>

      {/* Differential design */}
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Differential design (primary contrast)</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {contrasts.length > 1 && (
          <div className="mb-2 space-y-1">
            <label className="block text-xs text-gray-700">
              Which contrast does this run reproduce?
              <select
                aria-label="Which contrast"
                className={input}
                value={String(contrastIndex)}
                onChange={(e) => chooseContrast(Number(e.target.value))}
              >
                {contrasts.map((c, i) => (
                  <option key={i} value={String(i)}>
                    {`${c.name ?? `contrast ${i + 1}`}${c.assay ? ` (${c.assay})` : ""}`}
                  </option>
                ))}
              </select>
            </label>
            {design?.selected_contrast?.reason && (
              <p className="text-xs text-gray-600">
                {`Chosen by ${
                  design.selected_contrast.decided_by === "model"
                    ? design.selected_contrast.model
                    : design.selected_contrast.decided_by
                }: ${design.selected_contrast.reason}`}
                {design.selected_contrast.confidence != null
                  ? ` (${design.selected_contrast.confidence.toFixed(2)})`
                  : ""}
              </p>
            )}
          </div>
        )}

          <label className="text-xs text-gray-600">
            Contrast name
            <input className={input} aria-label="Contrast name" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-gray-600">
              |log2FC| threshold
              <input className={input} aria-label="log2FC threshold" value={lfc} onChange={(e) => setLfc(e.target.value)} />
            </label>
            <label className="text-xs text-gray-600">
              padj threshold
              <input className={input} aria-label="padj threshold" value={padj} onChange={(e) => setPadj(e.target.value)} />
            </label>
          </div>
          <label className="text-xs text-gray-600">
            Test condition
            <input
              className={input}
              aria-label="Test condition"
              value={testCondition}
              onChange={(e) => setTestCondition(e.target.value)}
            />
          </label>
          <label className="text-xs text-gray-600">
            Reference condition
            <input
              className={input}
              aria-label="Reference condition"
              value={refCondition}
              onChange={(e) => setRefCondition(e.target.value)}
            />
          </label>
          {!usingPicker && (
            <>
              <label className="text-xs text-gray-600">
                Test samples (matrix columns, comma-separated)
                <input
                  className={input}
                  aria-label="Test samples"
                  value={testSamples}
                  onChange={(e) => setTestSamples(e.target.value)}
                />
              </label>
              <label className="text-xs text-gray-600">
                Reference samples (comma-separated)
                <input
                  className={input}
                  aria-label="Reference samples"
                  value={refSamples}
                  onChange={(e) => setRefSamples(e.target.value)}
                />
              </label>
              <label className="text-xs text-gray-600 sm:col-span-2">
                Subject / donor pairing (optional, matched-pairs)
                <textarea
                  className={`${input} font-mono`}
                  aria-label="Subject pairing"
                  rows={3}
                  value={subjectsText}
                  onChange={(e) => setSubjectsText(e.target.value)}
                  placeholder={"SRX...=donorA\nSRX...=donorB\n..."}
                />
                <span className="mt-1 block text-[11px] leading-snug text-gray-500">
                  One <code>sample=subject</code> per line. Each subject must appear in BOTH arms; the run
                  then models <code>~ subject + condition</code> (paired), cancelling donor-to-donor
                  baseline variance. Leave blank for an unpaired design.
                </span>
              </label>
            </>
          )}
        </div>

        {usingPicker ? (
          <SampleManifestPicker
            samples={manifest}
            manualIds={manualIds}
            assignments={assignments}
            subjects={pickerSubjects}
            onAssign={(id, arm) => setAssignments((a) => ({ ...a, [id]: arm }))}
            onSubject={(id, subject) => setPickerSubjects((s) => ({ ...s, [id]: subject }))}
            onManualAdd={(id) => {
              setManualIds((ids) => (ids.includes(id) ? ids : [...ids, id]));
              setAssignments((a) => (a[id] ? a : { ...a, [id]: "test" }));
            }}
          />
        ) : (
          manifestReason && (
            <p className="text-[11px] leading-snug text-gray-500">
              Sample list unavailable: {manifestReason} Enter the matrix column ids manually above.
            </p>
          )
        )}
        <button
          className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
          disabled={busy !== null}
          onClick={saveDesign}
        >
          {busy === "design" ? "Saving..." : "Save design"}
        </button>
      </div>

      {/* Ground-truth result set. Offered only for the finding kinds this pipeline can reproduce:
          confirming a set with no route to run it costs the scientist their time and then hours of
          compute to reach an answer that was never available. Say so rather than hiding the section,
          which would leave them hunting for a control that vanished. */}
      {kinds.length === 0 ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Paper&apos;s ground-truth result set</p>
          <p className="rounded border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600">
            This study&apos;s pipeline cannot reproduce a reported finding, so there is no result set to
            confirm. The run still produces quality-control evidence, and the validation is assessed on
            that alone.
          </p>
        </div>
      ) : (
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Paper&apos;s ground-truth result set</p>
        {fs && (
          <div className="rounded border border-green-200 bg-green-50 px-3 py-2 text-xs text-gray-700">
            <span className="font-semibold text-green-700">Confirmed</span> {claim?.kind} set:{" "}
            <span className="font-semibold">{fs.n_sig}</span> significant ({fs.n_up} up / {fs.n_down} down), namespace{" "}
            {fs.namespace}
            {fs.parse_notes.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-amber-700">
                {fs.parse_notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-gray-600">
            Assay
            <select
              className={input}
              aria-label="Result set kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as "gene" | "interval")}
            >
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k] ?? k}
                </option>
              ))}
            </select>
          </label>
          <label className="flex-1 text-xs text-gray-600">
            Source (e.g. journal Table S3)
            <input className={input} aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)} />
          </label>
        </div>
        <label className="block text-xs text-gray-600">
          Paste the paper&apos;s deposited result table (DEG list / DA peak list; csv or tsv)
          <textarea
            className={`${input} font-mono`}
            aria-label="Result table"
            rows={5}
            value={tableText}
            onChange={(e) => setTableText(e.target.value)}
            placeholder={"gene,log2FoldChange,padj\nA1BG,2.5,0.001\n..."}
          />
        </label>
        {fetchMsg && <p className="text-xs text-gray-600">{fetchMsg}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button
            className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
            disabled={busy !== null || tableText.trim() === ""}
            onClick={() => confirmSet()}
          >
            {busy === "claim" ? "Parsing..." : "Confirm ground-truth set"}
          </button>
          <button
            className={`${btn} border border-gray-300 text-gray-700 hover:bg-gray-100`}
            disabled={busy !== null}
            onClick={autoFetch}
            title="Best-effort fetch of the paper's deposited table from GEO supplementary files"
          >
            {busy === "fetch" ? "Fetching..." : "Try GEO auto-fetch"}
          </button>
        </div>

        {claim?.needs_column_mapping && (
          <div className="rounded border border-amber-200 bg-amber-50 p-3 space-y-2">
            <p className="text-sm font-medium text-amber-900">
              Which column is which?
            </p>
            <p className="text-xs text-amber-800">
              This table&apos;s columns are not ones bioAF recognises by name, so nothing could be
              read from it. Point each role at a column and confirm again. Leave a role blank if the
              table does not have it.
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {claim.needs_column_mapping.roles.map((role) => (
                <label key={role} className="text-xs text-amber-900">
                  {`${role} column`}
                  <select
                    aria-label={`${role} column`}
                    className="mt-0.5 w-full rounded border border-amber-300 bg-white px-2 py-1 text-sm text-gray-900"
                    value={columnMap[role] ?? ""}
                    onChange={(e) =>
                      setColumnMap((m) => {
                        const next = { ...m };
                        if (e.target.value) next[role] = e.target.value;
                        else delete next[role];
                        return next;
                      })
                    }
                  >
                    <option value="">not in this table</option>
                    {claim.needs_column_mapping!.header
                      .filter((h) => h !== "")
                      .map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                  </select>
                </label>
              ))}
            </div>
            <Button
              size="sm"
              disabled={busy !== null || Object.keys(columnMap).length === 0}
              onClick={() => confirmSet(columnMap)}
            >
              {busy === "claim" ? "Parsing..." : "Use these columns"}
            </Button>
          </div>
        )}

        {claim?.column_mapping && (
          <p className="text-xs text-gray-600">
            {`Columns resolved by ${claim.column_mapping.decided_by === "model" ? claim.column_mapping.model : "you"}: `}
            {Object.entries(claim.column_mapping.columns)
              .map(([role, col]) => `${role} = ${col}`)
              .join(", ")}
            {claim.column_mapping.confidence != null
              ? ` (${claim.column_mapping.confidence.toFixed(2)})`
              : ""}
          </p>
        )}
      </div>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  );
}
