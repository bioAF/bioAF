"use client";

import { useState, type ReactNode } from "react";

import type { CompactResource, ReportSummary } from "@/lib/validationReport";

import { ResourceInventory } from "./ResourceInventory";
import { ResourceStatements } from "./ValidationScorecard";

/**
 * plan_8_2 section 4.2 (approved 2026-09-14): the Data and code section's resources.
 *
 * The deposits and supplements a check can use are in the table. Resources bioAF has no adapter for are one
 * compact group with links and the limitation, never hidden and never a defect in the paper; sample records
 * are another. Every resource stays one expander away, with the full table. The groups are the backend's.
 */
function Linked({ rows }: { rows: CompactResource[] }) {
  return (
    <ul className="space-y-0.5 text-sm">
      {rows.map((row) => (
        <li key={row.identifier ?? ""}>
          {row.link ? (
            <a
              href={row.link}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-xs text-bioaf-700 hover:underline"
            >
              {row.identifier}
            </a>
          ) : (
            <span className="font-mono text-xs">{row.identifier}</span>
          )}
          {row.limitation && <span className="text-xs text-gray-600">: {row.limitation}</span>}
        </li>
      ))}
    </ul>
  );
}

export function ReportDataAndCode({ summary, children }: { summary: ReportSummary; children?: ReactNode }) {
  // The full table is built when it is opened, so each resource is listed once until a person asks for all.
  const [everyOpen, setEveryOpen] = useState(false);
  const data = summary.sections?.data;
  const unsupported = data?.unsupported.resources ?? [];
  const samples = data?.sample_records ?? [];
  const grouped = new Set([...unsupported, ...samples].map((row) => row.identifier));
  const resources = summary.resources ?? [];
  const tabled = resources.filter((row) => !grouped.has(row.identifier));

  return (
    <div className="space-y-5">
      {tabled.length > 0 && (
        <div data-testid="resources-readable">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Resources the paper names</h3>
          <ResourceInventory summary={summary} include={(row) => !grouped.has(row.identifier)} />
        </div>
      )}
      {unsupported.length > 0 && (
        <div data-testid="resources-unsupported">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Resources bioAF has no adapter for
          </h3>
          <Linked rows={unsupported} />
          {data?.unsupported.note && <p className="mt-1 text-xs text-gray-500">{data.unsupported.note}</p>}
        </div>
      )}
      {samples.length > 0 && (
        <div data-testid="resources-samples">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Sample records</h3>
          <Linked rows={samples} />
        </div>
      )}
      {resources.length > 0 && (
        <details
          data-testid="resources-every"
          className="text-sm"
          onToggle={(event) => setEveryOpen((event.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer text-xs font-medium text-gray-700">
            Every resource the paper names ({resources.length})
          </summary>
          {everyOpen && (
            <div className="mt-2">
              <ResourceInventory summary={summary} />
            </div>
          )}
        </details>
      )}
      <ResourceStatements statements={summary.scorecard?.resource_statements ?? []} />
      {children}
    </div>
  );
}
