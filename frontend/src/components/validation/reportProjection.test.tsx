/**
 * change_7.3 sections 10 and 11: the report, rendered from the backend's projection.
 *
 * `__fixtures__/reportContract.json` is written by the backend's `test_report_contract.py` from the
 * real projection code, so these tests render what the API actually returns. The Groff fixture is
 * study 34's situation on the current build: the attachment bundle 404s on every attempt, the raw
 * reads sit under controlled access in EGA, and nothing executes.
 */
import { render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary } from "@/lib/validationReport";
import { AiDecisionList } from "./AiDecisionList";
import { CapabilityChecklist, type Capabilities } from "./CapabilityChecklist";
import { CompletionSummary } from "./CompletionSummary";
import { PrecomputeChecksPanel } from "./PrecomputeChecksPanel";
import { SupplementInventory } from "./SupplementInventory";
import { TechnicalDetails } from "./TechnicalDetails";
import { ValidationEvidenceTable } from "./ValidationEvidenceTable";
import { ValidationIssuesSection } from "./ValidationIssuesSection";
import { ValidationStudyOutcome } from "./ValidationStudyOutcome";

const groff = contract.groff_failed as unknown as ReportSummary;
const legacy = contract.study_34_legacy as unknown as ReportSummary;
const attempted = contract.attempted_no_verdict as unknown as ReportSummary;
const mappingUnresolved = contract.mapping_unresolved as unknown as ReportSummary;

describe("the headline follows the reproduction attempt (item 1)", () => {
  it("says reproduction was not attempted when nothing executed", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={null} classification="access_restricted" summary={groff} />,
    );
    expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument();
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
  });

  it("follows it with the actual limitations, not the bucket name", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={null} classification="missing_data" summary={legacy} />,
    );
    expect(screen.queryByText("Missing data")).not.toBeInTheDocument();
    expect(screen.getByText(/EGAS00001003667/)).toBeInTheDocument();
  });

  it("keeps Could Not Reproduce for a study that ran and reached no verdict", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={null} classification="inconclusive" summary={attempted} />,
    );
    expect(screen.getByText("Could Not Reproduce")).toBeInTheDocument();
    expect(screen.queryByText("Reproduction not attempted")).not.toBeInTheDocument();
  });

  it("renders the summary sentences under the headline (item 2)", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={null} classification="access_restricted" summary={groff} />,
    );
    for (const sentence of groff.summary) {
      expect(screen.getByText(sentence, { exact: false })).toBeInTheDocument();
    }
  });
});

describe("what the paper attached (items 3 and 4)", () => {
  it("never renders undefined rows", () => {
    const { container } = render(<SupplementInventory summary={groff} supplements={[]} />);
    expect(container.textContent).not.toMatch(/undefined/);
  });

  it("shows one notice for one failure, listing the attachments it affected", () => {
    render(<SupplementInventory summary={groff} supplements={[]} />);
    const notices = screen.getAllByText(/could not download the paper's supplementary files in this attempt/i);
    expect(notices).toHaveLength(1);
    const notice = notices[0].closest("[data-testid='retrieval-failure']") as HTMLElement;
    expect(within(notice).getByText(/Supplemental File S2/)).toBeInTheDocument();
  });

  it("puts the status, URL and attempts under a collapsed Technical details element", () => {
    render(<SupplementInventory summary={groff} supplements={[]} />);
    const details = screen.getByText("Technical details").closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("404")).toBeInTheDocument();
    expect(within(details).getByText(/supplementaryFiles/)).toBeInTheDocument();
  });

  it("lists each attachment once, with its retrieval status", () => {
    render(<SupplementInventory summary={groff} supplements={[]} />);
    for (const artifact of groff.artifacts) {
      expect(screen.getByTestId(`artifact-${artifact.identity}`)).toHaveTextContent(artifact.retrieval.label);
    }
  });

  it("keeps the index page and figures out of the attachments", () => {
    render(<SupplementInventory summary={groff} supplements={[]} />);
    expect(screen.queryByTestId("artifact-supp_29_10_1705__index.html")).not.toBeInTheDocument();
  });

  it("shows a row count only for an inspected table", () => {
    render(
      <SupplementInventory
        supplements={[
          { label: "Supplemental File S2", filename: "s2.docx", role: "code", resolved: true, row_count: null, columns: null, threshold_splits: null, size_bytes: 10, failure_reason: null },
          { label: "Supplemental File S9", filename: null, role: "unknown", resolved: false, columns: null, threshold_splits: null, size_bytes: null, failure_reason: null } as never,
        ]}
      />,
    );
    expect(document.body.textContent).not.toMatch(/undefined rows|null rows/);
  });
});

