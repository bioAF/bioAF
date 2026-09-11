"use client";

/**
 * plan_7 step 14c: the steps that hit an error while validating this paper.
 *
 * A refusal used to be invisible at every layer. Some models decline biotech queries outright, and
 * a model that declined every claim binding produced a plan which had silently fallen back to the
 * alias table, with nothing on screen to say so.
 *
 * Informational, never alarming. The section is absent when nothing went wrong, and each row states
 * whether the step carried on with a fallback or produced nothing: without that distinction the
 * section cries wolf and users learn to ignore it.
 *
 * Plain language on screen, technical detail in the logs, per the repo's error-copy rule. A refusal
 * names its model, because that is the one an administrator can request an account exception for.
 */

import { TechnicalDetails } from "./TechnicalDetails";

export interface ValidationIssue {
  step: string;
  outcome: string;
  impact: string;
  message: string;
  model: string | null;
  at: string | null;
  // change_7.3 section 9: URL, HTTP status, error class, attempts and times. Collapsed under the
  // plain row; the message itself stays one plain sentence.
  technical_detail?: Record<string, unknown> | null;
}

const OUTCOME_LABEL: Record<string, string> = {
  refusal: "the model declined to answer",
  unreachable: "bioAF could not reach the language model",
  internal: "bioAF hit an internal error",
  unparseable: "the model's answer was not in the format bioAF asked for",
  // change_7.2 section 7: an answer cut off at the token limit is its own event. Reporting it as
  // badly formatted is a true statement about the text and a false one about what happened.
  truncated: "the model's answer was cut off at its token limit",
  // change_7.3 section 9: failures that are not a model's.
  retrieval_failed: "bioAF could not retrieve a file it needed",
  not_performed: "this step could not run",
};

const IMPACT_LABEL: Record<string, string> = {
  degraded: "continued with a fallback",
  blocked: "produced nothing",
};

const IMPACT_CLASS: Record<string, string> = {
  degraded: "bg-amber-50 text-amber-700",
  blocked: "bg-red-50 text-red-700",
};

export function ValidationIssuesSection({ issues }: { issues: ValidationIssue[] }) {
  if (!issues || issues.length === 0) return null;

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
        Issues Encountered
      </h2>
      <p className="mb-2 text-sm text-gray-600">
        Some steps encountered errors that may affect the ability to validate this paper. Each is
        listed with what it cost the run.
      </p>
      <ul className="divide-y divide-gray-100 border-y border-gray-100">
        {issues.map((issue, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-2 py-2 text-sm">
            <span className="text-gray-800">{issue.step}</span>
            <span aria-hidden className="text-gray-500">
              &middot;
            </span>
            <span className="text-gray-600">
              {OUTCOME_LABEL[issue.outcome] ?? issue.outcome}
            </span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                IMPACT_CLASS[issue.impact] ?? "bg-gray-100 text-gray-700"
              }`}
            >
              {IMPACT_LABEL[issue.impact] ?? issue.impact}
            </span>
            {issue.model && (
              <span className="font-mono text-xs text-gray-500">{issue.model}</span>
            )}
            {issue.message && (
              <span className="basis-full text-xs text-gray-500">{issue.message}</span>
            )}
            {issue.technical_detail && (
              <span className="basis-full">
                <TechnicalDetails detail={issue.technical_detail} />
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
