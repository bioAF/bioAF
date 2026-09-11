"use client";

import type { CapabilityRow } from "@/lib/validationReport";

/**
 * plan_7 steps 13 and 19: what this paper actually has.
 *
 * Rendered at the C1 gate, where it decides which routes are worth offering, and again in the
 * findings report, where it is the reader's map of what could be tested at all.
 *
 * **UNKNOWN is rendered AS UNKNOWN**, with the reason beside it. A checklist that shows NO for a
 * GEO timeout tells the reader something false about the paper, which is the one thing this exists
 * to avoid. The failure also appears in the study's issues section, as a limitation of the run.
 *
 * **Existence and accessibility are separate answers.** A repository can be known to exist and be
 * private; a supplementary archive can be listed and 404 on download. A paper may name more than
 * one code source, and each keeps its own pair of answers.
 *
 * **Nothing here rules a paper in or out.** It says which route is best and how high a validation
 * level is reachable.
 */

export interface Capability {
  value: "yes" | "no" | "unknown";
  evidence: string | null;
  failure_reason: string | null;
}

export interface CodeSource {
  kind: string;
  url: string | null;
  identifier: string | null;
  exists: "yes" | "no" | "unknown";
  accessible: "yes" | "no" | "unknown" | "not_attempted";
  accessible_reason: string | null;
}

/**
 * One deposit, described by the archive it lives in (change_7.1 section 1).
 *
 * Existence, access and support are three answers, not one. EGAD00001005044 exists, is controlled,
 * and cannot be acquired by bioAF: a single chip would drop the half that tells the reader the
 * authors published their reads and the block is ours.
 */
export interface Deposit {
  archive: string;
  accession: string;
  provenance: string | null;
  scoped: boolean;
  exists: "yes" | "no" | "unknown";
  access: "public" | "controlled" | "unavailable" | "unknown" | "not_attempted";
  supported: "yes" | "no";
  raw_data: "yes" | "no" | "unknown";
  preprocessed_data: "yes" | "no" | "unknown";
  sample_metadata: "yes" | "no" | "unknown";
  evidence: string | null;
  failure_reason: string | null;
}

export interface Capabilities {
  paper_readable: Capability;
  deposit_exists: Capability;
  raw_data: Capability;
  preprocessed_data: Capability;
  sample_metadata: Capability;
  code_artifact: Capability;
  code_repository: Capability;
  code_sources: CodeSource[];
  deposits: Deposit[];
}

const ROWS: { key: keyof Capabilities; label: string }[] = [
  { key: "paper_readable", label: "Paper text available" },
  // Archive-neutral: EGA, SRA and ArrayExpress deposits all land in this row now, and the
  // per-deposit rows underneath name the archive each one actually lives in.
  { key: "deposit_exists", label: "Data deposit exists" },
  { key: "raw_data", label: "Raw sample data available" },
  { key: "preprocessed_data", label: "Pre-processed data available" },
  { key: "sample_metadata", label: "Sample metadata available" },
];

const VALUE_LABEL: Record<string, string> = {
  yes: "Yes",
  no: "No",
  unknown: "Unknown",
  not_attempted: "Not attempted",
  public: "Public",
  controlled: "Controlled",
  unavailable: "Unavailable",
};

const VALUE_CLASS: Record<string, string> = {
  yes: "bg-emerald-50 text-emerald-700",
  no: "bg-gray-100 text-gray-600",
  unknown: "bg-amber-50 text-amber-700",
  not_attempted: "bg-gray-100 text-gray-600",
  public: "bg-emerald-50 text-emerald-700",
  // Controlled is not a failure and not a fault of the paper. It is a restriction, and it reads as
  // one rather than as a red mark against the authors.
  controlled: "bg-amber-50 text-amber-700",
  unavailable: "bg-gray-100 text-gray-600",
};

const ARCHIVE_LABEL: Record<string, string> = {
  geo: "GEO",
  ega: "EGA",
  sra: "SRA",
  arrayexpress: "ArrayExpress",
  other: "Archive",
};

// The checklist's code row takes the SOURCE KIND rather than a bare yes, because "GitHub" and
// "Download" tell a reader what will be attempted and "Yes" does not.
const KIND_LABEL: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  zenodo: "Zenodo",
  codeocean: "Code Ocean",
  supplementary: "Download",
  other: "Download",
};