describe("what could and could not be established (items 4 and 12)", () => {
  it("shows tri-state facts with the reason", () => {
    render(<CompletionSummary completion={null} summary={groff} />);
    const processed = screen.getByTestId("fact-processed_results_available");
    expect(processed).toHaveTextContent("Not established");
    expect(processed).toHaveTextContent(/not inspected/);
  });

  it("names the acquisition fact for what it is", () => {
    // change_7.4 section 1.3 (flagged test change): acquisition is one of three facts now.
    render(<CompletionSummary completion={null} summary={groff} />);
    expect(screen.getByText("Analysis input acquired")).toBeInTheDocument();
  });

  it("separates acquired, usable and ready for analysis (change_7.4 section 1.3)", () => {
    render(<CompletionSummary completion={null} summary={groff} />);
    for (const key of ["input_acquired", "input_usable", "ready_for_analysis"]) {
      const fact = groff.completion_facts.find((f) => f.key === key);
      expect(fact).toBeDefined();
      const row = screen.getByTestId(`fact-${key}`);
      expect(row).toHaveTextContent(fact!.label);
      expect(row).toHaveTextContent(fact!.value_label);
    }
    expect(screen.getByText("Analysis input usable")).toBeInTheDocument();
    expect(screen.getByText("Ready for analysis")).toBeInTheDocument();
  });

  it("labels a retrieval failure as this attempt's, not as a missing input", () => {
    render(<CompletionSummary completion={null} summary={groff} />);
    expect(screen.getAllByText("Could not be retrieved in this attempt").length).toBeGreaterThan(0);
    expect(screen.queryByText("Required input not published")).not.toBeInTheDocument();
  });

  it("reports the route that was not chosen as context", () => {
    render(<CompletionSummary completion={null} summary={groff} />);
    expect(screen.getByTestId("limitation-context-pipeline")).toHaveTextContent(/not chosen/i);
  });

  it("does not repeat one failure per attachment", () => {
    render(<CompletionSummary completion={null} summary={legacy} />);
    expect(screen.getAllByText(/could not be retrieved in this attempt/i)).toHaveLength(1);
  });

  it("reads a legacy boolean No beside an uninspected attachment as not established", () => {
    render(<CompletionSummary completion={null} summary={legacy} />);
    expect(screen.getByTestId("fact-processed_results_available")).toHaveTextContent("Not established");
  });
});

