/**
 * plan_7 step 19, part 3: whether the computational methods are clearly defined and reproducible,
 * and whether the code executes correctly.
 *
 * **Observation first, then explanation, and they are visibly different things.** What was
 * attempted, what happened, and only then what might explain it, hedged, with its confidence and
 * the model that assessed it. Candidate explanations include bioAF's own input mapping, arguments
 * and environment, and the section is capable of saying "the difference may be ours" without
 * attributing anything to the paper.
 *
 * **Four distinct findings, not one.** "Did not execute", "executed and produced nothing",
 * "executed and produced output we cannot compare", and "executed and produced a comparable result"
 * are four different statements about a paper, and today's feature reports all four as
 * `inconclusive`.
 */
import { render, screen } from "@testing-library/react";

import { CodeSection, type CodeEvidence } from "./CodeSection";

const evidence = (over: Partial<CodeEvidence> = {}): CodeEvidence => ({
  code_resolution: {
    outcome: "resolved",
    kind: "github",
    url: "https://github.com/lab/paper",
    commit_sha: "abc1234def",
    reason: "pinned",
    attempts: [],
  },
  code_execution: {
    attempt: 1,
    method: "authors_code",
    qualifiers: [],
    entry_point: "scripts/run_deseq2.R",
    outcome: "ran_output_agrees",
    reason: "reproduced",
    source: { repo_url: "https://github.com/lab/paper", commit_sha: "abc1234def" },
    observation: {
      outcome: "ran_output_agrees",
      qualifiers: [],
      exit_code: 0,
      transcript_uri: null,
      transcript_tail: "",
    },
  },
  execution_assessment: null,
  signal_assessment: null,
  generated_analysis: null,
  precompute_checks: null,
  ...over,
});

