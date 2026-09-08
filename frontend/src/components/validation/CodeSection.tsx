"use client";

/**
 * plan_7 step 19, part 3: whether the computational methods are clearly defined and reproducible,
 * and whether the code executes correctly.
 *
 * **Observation first, then explanation, and they are visibly different things.** The section reads
 * in that order: what was attempted (which source, which commit, which entry point), what happened
 * (the outcome, with its evidence), and only then what might explain it, hedged, with its
 * confidence and the model that assessed it. Candidate explanations include bioAF's own input
 * mapping, arguments and environment, so the section can say "the difference may be ours" without
 * attributing anything to the paper.
 *
 * **Four distinct findings, not one.** "Did not execute", "executed and produced nothing",
 * "executed and produced output we cannot compare", and "executed and produced a comparable result"
 * are four different statements about a paper. Today's feature reports all four as `inconclusive`.
 *
 * **The tool never says the authors were wrong.** Where the noise-vs-signal call said a
 * misinterpretation is likely, it is shown BESIDE the numbers as a hedged possible issue.
 */

export interface CodeObservation {
  outcome: string;
  qualifiers: string[];
  exit_code: number | null;
  transcript_uri: string | null;
  transcript_tail: string;
  metric?: string | null;
  paper_value?: number | null;
  our_value?: number | null;
  unmatched?: { path?: string | null; name?: string | null; value?: number | null }[] | null;
}

export interface CodeExecution {
  attempt: number;
  method: string;
  qualifiers: string[];
  entry_point?: string | null;
  outcome?: string | null;
  reason?: string | null;
  source?: {
    repo_url?: string | null;
    commit_sha?: string | null;
    generated_by_model?: string | null;
    generated_source_uri?: string | null;
    assumptions?: string[] | null;
  } | null;
  observation?: CodeObservation | null;
}

export interface CodeEvidence {
  code_resolution?: {
    outcome: string;
    kind?: string | null;
    url?: string | null;
    commit_sha?: string | null;
    reason?: string | null;
    attempts?: { kind?: string; url?: string | null; ok?: boolean; reason?: string | null }[] | null;
  } | null;
  code_execution?: CodeExecution | null;
  execution_assessment?: {
    candidate: string | null;
    candidates_offered: string[];
    reason: string;
    confidence: number;
    model: string | null;
    assessed_at: string;
  } | null;
  signal_assessment?: {
    verdict: string;
    reason: string;
    confidence: number;
    model: string | null;
    assessed_at: string;
  } | null;
  generated_analysis?: { source?: string | null; assumptions?: string[] | null } | null;
  precompute_checks?: {
    // The same shape `PrecomputeChecksPanel` renders at the gate; only the three fields this
    // section reads are required, so a caller holding the full check can pass it unchanged.
    methods_detailed_enough?:
      | ({ verdict: string; detail: string; model: string | null } & Record<string, unknown>)
      | null;
  } | null;
}

// What each outcome MEANS, so a reader sees "the code does not execute: dependencies do not
// install" rather than a token from a state machine.
const OUTCOME_SENTENCE: Record<string, string> = {
  code_absent: "The paper published no analysis code, so its own analysis could not be run.",
  code_unreachable:
    "The paper's analysis code could not be fetched. That is a statement about the link, not about the science.",
  dependency_unresolvable:
    "The code does not execute: its dependencies would not install, so the analysis could not be run as published.",
  code_incomplete:
    "The code does not execute: it refers to something that was never published, so the analysis could not be run as published.",
  code_error: "The code does not execute: it installed and started, then failed while running.",
  data_mismatch:
    "The code ran and did not receive the inputs it expected. bioAF chose which deposited file to mount and how its columns map, so that choice is among the candidate explanations.",
  generation_failed: "No runnable analysis could be generated from the methods the paper describes.",
  ran_no_output: "The code executed and wrote nothing at all.",
  ran_output_uncomparable:
    "The code executed and produced real output that bioAF's comparison layer does not support. That is a limitation of bioAF, not a defect of the paper.",
  ran_output_diverges:
    "The code executed and produced a result that disagrees with the paper's own. Both numbers are below; the difference is not attributed without evidence.",
  ran_output_agrees: "The code executed and reproduced the paper's own result.",
};

// The candidate causes, in a reader's language. Ours are named as ours.
const CANDIDATE_LABEL: Record<string, string> = {
  bioaf_input_mapping: "bioAF's own choice of input file and column mapping",
  bioaf_arguments: "bioAF's own choice of entry point and arguments",
  bioaf_environment: "the environment bioAF built to run it in",
  deposit_problem: "the deposited data not matching what the analysis expects",
  code_defect: "the published code not doing what the paper describes",
};

const METHOD_LABEL: Record<string, string> = {
  authors_code: "the authors' own published code",
  llm_from_methods: "an analysis generated from the paper's described methods",
};

function num(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : value.toLocaleString();
}