describe("the claims at the gate (items 5, 6 and 7)", () => {
  const row = {
    metric_key: "de_gene_count",
    bound_key: null,
    resolved: false,
    reason: "a DE gene count is not a controlled metric",
    confidence: 0.97,
    model: "claude-opus-4-8",
    decided_by: "model",
    low_confidence: false,
    claim_text: "We identified 194 significantly differentially expressed genes",
    claimed_value: 194,
    unit: "genes",
    population: "whole embryo XX vs XY",
    contrast: "XX vs XY WE",
    cutoff: "padj < 0.05",
    mapping_status: "no_supported_metric",
    mapping_label: "No supported metric",
    mapping_explanation:
      "bioAF has no metric that measures this claim, so the mapping was declined and the claim cannot be compared.",
  };

  it("counts mapped claims without calling them tested", () => {
    render(<AiDecisionList decisions={[{ ...row, bound_key: "total_sequences", resolved: true }, row]} testedCount={0} />);
    expect(screen.getByText(/1 claim mapped to candidate comparison metrics; none tested\./)).toBeInTheDocument();
  });

  it("says No supported metric and why", () => {
    render(<AiDecisionList decisions={[row]} />);
    expect(screen.getByText("No supported metric")).toBeInTheDocument();
    expect(screen.getByText(/bioAF has no metric that measures this claim/)).toBeInTheDocument();
  });

  it("leads with the claim's science", () => {
    render(<AiDecisionList decisions={[row]} />);
    expect(screen.getByText(/We identified 194 significantly/)).toBeInTheDocument();
    expect(screen.getByText(/whole embryo XX vs XY/)).toBeInTheDocument();
    expect(screen.getByText(/XX vs XY WE/)).toBeInTheDocument();
    expect(screen.getByText(/padj < 0.05/)).toBeInTheDocument();
  });

  it("keeps the internal keys and the model under collapsed Binding details", () => {
    render(<AiDecisionList decisions={[row]} />);
    const details = screen.getByText("Binding details").closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("de_gene_count")).toBeInTheDocument();
    expect(within(details).getByText(/claude-opus-4-8/)).toBeInTheDocument();
  });
});

describe("the evidence table's empty state (item 8)", () => {
  it("says there are no execution results because nothing was attempted", () => {
    render(<ValidationEvidenceTable evidence={null} attemptStatus="not_attempted" />);
    expect(screen.getByText("No execution results: reproduction was not attempted.")).toBeInTheDocument();
  });
});

describe("what this paper has (item 11)", () => {
  it("separates deposited from available to bioAF", () => {
    render(<CapabilityChecklist capabilities={{} as Capabilities} rows={groff.capability_rows} />);
    expect(screen.getByTestId("capability-raw_data")).toHaveTextContent("Raw sample data depositedYes");
    expect(screen.getByTestId("capability-raw_data_available")).toHaveTextContent("Raw sample data available to bioAFNo");
    expect(screen.queryByText("Raw sample data available")).not.toBeInTheDocument();
  });
});

describe("the issues list (section 9)", () => {
  it("labels every outcome the backend can record", () => {
    for (const outcome of Object.keys(contract.enums.issue_outcome)) {
      const { unmount } = render(
        <ValidationIssuesSection issues={[{ step: "s", outcome, impact: "degraded", message: "m", model: null, at: null }]} />,
      );
      expect(screen.queryByText(outcome)).not.toBeInTheDocument();
      unmount();
    }
  });

  it("puts technical detail under a collapsed element", () => {
    render(
      <ValidationIssuesSection
        issues={[
          {
            step: "retrieving the paper's supplementary files",
            outcome: "retrieval_failed",
            impact: "degraded",
            message: "bioAF could not download the paper's supplementary files in this attempt.",
            model: null,
            at: null,
            technical_detail: { http_status: 404, attempts: 3 },
          },
        ]}
      />,
    );
    const details = screen.getByText("Technical details").closest("details") as HTMLElement;
    expect(within(details).getByText(/404/)).toBeInTheDocument();
  });
});

describe("the read-time checks are provisional until inspected (section 6)", () => {
  it("never renders a paper-text judgment as a settled Yes", () => {
    render(
      <PrecomputeChecksPanel
        checks={{
          samples_described_enough: {
            check: "samples_described_enough",
            verdict: "ok",
            detail: "supplemental files list per-sample metadata",
            blocking: false,
            decided_by: "model",
            model: "m",
            reason: "",
            confidence: 0.7,
            basis: "paper_text",
          },
        }}
        onOverride={() => undefined}
      />,
    );
    expect(screen.getByText(/provisional/i)).toBeInTheDocument();
    expect(screen.getByText(/not checked against the attachments/)).toBeInTheDocument();
  });
});

describe("TechnicalDetails", () => {
  it("is collapsed and lists each detail", () => {
    render(<TechnicalDetails detail={{ url: "https://x/y", http_status: 404, attempts: null }} />);
    const details = screen.getByText("Technical details").closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText(/https:\/\/x\/y/)).toBeInTheDocument();
    expect(within(details).queryByText(/attempts/)).not.toBeInTheDocument();
  });
});

