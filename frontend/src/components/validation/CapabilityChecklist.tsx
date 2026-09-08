"use client";

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

export interface Capabilities {
  paper_readable: Capability;
  geo_entry: Capability;
  raw_data: Capability;
  preprocessed_data: Capability;
  sample_metadata: Capability;
  code_artifact: Capability;
  code_repository: Capability;
  code_sources: CodeSource[];
}

const ROWS: { key: keyof Capabilities; label: string }[] = [
  { key: "paper_readable", label: "Paper text available" },
  { key: "geo_entry", label: "GEO entry exists" },
  { key: "raw_data", label: "Raw sample data available" },
  { key: "preprocessed_data", label: "Pre-processed data available" },
  { key: "sample_metadata", label: "Sample metadata available" },
];

const VALUE_LABEL: Record<string, string> = {
  yes: "Yes",
  no: "No",
  unknown: "Unknown",
  not_attempted: "Not attempted",
};

const VALUE_CLASS: Record<string, string> = {
  yes: "bg-emerald-50 text-emerald-700",
  no: "bg-gray-100 text-gray-600",
  unknown: "bg-amber-50 text-amber-700",
  not_attempted: "bg-gray-100 text-gray-600",
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

export function CapabilityChecklist({ capabilities }: { capabilities: Capabilities | null | undefined }) {
  if (!capabilities) return null;
  const sources = capabilities.code_sources ?? [];

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <tbody className="divide-y divide-gray-100">
          {ROWS.map(({ key, label }) => {
            const cap = capabilities[key] as Capability | undefined;
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

          {sources.length === 0 ? (
            <tr>
              <td className="py-1.5 pr-4 text-gray-700">Code published</td>
              <td className="py-1.5 pr-4">
                <Chip value={capabilities.code_repository?.value ?? "unknown"} />
              </td>
              <td className="py-1.5 text-xs text-gray-500">
                {capabilities.code_repository?.value === "unknown"
                  ? capabilities.code_repository?.failure_reason || ""
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
