"use client";

/**
 * plan_8_4 section 7: the evidence scorecard, at the top of a literature validation report.
 *
 * The headline is V / 100: the points of supporting evidence bioAF has established. It is not a
 * probability that the paper is correct and not a fraction of the paper reproduced, and the three-part
 * bar beside it is never optional, because a lone 35 cannot tell 65 unknown points from 65 failed
 * ones. All three quantities show, including their zeros.
 *
 * Deliberately absent: red, amber and green score bands. A band implies a verdict on the paper, and
 * this number is a statement about what was checked. Zero verified with everything untested reads
 * "Not yet assessed", never "Failed". The reproduction statement sits beside the score and is never
 * moved by it: a paper can earn every documentary point and still have had no reproduction attempted.
 *
 * Rendered from the backend's projection. The frontend computes no points and words no status.
 */

import { useState } from "react";

import { Card } from "@/components/ui/Card";
import type { EvidenceScore, EvidenceScoreSection } from "@/lib/validationReport";

// Order and appearance are the same everywhere the bar is drawn. A pattern and a label carry each
// part, so the bar reads without colour.
const PART_STYLE: Record<string, string> = {
  verified: "bg-emerald-600",
  untested: "bg-gray-300",
  negative: "bg-red-600",
};

function pct(part: string, card: EvidenceScore): number {
  const value = { verified: card.score, untested: card.undetermined, negative: card.failed }[part] ?? 0;
  return Math.max(0, Math.min(100, value));
}

export function EvidenceScoreBar({ card }: { card: EvidenceScore }) {
  return (
    <div
      data-testid="evidence-score-bar"
      role="img"
      aria-label={card.counts_label}
      className="flex h-3 w-full overflow-hidden rounded bg-gray-200"
    >
      {card.parts.map((part) => (
        <div
          key={part.key}
          data-testid={`evidence-bar-${part.key === "untested" ? "undetermined" : part.key}`}
          className={PART_STYLE[part.key] ?? "bg-gray-300"}
          style={{ width: `${pct(part.key, card)}%` }}
        />
      ))}
    </div>
  );
}

function Section({ section, card }: { section: EvidenceScoreSection; card: EvidenceScore }) {
  const [open, setOpen] = useState(false);
  const limits = card.capability_limits.filter((limit) => limit.section === section.section);
  // plan_8_4 section 4: a section reads the way the headline does. Without this the row printed
  // "0.2857142857142857 / 30", which is not a number anyone can act on. A report written before the
  // backend sent the display falls back to its own numbers.
  const shown = section.display ?? {
    verified: String(section.verified),
    undetermined: String(section.undetermined),
    failed: String(section.failed),
    maximum: String(section.maximum),
  };
  return (
    <li data-testid={`evidence-section-${section.section}`} className="border-t border-gray-200 py-2">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        className="flex w-full items-baseline justify-between gap-3 text-left"
      >
        <span className="text-sm font-medium text-gray-800">{section.title}</span>
        <span className="text-sm tabular-nums text-gray-700">
          {shown.verified} / {shown.maximum}
        </span>
      </button>
      <p className="text-xs text-gray-600">
        {shown.verified} positive · {shown.undetermined} untested · {shown.failed} negative
      </p>
      {open && (
        <div className="mt-1 space-y-1 text-xs text-gray-600">
          {/* Section 7: each obligation as ITSELF. "Positive" here means this named obligation was
              established, never that the paper is proven. */}
          {(section.criteria ?? []).map((criterion) => (
            <div key={criterion.criterion} data-testid={`evidence-criterion-${criterion.criterion}`} className="mt-1">
              <p className="text-gray-800">
                {criterion.criterion} {criterion.title}: {criterion.verified} positive, {criterion.undetermined}{" "}
                untested, {criterion.failed} negative of {criterion.points}
              </p>
              <ul className="ml-4 list-disc">
                {criterion.obligations.map((obligation) => (
                  <li key={obligation.leaf}>
                    <span className="text-gray-700">{obligation.statement}</span>{" "}
                    <span className="font-medium">
                      ({obligation.label}, {obligation.points})
                    </span>
                    {obligation.rationale ? `: ${obligation.rationale}` : ""}
                    {obligation.impact ? ` ${obligation.impact}.` : ""}
                    {obligation.next_action ? ` Next: ${obligation.next_action}.` : ""}{" "}
                    <span className="text-gray-500">{obligation.method_label}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {section.outstanding && <p className="text-gray-700">Outstanding: {section.outstanding}</p>}
          {section.unsupported_count > 0 && (
            <p data-testid={`evidence-limits-${section.section}`}>
              {section.unsupported_count} {section.unsupported_count === 1 ? "obligation has" : "obligations have"} no
              implemented check
              {limits.length > 0 ? `: ${limits.map((limit) => limit.leaf).join(", ")}` : ""}.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function EvidenceScorecard({ card }: { card: EvidenceScore }) {
  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-medium text-gray-700">Validation Scorecard</h2>
        <span className="text-xs text-gray-600">{card.rubric_label}</span>
      </div>
      <p data-testid="evidence-score-headline" className="mt-1 text-3xl font-semibold tabular-nums text-ink">
        {card.headline}
      </p>
      {card.score_note && (
        <p data-testid="evidence-score-note" className="text-sm text-gray-700">
          {card.score_note}
        </p>
      )}
      <div className="mt-2">
        <EvidenceScoreBar card={card} />
      </div>
      <p data-testid="evidence-score-counts" className="mt-1 text-sm text-gray-700">
        {card.counts_label}
      </p>
      <p data-testid="evidence-score-scope" className="text-xs text-gray-600">
        {card.scope.label}
      </p>
      <p data-testid="evidence-reproduction" className="text-xs text-gray-700">
        {card.reproduction.label}
      </p>
      <p className="mt-2 text-xs text-gray-600">{card.explanation}</p>
      {/* Section 7: a confirmed problem shows beside the score whatever its point weight. A high
          score must never hide that a record contradicts the paper. */}
      {card.concerns.length > 0 && (
        <ul data-testid="evidence-concerns" className="mt-2 space-y-1 text-xs text-gray-800">
          {card.concerns.map((concern) => (
            <li key={concern.leaf}>
              <span className="font-medium">{concern.criterion}:</span> {concern.rationale}
              {concern.impact ? ` ${concern.impact}.` : ""}
            </li>
          ))}
        </ul>
      )}
      {/* Section 6.4: a grey obligation with no stated way forward is indistinguishable from one
          nobody will ever assess. This says what each would take, and which need an approval. */}
      {card.next_checks.length > 0 && (
        <details className="mt-2 text-xs text-gray-700">
          <summary className="cursor-pointer text-gray-600">
            {card.next_checks.length} {card.next_checks.length === 1 ? "check" : "checks"} bioAF could still make
          </summary>
          <ul data-testid="evidence-next-checks" className="ml-4 mt-1 list-disc space-y-0.5">
            {card.next_checks.map((next) => (
              <li key={next.leaf}>
                <span className="font-medium">{next.leaf}</span> ({next.points} points): {next.action}
                {next.needs_approval ? " (needs an approval)" : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      <ul className="mt-3">
        {card.sections.map((section) => (
          <Section key={section.section} section={section} card={card} />
        ))}
      </ul>
    </Card>
  );
}
