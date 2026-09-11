"use client";

import { useState } from "react";
import { PROVISIONAL_NOTE } from "@/lib/validationReport";

/**
 * plan_7 step 14 at the C1 gate: the cheap checks, in front of the person authorising the spend.
 *
 * "The deposit holds 5 bigwigs and the paper describes 21 RNA-seq samples" belongs before an
 * approval, not in the report afterwards. That is the whole reason these run at read time.
 *
 * **Deterministic blocks, judgment advises.** The species check is a string comparison and it
 * invalidates every number downstream, so it refuses approval and offers a deliberate override with
 * a stated reason. The other three are model judgments about a paper's sufficiency; they are marked
 * advisory and never stop a run, because a panel that presents an opinion as a blocker teaches
 * people to click through blockers.
 */

export interface PrecomputeCheck {
  check: string;
  verdict: string;
  detail: string;
  blocking: boolean;
  decided_by: string;
  model: string | null;
  reason: string;
  confidence: number;
  // change_7.3 section 6: what the verdict rests on. A judgment from the prose is provisional until
  // something inspected settles it, and must never render as a settled "Yes".
  basis?: "paper_text" | "inspected_evidence" | null;
}

// A check recorded before `basis` existed is read the way the backend reads it: a model's judgment
// rests on the paper's text, a measurement that answered rests on the evidence it compared.
function isProvisional(check: PrecomputeCheck): boolean {
  if (check.basis) return check.basis === "paper_text";
  return check.decided_by === "model";
}

export interface PrecomputeChecks {
  species_matches?: PrecomputeCheck;
  sample_data_matches_paper?: PrecomputeCheck;
  methods_detailed_enough?: PrecomputeCheck;
  samples_described_enough?: PrecomputeCheck;
}

export interface SpeciesOverride {
  reason: string;
  by_user_id: number;
  at: string;
}

const LABELS: { key: keyof PrecomputeChecks; label: string }[] = [
  { key: "species_matches", label: "Species matches the deposit" },
  { key: "sample_data_matches_paper", label: "Sample data matches the paper" },
  { key: "methods_detailed_enough", label: "Methods described in enough detail" },
  { key: "samples_described_enough", label: "Samples described in enough detail" },
];

const VERDICT_LABEL: Record<string, string> = {
  ok: "Yes",
  mismatch: "No",
  unknown: "Unknown",
};

const VERDICT_CLASS: Record<string, string> = {
  ok: "bg-emerald-50 text-emerald-700",
  mismatch: "bg-amber-50 text-amber-700",
  unknown: "bg-gray-100 text-gray-600",
};

export function PrecomputeChecksPanel({
  checks,
  override,
  onOverride,
}: {
  checks: PrecomputeChecks | null | undefined;
  override?: SpeciesOverride | null;
  onOverride: (reason: string) => void;
}) {
  const [showOverride, setShowOverride] = useState(false);
  const [reason, setReason] = useState("");

  if (!checks) return null;
  const species = checks.species_matches;
  const blocked = !!species?.blocking && !override;

  return (
    <div className="space-y-2">
      <ul className="divide-y divide-gray-100 border-y border-gray-100">
        {LABELS.map(({ key, label }) => {
          const check = checks[key];
          if (!check) return null;
          return (
            <li key={key} className="flex flex-wrap items-baseline gap-x-2 py-1.5 text-sm">
              <span className="text-gray-800">{label}</span>
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                  isProvisional(check)
                    ? "bg-gray-100 text-gray-600"
                    : (VERDICT_CLASS[check.verdict] ?? "bg-gray-100 text-gray-600")
                }`}
              >
                {VERDICT_LABEL[check.verdict] ?? check.verdict}
                {isProvisional(check) && " (provisional)"}
              </span>
              {isProvisional(check) && <span className="text-xs text-gray-500">{PROVISIONAL_NOTE}</span>}
              {!check.blocking && check.verdict === "mismatch" && (
                <span className="text-xs text-gray-500">advisory</span>
              )}
              {check.decided_by === "model" && check.model && (
                <span className="font-mono text-xs text-gray-500">{check.model}</span>
              )}
              {check.detail && <span className="basis-full text-xs text-gray-500">{check.detail}</span>}
            </li>
          );
        })}
      </ul>

      {blocked && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">Approval is blocked: the paper and the deposit name different organisms.</p>
          <p className="mt-1 text-xs">
            Aligning one to the other&apos;s reference genome produces numbers that look valid and are
            not, after hours of compute.
          </p>
          {!showOverride ? (
            <button
              type="button"
              className="mt-2 rounded border border-amber-400 px-3 py-1 text-xs font-medium"
              onClick={() => setShowOverride(true)}
            >
              Run it anyway
            </button>
          ) : (
            <div className="mt-2 space-y-2">
              <label className="block text-xs" htmlFor="species-override-reason">
                Why should the deposit&apos;s declared organism be overruled? The reason goes on the
                record, so a verdict that later diverges can be argued against this choice.
              </label>
              <textarea
                id="species-override-reason"
                className="w-full rounded border border-amber-300 p-2 text-xs"
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <button
                type="button"
                className="rounded bg-amber-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-50"
                disabled={reason.trim().length < 3}
                onClick={() => onOverride(reason.trim())}
              >
                Record and continue
              </button>
            </div>
          )}
        </div>
      )}

      {override && (
        <p className="text-xs text-gray-600">
          Species mismatch overridden: {override.reason}
        </p>
      )}
    </div>
  );
}