describe("CodeSection", () => {
  it("renders nothing for a study that never looked for code", () => {
    const { container } = render(<CodeSection evidence={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names what was attempted: which source, which commit, which entry point", () => {
    render(<CodeSection evidence={evidence()} />);
    expect(screen.getByText(/github\.com\/lab\/paper/)).toBeInTheDocument();
    expect(screen.getByText(/abc1234def/)).toBeInTheDocument();
    expect(screen.getByText(/scripts\/run_deseq2\.R/)).toBeInTheDocument();
  });

  it("says the code does not execute in words rather than as a token", () => {
    render(
      <CodeSection
        evidence={evidence({
          code_execution: {
            ...evidence().code_execution!,
            outcome: "dependency_unresolvable",
            observation: {
              outcome: "dependency_unresolvable",
              qualifiers: [],
              exit_code: 1,
              transcript_uri: "gs://x/log",
              transcript_tail: "ERROR: Could not find a version that satisfies DESeq2",
            },
          },
        })}
      />,
    );
    expect(screen.getByText(/dependencies would not install/i)).toBeInTheDocument();
    expect(screen.queryByText("dependency_unresolvable")).not.toBeInTheDocument();
  });

  it("distinguishes producing nothing from producing something uncomparable", () => {
    const nothing = render(
      <CodeSection
        evidence={evidence({
          code_execution: { ...evidence().code_execution!, outcome: "ran_no_output" },
        })}
      />,
    );
    expect(screen.getByText(/wrote nothing/i)).toBeInTheDocument();
    nothing.unmount();

    render(
      <CodeSection
        evidence={evidence({
          code_execution: { ...evidence().code_execution!, outcome: "ran_output_uncomparable" },
        })}
      />,
    );
    expect(screen.getByText(/limitation of bioAF/i)).toBeInTheDocument();
  });

  it("shows the paper's number beside ours whenever they diverge", () => {
    render(
      <CodeSection
        evidence={evidence({
          code_execution: {
            ...evidence().code_execution!,
            outcome: "ran_output_diverges",
            observation: {
              outcome: "ran_output_diverges",
              qualifiers: [],
              exit_code: 0,
              transcript_uri: null,
              transcript_tail: "",
              metric: "peak_count",
              paper_value: 7389,
              our_value: 4054,
            },
          },
        })}
      />,
    );
    expect(screen.getByText(/7,?389/)).toBeInTheDocument();
    expect(screen.getByText(/4,?054/)).toBeInTheDocument();
  });

  it("puts the explanation after the observation and marks it as a candidate", () => {
    render(
      <CodeSection
        evidence={evidence({
          execution_assessment: {
            candidate: "bioaf_input_mapping",
            candidates_offered: ["bioaf_input_mapping", "code_defect", "unresolved"],
            reason: "we chose which deposited file to mount and how its columns map",
            confidence: 0.6,
            model: "claude-opus-4-8",
            assessed_at: "2026-09-07T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getByText(/what happened/i)).toBeInTheDocument();
    expect(screen.getByText(/what might explain it/i)).toBeInTheDocument();
    // Named in the verdict line AND among the candidates considered, which is the point: the
    // reader sees that our own side was weighed rather than assumed away.
    expect(screen.getAllByText(/bioAF's own choice of input/i).length).toBeGreaterThan(0);
  });

  it("can say the difference may be ours without attributing anything to the paper", () => {
    render(
      <CodeSection
        evidence={evidence({
          execution_assessment: {
            candidate: "bioaf_input_mapping",
            candidates_offered: [],
            reason: "our column mapping is the cheapest explanation to check",
            confidence: 0.6,
            model: "m",
            assessed_at: "2026-09-07T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getAllByText(/bioAF's own choice of input/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/the authors were wrong/i)).not.toBeInTheDocument();
  });

  it("says the cause is unresolved rather than inventing one", () => {
    render(
      <CodeSection
        evidence={evidence({
          execution_assessment: {
            candidate: null,
            candidates_offered: ["bioaf_input_mapping", "code_defect"],
            reason: "the evidence does not reach any of these",
            confidence: 0.3,
            model: "m",
            assessed_at: "2026-09-07T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getByText(/could not be resolved/i)).toBeInTheDocument();
  });

  it("shows a possible-noise flag hedged, never as the verdict", () => {
    render(
      <CodeSection
        evidence={evidence({
          signal_assessment: {
            verdict: "likely",
            reason: "weak enrichment and a small number of events",
            confidence: 0.7,
            model: "claude-opus-4-8",
            assessed_at: "2026-09-07T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getByText(/possible issue/i)).toBeInTheDocument();
    expect(screen.getByText(/may/i)).toBeInTheDocument();
  });

  it("reports plainly that we could not reproduce when noise is not likely", () => {
    render(
      <CodeSection
        evidence={evidence({
          signal_assessment: {
            verdict: "not likely",
            reason: "the paper's number looks like a real measurement",
            confidence: 0.8,
            model: "m",
            assessed_at: "2026-09-07T00:00:00Z",
          },
        })}
      />,
    );
    expect(screen.getByText(/could not reproduce/i)).toBeInTheDocument();
    expect(screen.queryByText(/possible issue/i)).not.toBeInTheDocument();
  });

  it("names the model and lists the assumptions for a generated analysis", () => {
    render(
      <CodeSection
        evidence={evidence({
          code_execution: {
            ...evidence().code_execution!,
            method: "llm_from_methods",
            qualifiers: ["generated_from_prose", "methods_inadequate"],
            source: {
              generated_by_model: "claude-opus-4-8",
              generated_source_uri: "gs://x/generated_analysis.R",
              assumptions: ["the paper does not state the FDR threshold, so 0.05 was assumed"],
            },
          },
        })}
      />,
    );
    expect(screen.getByText(/claude-opus-4-8/)).toBeInTheDocument();
    expect(screen.getByText(/0\.05 was assumed/)).toBeInTheDocument();
  });

  it("reports a generated run that agrees as agreement under a stated limitation", () => {
    render(
      <CodeSection
        evidence={evidence({
          code_execution: {
            ...evidence().code_execution!,
            method: "llm_from_methods",
            outcome: "ran_output_agrees",
            qualifiers: ["generated_from_prose", "methods_inadequate"],
            source: { generated_by_model: "m", assumptions: [] },
          },
        })}
      />,
    );
    expect(screen.getByText(/description was thin/i)).toBeInTheDocument();
    // In the heading and in the attribution line: the report says twice, in different places, that
    // this was not the authors' own analysis.
    expect(screen.getAllByText(/generated from the paper/i).length).toBeGreaterThan(0);
  });

  it("says whether the methods were clearly described, from the pre-compute check", () => {
    render(
      <CodeSection
        evidence={evidence({
          precompute_checks: {
            methods_detailed_enough: {
              check: "methods_detailed_enough",
              verdict: "mismatch",
              detail: "no aligner, genome build or threshold is named",
              blocking: false,
              decided_by: "model",
              model: "m",
              reason: "no aligner, genome build or threshold is named",
              confidence: 0.8,
            },
          },
        })}
      />,
    );
    expect(screen.getByText(/no aligner, genome build or threshold is named/)).toBeInTheDocument();
  });

  it("says plainly when the paper published no code at all", () => {
    render(
      <CodeSection
        evidence={{
          code_resolution: { outcome: "code_absent", reason: "This paper names no analysis code.", attempts: [] },
        }}
      />,
    );
    expect(screen.getByText(/names no analysis code/i)).toBeInTheDocument();
  });
});
