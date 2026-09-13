/**
 * plan_8 section 7: the Validation Scorecard in a studies-list cell.
 *
 * The same two metrics as the report, compact: the overall score over the assessed scope, and the
 * primary facts a high score must not hide. Cut from the backend's scorecard by the list response, so
 * the list and the report cannot disagree, and it is never a third number.
 */

import { NOT_SET } from "@/lib/placeholders";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { CompactScorecard } from "@/lib/validationReport";

export function ScorecardCompact({ scorecard }: { scorecard: CompactScorecard | null | undefined }) {
  if (!scorecard) {
    return <span className="text-gray-500">{NOT_SET}</span>;
  }
  const scoreLabel =
    scorecard.display_score === null || scorecard.display_score === undefined
      ? "Overall score: not assessed"
      : `Overall score: ${scorecard.display_score} out of 100`;
  return (
    <div className="space-y-0.5">
      <div aria-label={scoreLabel} className="font-semibold tabular-nums text-gray-900">
        {scorecard.score_label ?? NOT_SET}
      </div>
      <div className="text-xs text-gray-600">{scorecard.scope_label ?? scorecard.status_label ?? NOT_SET}</div>
      {(scorecard.indicators ?? []).map((indicator) => (
        <div
          key={indicator.kind}
          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${statusBadgeClass(
            "validationFinding",
            indicator.kind === "primary_discrepancy" ? "discrepancy" : "unresolved",
          )}`}
        >
          <span aria-hidden="true">⚠</span>
          <span>{indicator.text}</span>
        </div>
      ))}
      {scorecard.in_progress && scorecard.in_progress_label && (
        <div className="text-xs text-gray-600">{scorecard.in_progress_label}</div>
      )}
    </div>
  );
}
