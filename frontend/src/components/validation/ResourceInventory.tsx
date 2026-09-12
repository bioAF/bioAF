"use client";

/**
 * change_7.5 section 2.1: every resource the paper names or its repositories link, typed, linked to
 * the experiment it serves, with what bioAF can do with it.
 *
 * Study 38's paper named a PRIDE deposit and PDB structures. None of them reached the report, because
 * only the accessions a model returned were ever looked up. A resource bioAF cannot retrieve is still
 * listed, with the reason; it is a limitation of bioAF, never a finding about the paper.
 *
 * Rendered from the report projection: the type and ability labels are the backend's (pending the
 * owner's sign-off).
 */

import type { ReportSummary } from "@/lib/validationReport";

const ABILITY_CLASS: Record<string, string> = {
  yes: "bg-emerald-50 text-emerald-700",
  no: "bg-gray-100 text-gray-600",
  not_established: "bg-amber-50 text-amber-700",
  unknown: "bg-amber-50 text-amber-700",
};

function Ability({ value, label }: { value: string | null; label: string | null }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${ABILITY_CLASS[value ?? "unknown"] ?? ABILITY_CLASS.unknown}`}>
      {label ?? value ?? "Unknown"}
    </span>
  );
}

export function ResourceInventory({ summary }: { summary: ReportSummary | null | undefined }) {
  const rows = summary?.resources ?? [];
  if (rows.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
            <th className="py-1 pr-4 font-medium">Resource</th>
            <th className="py-1 pr-4 font-medium">Type</th>
            <th className="py-1 pr-4 font-medium">Experiment</th>
            <th className="py-1 pr-4 font-medium">Retrievable by bioAF</th>
            <th className="py-1 pr-4 font-medium">Analyzable by bioAF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id ?? row.identifier ?? ""} className="border-t border-gray-100 align-top">
              <td className="py-1.5 pr-4">
                <span className="font-mono text-xs">{row.identifier}</span>
                {row.role && <p className="text-xs text-gray-500">{row.role}</p>}
                {row.limitation && <p className="text-xs text-gray-600">{row.limitation}</p>}
              </td>
              <td className="py-1.5 pr-4">{row.type_label}</td>
              <td className="py-1.5 pr-4">
                {row.reported_experiment_ids.length > 0 ? (
                  row.reported_experiment_ids.map((id) => (
                    <span key={id} className="mr-1 font-mono text-xs">
                      {id}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-gray-500">Not linked</span>
                )}
              </td>
              <td className="py-1.5 pr-4">
                <Ability value={row.retrievable} label={row.retrievable_label} />
              </td>
              <td className="py-1.5 pr-4">
                <Ability value={row.analyzable} label={row.analyzable_label} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
