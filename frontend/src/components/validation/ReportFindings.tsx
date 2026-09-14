"use client";

import { useId, useState } from "react";

import type { ReportClaim, ReportSummary, ResourceStatement, ScorecardItem } from "@/lib/validationReport";

import { ClaimItem } from "./ClaimSelection";
import { FindingBadges, FindingExtras } from "./ValidationScorecard";

/**
 * plan_8_2 section 4.2 (approved 2026-09-14): the Findings section.
 *
 * Findings are grouped by the experiment that reports them, and each holds its claims (with their checks,
 * consistency and the recorded-reading control), at the same `claim-{n}` anchors as before. A reason several
 * findings share is stated once, naming the findings it affects; each of them links to it rather than
 * repeating it. A valid discrepancy is open by default; the rest are collapsed. The grouping and the
 * shared reasons are the backend's.
 */
const INITIAL_ITEMS = 5;

function FindingItem({
  item,
  open,
  claims,
  statements,
  studyId,
  onChanged,
}: {
  item: ScorecardItem;
  open: boolean;
  claims: ReportClaim[];
  statements: Map<string, ResourceStatement>;
  studyId?: number;
  onChanged?: (updated: unknown) => void;
}) {
  const held = item.claim_indices.filter((index) => claims[index]);
  return (
    <details
      id={`finding-${item.finding_id}`}
      open={open || undefined}
      className="group/finding rounded border border-gray-200"
    >
      <summary className="flex cursor-pointer list-none flex-col gap-0.5 px-3 py-2 [&::-webkit-details-marker]:hidden">
        <span className="flex flex-wrap items-baseline gap-2 text-sm">
          <span aria-hidden="true" className="text-[10px] text-gray-500 group-open/finding:rotate-90">
            ▶
          </span>
          <span className="font-mono text-xs text-gray-500">{item.finding_id}</span>
          <span className="font-medium text-gray-900">{item.description ?? item.finding_id}</span>
          <FindingBadges item={item} />
        </span>
        {item.shared_reason ? (
          <span className="pl-5 text-xs text-gray-600">
            <a href={`#shared-reason-${item.shared_reason}`} className="text-bioaf-700 hover:underline">
              See {item.shared_reason}
            </a>
          </span>
        ) : (
          item.reason && <span className="pl-5 text-xs text-gray-600">{item.reason}</span>
        )}
      </summary>
      <div className="space-y-2 px-3 pb-3 pl-8">
        <FindingExtras item={item} statements={statements} />
        {held.length > 0 && (
          <ol className="space-y-3 border-t border-gray-100 pt-2">
            {held.map((index) => (
              <ClaimItem key={index} claim={claims[index]} index={index} studyId={studyId} onChanged={onChanged} />
            ))}
          </ol>
        )}
      </div>
    </details>
  );
}

function FindingGroup({
  groupKey,
  label,
  items,
  ...rest
}: {
  groupKey: string;
  label: string;
  items: ScorecardItem[];
  openIds: Set<string>;
  claims: ReportClaim[];
  statements: Map<string, ResourceStatement>;
  studyId?: number;
  onChanged?: (updated: unknown) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const shown = expanded ? items : items.slice(0, INITIAL_ITEMS);
  return (
    <div data-testid={`findings-group-${groupKey}`}>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</h3>
      <div id={listId} className="space-y-2">
        {shown.map((item) => (
          <FindingItem
            key={item.finding_id}
            item={item}
            open={rest.openIds.has(item.finding_id)}
            claims={rest.claims}
            statements={rest.statements}
            studyId={rest.studyId}
            onChanged={rest.onChanged}
          />
        ))}
      </div>
      {items.length > INITIAL_ITEMS && (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 text-xs font-medium text-bioaf-700 hover:underline"
        >
          {expanded ? "Show fewer" : `Show all (${items.length - INITIAL_ITEMS} more)`}
        </button>
      )}
    </div>
  );
}

export function ReportFindings({
  summary,
  studyId,
  onChanged,
}: {
  summary: ReportSummary;
  studyId?: number;
  onChanged?: (updated: unknown) => void;
}) {
  const card = summary.scorecard;
  const section = summary.sections?.findings;
  const claims = summary.claims ?? [];
  const items = new Map(
    [...(card?.assessed_items ?? []), ...(card?.unassessed_items ?? []), ...(card?.excluded_items ?? [])].map(
      (item) => [item.finding_id, item],
    ),
  );
  const statements = new Map((card?.resource_statements ?? []).map((s) => [s.identifier, s]));
  const openIds = new Set(section?.open ?? []);
  const shared = section?.shared_reasons ?? [];
  const groups = section?.groups ?? [];
  // Claims no finding holds keep their rows (a study read before the inventory, or one still grouping).
  const ungrouped = (section?.ungrouped_claims ?? []).filter(
    (index) => claims[index] && ((claims[index].checks ?? []).length > 0 || claims[index].selection),
  );
  const common = { openIds, claims, statements, studyId, onChanged };

  return (
    <div className="space-y-5">
      {shared.map((reason) => (
        <div
          key={reason.id}
          id={`shared-reason-${reason.id}`}
          data-testid={`shared-reason-${reason.id}`}
          className="space-y-1 rounded bg-gray-50 p-3 text-sm"
        >
          <p className="text-gray-800">
            <span className="font-mono text-xs text-gray-500">{reason.id}</span>{" "}
            <span className="font-medium">Shared reason</span>
            {reason.cause_label ? ` (${reason.cause_label})` : ""}: {reason.text}
          </p>
          <p className="text-xs text-gray-600">
            Affects{" "}
            {reason.finding_ids.map((id, i) => (
              <span key={id}>
                {i > 0 && ", "}
                <a href={`#finding-${id}`} className="text-bioaf-700 hover:underline">
                  {id}
                </a>
              </span>
            ))}
          </p>
        </div>
      ))}

      {groups
        .filter((group) => group.key !== "excluded")
        .map((group) => (
          <FindingGroup
            key={group.key}
            groupKey={group.key}
            label={group.label}
            items={group.finding_ids.map((id) => items.get(id)).filter((item): item is ScorecardItem => !!item)}
            {...common}
          />
        ))}

      {groups
        .filter((group) => group.key === "excluded")
        .map((group) => (
          <details key={group.key} data-testid="findings-group-excluded" className="text-sm">
            <summary className="cursor-pointer text-xs font-medium text-gray-700">
              {group.label} ({group.finding_ids.length})
            </summary>
            <div className="mt-2 space-y-2">
              {group.finding_ids
                .map((id) => items.get(id))
                .filter((item): item is ScorecardItem => !!item)
                .map((item) => (
                  <FindingItem key={item.finding_id} item={item} open={false} {...common} />
                ))}
            </div>
          </details>
        ))}

      {ungrouped.length > 0 && (
        <div data-testid="findings-group-ungrouped">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Claims not grouped into a finding
          </h3>
          <ol className="space-y-3">
            {ungrouped.map((index) => (
              <ClaimItem key={index} claim={claims[index]} index={index} studyId={studyId} onChanged={onChanged} />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