function Chip({ value }: { value: string }) {
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs font-medium ${VALUE_CLASS[value] ?? "bg-gray-100 text-gray-600"}`}
    >
      {VALUE_LABEL[value] ?? value}
    </span>
  );
}

export function CapabilityChecklist({
  capabilities,
  rows,
}: {
  capabilities: Capabilities | null | undefined;
  // change_7.3 section 10 item 11: the checklist rows from the report projection, where each data row
  // is two facts: "deposited" is about the paper, "available to bioAF" is about bioAF. "Raw sample
  // data available: Yes" was shown for reads under controlled access in an archive bioAF cannot read.
  rows?: CapabilityRow[] | null;
}) {
  if (!capabilities && !rows) return null;
  const caps = capabilities ?? ({} as Capabilities);
  const sources = caps.code_sources ?? [];

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <tbody className="divide-y divide-gray-100">
          {rows && rows.length > 0 &&
            rows
              .filter((row) => !["code_artifact", "code_repository"].includes(row.key))
              .map((row) => (
                <tr key={row.key} data-testid={`capability-${row.key}`}>
                  <td className="py-1.5 pr-4 text-gray-700">{row.label}</td>
                  <td className="py-1.5 pr-4">
                    <Chip value={row.value ?? "unknown"} />
                  </td>
                  <td className="py-1.5 text-xs text-gray-500">{row.detail || ""}</td>
                </tr>
              ))}
          {!(rows && rows.length > 0) && ROWS.map(({ key, label }) => {
            const cap = caps[key] as Capability | undefined;
            if (!cap) return null;
            return (
              <tr key={key}>
                <td className="py-1.5 pr-4 text-gray-700">{label}</td>
                <td className="py-1.5 pr-4">
                  <Chip value={cap.value} />
                </td>
                <td className="py-1.5 text-xs text-gray-500">{cap.failure_reason || cap.evidence || ""}</td>
              </tr>
            );
          })}

          {(caps.deposits ?? []).map((deposit) => (
            <tr key={deposit.accession} data-testid={`deposit-${deposit.accession}`}>
              <td className="py-1.5 pr-4 text-gray-700">
                Deposit <span className="text-xs text-gray-500">({ARCHIVE_LABEL[deposit.archive] ?? deposit.archive})</span>
                {deposit.provenance === "extracted" && (
                  <span className="ml-1 text-xs text-gray-500">from the paper</span>
                )}
              </td>
              <td className="py-1.5 pr-4 whitespace-nowrap">
                <Chip value={deposit.exists} />
                {/* Access is a separate fact from existence: a deposit can be known, released and
                    still closed to everyone without a data access agreement. */}
                <span className="ml-1">
                  <Chip value={deposit.access} />
                </span>
                {deposit.supported === "no" && deposit.exists !== "no" && (
                  <span className="ml-1 rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-600">
                    bioAF cannot acquire
                  </span>
                )}
              </td>
              <td className="py-1.5 text-xs text-gray-500">
                {deposit.accession}
                {(deposit.failure_reason || deposit.evidence) && (
                  <span className="ml-2">{deposit.failure_reason || deposit.evidence}</span>
                )}
              </td>
            </tr>
          ))}

          {sources.length === 0 ? (
            <tr>
              <td className="py-1.5 pr-4 text-gray-700">Code published</td>
              <td className="py-1.5 pr-4">
                <Chip value={caps.code_repository?.value ?? "unknown"} />
              </td>
              <td className="py-1.5 text-xs text-gray-500">
                {caps.code_repository?.value === "unknown"
                  ? caps.code_repository?.failure_reason || ""
                  : "no code source was named in the paper"}
              </td>
            </tr>
          ) : (
            sources.map((source, i) => (
              <tr key={i}>
                <td className="py-1.5 pr-4 text-gray-700">
                  Code published{" "}
                  <span className="text-xs text-gray-500">({KIND_LABEL[source.kind] ?? source.kind})</span>
                </td>
                <td className="py-1.5 pr-4">
                  <Chip value={source.exists} />
                  {/* Separate from existence: a link can be known and dead, and one cell cannot
                      carry both facts without losing the useful half. */}
                  <span className="ml-1">
                    <Chip value={source.accessible} />
                  </span>
                </td>
                <td className="py-1.5 text-xs text-gray-500">
                  {source.url ? (
                    <a
                      className="text-blue-700 hover:underline"
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {source.url}
                    </a>
                  ) : (
                    source.identifier || ""
                  )}
                  {source.accessible === "no" && (
                    <span className="ml-2">not accessible: {source.accessible_reason || "could not be fetched"}</span>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
