import type { ClassificationResult } from "@/components/validation/ValidationEvidenceTable";
import { classificationLabel, classificationTone } from "@/lib/validationClassification";
import { statusBadgeClass } from "@/lib/statusStyles";

// The automatic classifier's (E2/E3/E4) suggested verdict + why. Rendered once the classifier has run
// (at comparing, or after a classified study). When the study is still at comparing this is a
// SUGGESTION a human ratifies via the Classify control; auto_finalize studies were closed on it.

export function ValidationVerdictPanel({
  result,
  level3Skipped,
  level3Failed,
}: {
  result: ClassificationResult | null | undefined;
  // The finding step was configured but could not run (no input file, no template, a contrast too
  // small to fit). Without this the only account of it was one line in a server log, so a scientist
  // who confirmed a ground-truth set and got `inconclusive` back could not learn it never ran.
  level3Skipped?: { reason?: string | null } | null;
  // The finding step ran and failed. The study keeps the Level-2 verdict it earned; this says which
  // stronger verdict was not available, and why.
  level3Failed?: { reason?: string | null } | null;
}) {
  if (!result || !result.classification) return null;

  const tone = classificationTone(result.classification);
  const cov = result.coverage ?? {};
  const attributionReasons = result.attribution?.reasons ?? [];

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-gray-700">
          {result.auto_finalize ? "Verdict" : "Suggested verdict"}
        </span>
        <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${statusBadgeClass("validationTone", tone)}`}>
          {classificationLabel(result.classification)}
        </span>
        {result.auto_finalize ? (
          <span className="text-xs text-gray-500">applied automatically (clean agreement)</span>
        ) : (
          <span className="text-xs text-gray-500">for a human to ratify or override below</span>
        )}
      </div>

      {result.reasoning && <p className="text-sm text-gray-700">{result.reasoning}</p>}

      {(cov.comparable !== undefined || cov.targets !== undefined) && (
        <p className="mt-2 text-xs text-gray-500">
          Coverage: {cov.comparable ?? 0} of {cov.targets ?? 0} claimed metric(s) comparable
          {" · "}
          {cov.agree ?? 0} agree, {cov.diverge ?? 0} diverge
          {cov.advisory ? `, ${cov.advisory} advisory` : ""}, {cov.not_computed ?? 0} not computed
        </p>
      )}

      {level3Failed?.reason && (
        <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          The finding step did not complete: {level3Failed.reason}. This verdict rests on the
          quality-control evidence alone.
        </p>
      )}

      {level3Skipped?.reason && (
        <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          The finding step did not run: {level3Skipped.reason}. This verdict rests on the
          quality-control evidence alone.
        </p>
      )}

      {attributionReasons.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-xs text-gray-500">
          {attributionReasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
