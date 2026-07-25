"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

// Shapes mirror the backend plan surface (ReproductionPlanResponse.differential_design / finding_claim).
export interface Contrast {
  name?: string | null;
  test_condition?: string | null;
  reference_condition?: string | null;
  test_samples: string[];
  reference_samples: string[];
}

export interface DifferentialDesign {
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
  onChanged,
}: {
  studyId: number;
  design?: DifferentialDesign | null;
  claim?: FindingClaim | null;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const primary: Contrast = design?.contrasts?.[0] ?? { test_samples: [], reference_samples: [] };

  const [name, setName] = useState(primary.name ?? "");
  const [testCondition, setTestCondition] = useState(primary.test_condition ?? "");
  const [refCondition, setRefCondition] = useState(primary.reference_condition ?? "");
  const [testSamples, setTestSamples] = useState((primary.test_samples ?? []).join(", "));
  const [refSamples, setRefSamples] = useState((primary.reference_samples ?? []).join(", "));
  const [lfc, setLfc] = useState(design?.thresholds?.log2fc != null ? String(design.thresholds.log2fc) : "");
  const [padj, setPadj] = useState(design?.thresholds?.padj != null ? String(design.thresholds.padj) : "");

  const [kind, setKind] = useState<"gene" | "interval">((claim?.kind as "gene" | "interval") ?? "gene");
  const [tableText, setTableText] = useState("");
  const [source, setSource] = useState(claim?.source_locator ?? "");

  const [busy, setBusy] = useState<null | "design" | "claim">(null);
  const [error, setError] = useState<string | null>(null);

  if (!canAccess("lit_validation", "approve")) return null;

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

  function saveDesign() {
    const payload = {
      contrasts: [
        {
          name: name.trim() || null,
          test_condition: testCondition.trim() || null,
          reference_condition: refCondition.trim() || null,
          test_samples: parseList(testSamples),
          reference_samples: parseList(refSamples),
        },
      ],
      thresholds: { log2fc: numOrNull(lfc), padj: numOrNull(padj) },
    };
    return run("design", () => api.put(`${base}/differential-design`, payload));
  }

  function confirmSet() {
    return run("claim", () =>
      api.post(`${base}/finding-set`, {
        kind,
        table_text: tableText,
        source_locator: source.trim() || null,
      }),
    );
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
        </div>
        <button
          className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
          disabled={busy !== null}
          onClick={saveDesign}
        >
          {busy === "design" ? "Saving..." : "Save design"}
        </button>
      </div>

      {/* Ground-truth result set */}
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
              <option value="gene">Gene set (RNA-seq DE)</option>
              <option value="interval">Interval set (ATAC/ChIP DA)</option>
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
        <button
          className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
          disabled={busy !== null || tableText.trim() === ""}
          onClick={confirmSet}
        >
          {busy === "claim" ? "Parsing..." : "Confirm ground-truth set"}
        </button>
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  );
}