export function CodeSection({ evidence }: { evidence: CodeEvidence }) {
  const resolution = evidence.code_resolution;
  const execution = evidence.code_execution;
  const assessment = evidence.execution_assessment;
  const signal = evidence.signal_assessment;
  const methods = evidence.precompute_checks?.methods_detailed_enough;

  if (!resolution && !execution && !methods) return null;

  const observation = execution?.observation;
  const outcome = execution?.outcome || resolution?.outcome;
  const source = execution?.source || {};
  const generated = execution?.method === "llm_from_methods";
  const thin = (execution?.qualifiers || []).includes("methods_inadequate");

  return (
    <div className="space-y-4 text-sm">
      {methods && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Are the computational methods clearly described?
          </p>
          <p className="text-gray-800">
            {methods.verdict === "ok" ? "Yes." : methods.verdict === "mismatch" ? "No." : "Could not be established."}{" "}
            <span className="text-gray-600">{methods.detail}</span>
          </p>
        </div>
      )}

      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          What was attempted{execution?.method ? `: ${METHOD_LABEL[execution.method] ?? execution.method}` : ""}
        </p>
        {resolution?.url || source.repo_url ? (
          <p className="text-gray-800">
            <a
              className="text-blue-700 hover:underline"
              href={resolution?.url || source.repo_url || undefined}
              target="_blank"
              rel="noreferrer noopener"
            >
              {resolution?.url || source.repo_url}
            </a>
            {(resolution?.commit_sha || source.commit_sha) && (
              <span className="ml-2 font-mono text-xs text-gray-600">
                {resolution?.commit_sha || source.commit_sha}
              </span>
            )}
            {execution?.entry_point && (
              <span className="ml-2 font-mono text-xs text-gray-600">{execution.entry_point}</span>
            )}
          </p>
        ) : (
          <p className="text-gray-800">{resolution?.reason || "No code source was resolved."}</p>
        )}
        {generated && (
          <p className="text-xs text-gray-600">
            This analysis was generated from the paper&apos;s described methods by{" "}
            <span className="font-mono">{source.generated_by_model}</span>, not published by the
            authors.
          </p>
        )}
      </div>

      {outcome && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">What happened</p>
          <p className="text-gray-800">{OUTCOME_SENTENCE[outcome] ?? outcome}</p>
          {thin && (
            <p className="text-xs text-amber-800">
              The paper&apos;s methods description was thin, so this result is reported under that
              limitation rather than as a clean reproduction.
            </p>
          )}
          {observation?.paper_value !== undefined && observation?.paper_value !== null && (
            <table className="mt-2 text-xs">
              <tbody>
                <tr>
                  <td className="pr-4 text-gray-500">{observation.metric || "the paper's result"}</td>
                  <td className="pr-4 text-gray-700">The paper: {num(observation.paper_value)}</td>
                  <td className="text-gray-700">Our re-run: {num(observation.our_value)}</td>
                </tr>
              </tbody>
            </table>
          )}
          {observation?.unmatched && observation.unmatched.length > 0 && (
            <p className="mt-1 text-xs text-gray-600">
              Produced but not compared:{" "}
              {observation.unmatched.map((u) => u.name || u.path).filter(Boolean).join(", ")}. bioAF&apos;s
              comparison layer does not support these, so they are retained rather than scored.
            </p>
          )}
          {(source.assumptions || []).length > 0 && (
            <div className="mt-1">
              <p className="text-xs text-gray-500">Assumptions the generation had to make:</p>
              <ul className="list-inside list-disc text-xs text-gray-600">
                {(source.assumptions || []).map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {assessment && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">What might explain it</p>
          <p className="text-gray-800">
            {assessment.candidate
              ? `Most likely: ${CANDIDATE_LABEL[assessment.candidate] ?? assessment.candidate}.`
              : "The cause could not be resolved from the evidence."}
          </p>
          <p className="text-xs text-gray-600">
            {assessment.reason}
            {assessment.model && (
              <>
                {" "}
                <span className="font-mono">{assessment.model}</span>
              </>
            )}
            <span className="ml-1 tabular-nums">{assessment.confidence.toFixed(2)}</span>
          </p>
          {assessment.candidates_offered.length > 0 && (
            <p className="text-xs text-gray-500">
              Considered:{" "}
              {assessment.candidates_offered
                .map((c) => CANDIDATE_LABEL[c] ?? c)
                .filter((c) => c !== "unresolved")
                .join("; ")}
              .
            </p>
          )}
        </div>
      )}

      {signal && (
        <div>
          {signal.verdict === "likely" ? (
            <p className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
              <span className="font-medium">Possible issue.</span> The paper&apos;s result may be a
              reading of noise as signal: {signal.reason} This is a flagged possibility, not a
              conclusion, and the numbers above are what a reader should judge from.{" "}
              <span className="font-mono">{signal.model}</span>
            </p>
          ) : (
            <p className="text-xs text-gray-600">
              We could not reproduce the paper&apos;s finding, and the difference does not look like
              noise read as signal: {signal.reason}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
