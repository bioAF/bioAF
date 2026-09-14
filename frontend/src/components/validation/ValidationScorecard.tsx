"use client";

/**
 * plan_8 section 6: the Validation Scorecard, at the top of a literature validation report.
 *
 * Two metrics of comparable prominence: the overall score (agreement among conclusively assessed
 * findings, primary findings weighted twice as much as supporting ones) and the assessed scope (an
 * unweighted count). Below them, what a high score must never hide: primary discrepancies and
 * primary findings left unassessed. Then every finding, assessed or not, with its category, outcome
 * and reason, so each number traces to the findings it counts.
 *
 * Rendered from the backend's projection. The frontend computes no metric and words no status.
 */

import { useId, useState } from "react";

import { Card } from "@/components/ui/Card";
import { NOT_SET } from "@/lib/placeholders";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { ResourceStatement, ScorecardItem, ValidationScorecardData } from "@/lib/validationReport";

// A long inventory shows this many findings per list before "Show all".
const INITIAL_ITEMS = 5;

// Text and an icon beside every colour, so no outcome is carried by colour alone.
const STATUS_ICON: Record<string, string> = {
  supported: "✓",
  discrepancy: "✕",
  inconclusive: "?",
  blocked: "⊘",
  unresolved: "!",
  not_attempted: "○",
};

const METRIC_VALUE = "text-3xl font-semibold tabular-nums text-ink";
const ASSESSED = new Set(["supported", "discrepancy"]);

// plan_8_1 section 4.4: a resource statement's outcome, in the same text-and-colour vocabulary.
const STATEMENT_STATUS: Record<ResourceStatement["outcome"], string> = {
  verified: "supported",
  contradicted: "discrepancy",
  not_established: "unresolved",
};

function scoreLabel(card: ValidationScorecardData): string {
  if (card.display_score === null || card.display_score === undefined) {
    return card.score_status_label ? `Overall score: ${card.score_status_label}` : "Overall score: not assessed";
  }
  return `Overall score: ${card.display_score} out of 100`;
}

function scopeLabel(card: ValidationScorecardData): string {
  if (card.assessed_count === null || card.total_count === null || card.scope_label === null) {
    return `Assessed scope: ${card.status_label ?? "not established"}`;
  }
  return `Assessed scope: ${card.assessed_count} of ${card.total_count} findings assessed`;
}

function FindingRow({ item, statements }: { item: ScorecardItem; statements?: Map<string, ResourceStatement> }) {
  const evidence = item.claim_indices?.[0];
  const governing = item.depth ? (item.governing?.evidence ?? []) : [];
  return (
    <li className="py-2 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <span
          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${statusBadgeClass("validationFinding", item.status)}`}
        >
          <span aria-hidden="true">{STATUS_ICON[item.status] ?? "•"}</span>
          <span>{item.status_label}</span>
        </span>
        {item.cause_label && <span className="text-xs font-medium text-gray-700">{item.cause_label}</span>}
        {/* plan_8_1 section 4.5: every assessed item shows its depth. */}
        {item.depth_label && (
          <span className="rounded border border-gray-300 px-1.5 py-0.5 text-xs font-medium text-gray-800">
            {item.depth_label}
          </span>
        )}
        <span className="rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-700">{item.category_label}</span>
        <span className="text-gray-900">{item.description ?? item.finding_id}</span>
        {evidence !== undefined && (
          <a href={`#claim-${evidence}`} className="text-xs text-bioaf-700 hover:underline">
            Evidence
          </a>
        )}
      </div>
      {item.reason && <p className="mt-0.5 text-xs text-gray-600">{item.reason}</p>}
      {governing.length > 0 && (
        <p className="mt-0.5 text-xs text-gray-600">Governing evidence: {governing.join(", ")}</p>
      )}
      {(item.concerns ?? []).map((concern, i) => (
        <p key={`concern-${i}`} className="mt-0.5 text-xs font-medium text-gray-800">
          Concern: {concern.text}
        </p>
      ))}
      {/* plan_8_1 section 4.7: an unassessed finding keeps each check's own cause. */}
      {!ASSESSED.has(item.status) &&
        (item.check_causes ?? []).map((row) => (
          <p key={`${row.check}-${row.cause}`} className="mt-0.5 text-xs text-gray-600">
            {row.check_label}: {row.cause_label ?? NOT_SET}
            {row.reason ? `. ${row.reason}` : ""}
          </p>
        ))}
      {(item.resource_statements ?? []).map((identifier) => (
        <p key={`statement-${identifier}`} className="mt-0.5 text-xs text-gray-600">
          Resource statement {identifier}: {statements?.get(identifier)?.outcome_label ?? NOT_SET}
        </p>
      ))}
      {item.supporting_checks.map((check, i) => (
        <p key={`${check.kind}-${i}`} className="mt-0.5 text-xs text-gray-600">
          {check.text}
        </p>
      ))}
      {item.rationale && !item.importance_problem && (
        <p className="mt-0.5 text-xs text-gray-500">
          {item.category_label} because: {item.rationale}
        </p>
      )}
      {item.importance_problem && (
        <p className="mt-0.5 text-xs text-gray-600">Importance not established: {item.importance_problem}</p>
      )}
    </li>
  );
}

