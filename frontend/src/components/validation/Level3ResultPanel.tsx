"use client";

// F1' surfacing (ADR-069 / spec-08): render the Level-3 finding-concordance evidence so the scientist
// sees WHY the verdict speaks (or does not speak) to the paper's reported finding: our reproduced set
// vs the paper's set, the directional overlap, and the enrichment significance.

export interface Concordance {
  kind: string;
  verdict: string; // "agree" | "diverge" | "not_computed"
  paper_n: number;
  our_n: number;
  overlap: number;
  concordant: number;
  directional_overlap_frac: number;
  enrichment_p: number;
  notes: string[];
}

export interface Level3Result {
  concordance: Concordance;
  our_finding_set?: { n_sig: number; namespace: string } | null;
}

function fmtP(p: number): string {
  if (p === 0) return "0";
  if (p < 1e-3 || p >= 1e4) return p.toExponential(1);
  return String(Number(p.toPrecision(3)));
}

const VERDICT: Record<string, { label: string; cls: string }> = {
  agree: { label: "Finding reproduced", cls: "bg-green-100 text-green-800" },
  diverge: { label: "Finding did not reproduce", cls: "bg-red-100 text-red-800" },
  not_computed: { label: "Not computed", cls: "bg-gray-100 text-gray-700" },
};

export function Level3ResultPanel({
  result,
  contrast,
}: {
  result?: Level3Result | null;
  contrast?: string | null;
}) {
  const c = result?.concordance;
  if (!c) return null;

  const v = VERDICT[c.verdict] ?? { label: c.verdict, cls: "bg-gray-100 text-gray-700" };
  const pct = Math.round((c.directional_overlap_frac || 0) * 100);

  return (
    <div className="rounded border border-gray-200 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-gray-800">Level 3: differential finding concordance</h3>
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${v.cls}`}>{v.label}</span>
        {contrast && <span className="text-xs text-gray-500">contrast: {contrast}</span>}
      </div>

      {c.verdict !== "not_computed" && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <Stat label="Paper's set" value={c.paper_n} />
          <Stat label="Our reproduced set" value={c.our_n} />
          <Stat label="Directional overlap" value={`${pct}% (${c.concordant}/${c.paper_n})`} />
          <Stat label="Enrichment p" value={fmtP(c.enrichment_p)} />
        </dl>
      )}

      {c.notes.length > 0 && (
        <ul className="mt-3 list-inside list-disc text-xs text-gray-600">
          {c.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="font-semibold text-gray-800">{value}</dd>
    </div>
  );
}