describe("every vocabulary in the contract has a label (section 11)", () => {
  it("the limitation, role and retrieval labels come from the projection, so none is missing", () => {
    for (const group of ["limitation_kind", "role", "retrieval_status", "issue_outcome", "tristate"] as const) {
      for (const label of Object.values(contract.enums[group])) {
        expect(typeof label).toBe("string");
        expect((label as string).length).toBeGreaterThan(0);
      }
    }
  });
});

describe("the studies list follows the attempt too (item 1)", () => {
  it("reads Reproduction not attempted from the attempt alone", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={null} classification="access_restricted" attempt="not_attempted" />,
    );
    expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument();
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
  });

  it("keeps Could Not Reproduce for an attempt with no verdict", () => {
    render(<ValidationStudyOutcome state="classified" confidence={null} classification="inconclusive" attempt="attempted" />);
    expect(screen.getByText("Could Not Reproduce")).toBeInTheDocument();
  });
});


describe("an input acquired and read that could not be assigned (change_7.4 sections 1.1 and 1.3)", () => {
  it("names the limitation for what failed, with the file in its detail", () => {
    render(<CompletionSummary completion={null} summary={mappingUnresolved} />);
    const row = screen.getByTestId("limitation-sample_mapping_unresolved");
    expect(row).toHaveTextContent("Samples could not be assigned to the comparison");
    expect(row).toHaveTextContent("GSE1_counts.tsv.gz");
    expect(row).not.toHaveTextContent(/could not reach/i);
  });

  it("keeps the typed cause under a collapsed Technical details element", () => {
    render(<CompletionSummary completion={null} summary={mappingUnresolved} />);
    const row = screen.getByTestId("limitation-sample_mapping_unresolved");
    const details = within(row).getByText("Technical details").closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("sample_mapping_unresolved")).toBeInTheDocument();
  });

  it("says the input was acquired and usable, and not ready", () => {
    render(<CompletionSummary completion={null} summary={mappingUnresolved} />);
    expect(screen.getByTestId("fact-input_acquired")).toHaveTextContent("Yes");
    expect(screen.getByTestId("fact-input_acquired")).toHaveTextContent("GSE1_counts.tsv.gz");
    expect(screen.getByTestId("fact-input_usable")).toHaveTextContent("Yes");
    expect(screen.getByTestId("fact-ready_for_analysis")).toHaveTextContent("No");
  });
});

describe("the stated cutoff, in one vocabulary (change_7.5 section 1.2)", () => {
  it("renders a claim's cutoff in the projection's words", () => {
    const claim = groff.claims.find((c) => c.cutoff);
    expect(claim?.cutoff).toBe("adjusted P 0.05");
    render(
      <AiDecisionList
        decisions={[
          {
            metric_key: claim?.mapping.metric_key ?? null,
            bound_key: null,
            resolved: false,
            reason: claim?.mapping.reason ?? null,
            confidence: null,
            model: null,
            decided_by: "model",
            low_confidence: false,
            claim_text: claim?.description ?? "",
            claimed_value: claim?.value ?? null,
            unit: claim?.unit ?? null,
            population: claim?.population ?? null,
            contrast: claim?.contrast ?? null,
            cutoff: claim?.cutoff ?? null,
            mapping_status: claim?.mapping.status ?? "",
            mapping_label: claim?.mapping.label ?? "",
            mapping_explanation: claim?.mapping.explanation ?? null,
          },
        ]}
      />,
    );
    expect(screen.getByText(/adjusted P 0\.05/)).toBeInTheDocument();
  });

  it("states each contrast's cutoff in words, never as the legacy pair", () => {
    for (const contrast of groff.contrasts) {
      expect(contrast).not.toHaveProperty("thresholds");
      expect(contrast).toHaveProperty("cutoff");
    }
  });
});