function FindingList({
  title,
  items,
  testId,
  statements,
}: {
  title: string;
  items: ScorecardItem[];
  testId: string;
  statements: Map<string, ResourceStatement>;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  if (items.length === 0) return null;
  const shown = expanded ? items : items.slice(0, INITIAL_ITEMS);
  const hidden = items.length - shown.length;
  return (
    <div data-testid={testId} className="mt-4">
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      <ul id={listId} className="divide-y divide-gray-100">
        {shown.map((item) => (
          <FindingRow key={item.finding_id} item={item} statements={statements} />
        ))}
      </ul>
      {items.length > INITIAL_ITEMS && (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 text-xs font-medium text-bioaf-700 hover:underline"
        >
          {expanded ? "Show fewer" : `Show all (${hidden} more)`}
        </button>
      )}
    </div>
  );
}

function ResourceStatements({ statements }: { statements: ResourceStatement[] }) {
  if (statements.length === 0) return null;
  return (
    <div data-testid="scorecard-resource-statements" className="mt-4">
      <h3 className="text-sm font-semibold text-gray-800">Resource statements</h3>
      <p className="text-xs text-gray-600">
        What the paper states about each resource, checked against the resource&apos;s own record. Not scored.
      </p>
      <ul className="divide-y divide-gray-100">
        {statements.map((statement) => (
          <li key={statement.identifier} className="py-2 text-sm">
            <div className="flex flex-wrap items-baseline gap-2">
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium ${statusBadgeClass(
                  "validationFinding",
                  STATEMENT_STATUS[statement.outcome] ?? "unresolved",
                )}`}
              >
                {statement.outcome_label}
              </span>
              <span className="font-mono text-gray-900">{statement.identifier}</span>
              {[statement.archive, statement.access].filter(Boolean).length > 0 && (
                <span className="text-xs text-gray-600">
                  {[statement.archive, statement.access].filter(Boolean).join(", ")}
                </span>
              )}
            </div>
            {statement.checks.map((check) => (
              <p key={check.field} className="mt-0.5 text-xs text-gray-600">
                {check.outcome_label}: {check.detail}
              </p>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ValidationScorecard({ scorecard }: { scorecard: ValidationScorecardData | null | undefined }) {
  if (!scorecard) return null;
  const card = scorecard;
  const statements = new Map((card.resource_statements ?? []).map((s) => [s.identifier, s]));
  const inProgress = card.in_progress && card.in_progress_label && (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusBadgeClass("validationStage", "in_progress")}`}>
      {card.in_progress_label}
    </span>
  );

  if (card.status === "unavailable") {
    return (
      <Card title={card.title} actions={inProgress || undefined}>
        <p className="text-sm text-gray-700">{card.status_label}</p>
      </Card>
    );
  }

  const unestablished =
    card.status === "not_established" || card.status === "not_applicable" || card.status === "pending";
  return (
    <Card title={card.title} actions={inProgress || undefined}>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dd data-testid="scorecard-score" aria-label={scoreLabel(card)} className={METRIC_VALUE}>
            {card.score_label ??
              (card.score_status_label ? (
                <span className="text-base font-medium text-gray-700">{card.score_status_label}</span>
              ) : (
                NOT_SET
              ))}
          </dd>
          <dt className="text-xs uppercase tracking-wide text-gray-500">Overall score</dt>
        </div>
        <div>
          <dd data-testid="scorecard-scope" aria-label={scopeLabel(card)} className={METRIC_VALUE}>
            {card.scope_label ?? card.status_label ?? NOT_SET}
          </dd>
          {/* plan_8_1 section 4.5: the depth, directly under the scope it qualifies. */}
          {card.depth_label && (
            <dd data-testid="scorecard-depth" className="text-base font-semibold text-ink">
              {card.depth_label}
            </dd>
          )}
          <dt className="text-xs uppercase tracking-wide text-gray-500">Assessed scope</dt>
        </div>
      </dl>

      {/* plan_8_2 section 1.4: the checks' activity, which a concluded status never hides. */}
      {card.activity?.label && (
        <p data-testid="scorecard-activity" className="mt-3 text-sm text-gray-700">
          Checks: {card.activity.label}
        </p>
      )}
      {unestablished && card.reason && <p className="mt-3 text-sm text-gray-700">{card.reason}</p>}
      {/* plan_8_1 section 2.3: a provisional scope says why its total may fall. */}
      {card.provisional_note && (
        <p data-testid="scorecard-provisional-note" className="mt-3 text-sm text-gray-700">
          Provisional: {card.provisional_note}
        </p>
      )}
      {card.unresolved_importance.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-gray-600">
          {card.unresolved_importance.map((row) => (
            <li key={row.finding_id}>
              {row.description ?? row.finding_id}: {row.problem}
            </li>
          ))}
        </ul>
      )}

      {card.summary && <p className="mt-3 text-sm text-gray-800">{card.summary}</p>}

      {card.messages.length > 0 && (
        <ul data-testid="scorecard-messages" className="mt-2 space-y-1">
          {card.messages.map((message, i) => (
            <li
              key={`${message.kind}-${i}`}
              className={`flex flex-wrap items-baseline gap-2 rounded px-2 py-1 text-sm font-medium ${statusBadgeClass(
                "validationFinding",
                message.kind === "primary_discrepancy" ? "discrepancy" : "unresolved",
              )}`}
            >
              <span aria-hidden="true">⚠</span>
              <span>{message.text}</span>
              {message.kind === "primary_unassessed" && (
                <span className="font-normal">
                  (
                  {message.findings
                    .map(
                      (id) =>
                        card.unassessed_items.find((item) => item.finding_id === id)?.description ?? id,
                    )
                    .join("; ")}
                  )
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {(card.status === "scored" || card.status === "not_assessed") && (
        <details className="mt-3 text-xs text-gray-600">
          <summary className="cursor-pointer font-medium text-gray-700">How the score is calculated</summary>
          <p className="mt-1">
            {card.explanation}
            {(card.assessed_weight ?? 0) > 0 &&
              ` Weighted agreement: ${card.supported_weight} of ${card.assessed_weight}.`}{" "}
            Scored under {card.rubric_label}, finding inventory revision {card.inventory_revision}
            {card.outcomes_recorded_at ? ", from the outcomes recorded when the study concluded" : ""}.
          </p>
        </details>
      )}

      <FindingList title="Assessed" items={card.assessed_items} testId="scorecard-assessed" statements={statements} />
      <FindingList
        title="Not assessed / unresolved"
        items={card.unassessed_items}
        testId="scorecard-unassessed"
        statements={statements}
      />

      {card.excluded_items.length > 0 && (
        <details data-testid="scorecard-excluded" className="mt-4 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-gray-700">
            Not scored: technical prerequisites and descriptive checks ({card.excluded_items.length})
          </summary>
          <ul className="divide-y divide-gray-100">
            {card.excluded_items.map((item) => (
              <FindingRow key={item.finding_id} item={item} statements={statements} />
            ))}
          </ul>
        </details>
      )}

      <ResourceStatements statements={card.resource_statements ?? []} />
    </Card>
  );
}
