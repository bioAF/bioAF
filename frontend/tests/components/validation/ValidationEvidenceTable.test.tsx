import { render, screen, within } from "@testing-library/react";
import { ValidationEvidenceTable } from "@/components/validation/ValidationEvidenceTable";

describe("ValidationEvidenceTable", () => {
  it("shows an empty-state message when there is no evidence yet", () => {
    render(<ValidationEvidenceTable evidence={null} />);
    expect(screen.getByText(/no evidence/i)).toBeInTheDocument();
  });

  it("joins a claimed target to the computed metric that shares its key", () => {
    render(
      <ValidationEvidenceTable
        evidence={{
          comparison_targets: [{ metric_key: "total_sequences", claimed_value: 7000000, unit: "reads" }],
          computed_metrics: { total_sequences: 6600000 },
        }}
      />
    );
    const row = screen.getByText("total_sequences").closest("tr")!;
    expect(within(row).getByText("7000000")).toBeInTheDocument();
    expect(within(row).getByText("6600000")).toBeInTheDocument();
  });

  it("marks a claimed target with no computed counterpart as not reported (the key-vocab mismatch)", () => {
    render(
      <ValidationEvidenceTable
        evidence={{
          comparison_targets: [{ metric_key: "mean_raw_reads_per_sample", claimed_value: 7000000 }],
          computed_metrics: { total_sequences: 6600000 },
        }}
      />
    );
    const row = screen.getByText("mean_raw_reads_per_sample").closest("tr")!;
    expect(within(row).getByText(/not reported/i)).toBeInTheDocument();
  });

  it("renders the classifier's per-metric verdicts when the classifier has run", () => {
    render(
      <ValidationEvidenceTable
        evidence={{
          computed_metrics: { total_sequences: 6600000, percent_gc: 48 },
          classification_result: {
            classification: "validated",
            comparisons: [
              { metric_key: "total_reads", mapped_key: "total_sequences", claimed_value: 7000000, computed_value: 6600000, delta: -400000, verdict: "agree" },
              { metric_key: "mean_reads_after_trimming_per_sample", mapped_key: null, claimed_value: 5000000, computed_value: null, verdict: "not_computed" },
            ],
          },
        }}
      />
    );
    // The agreeing row shows its verdict chip and the mapped key it joined on.
    const agreeRow = screen.getByText("total_reads").closest("tr")!;
    expect(within(agreeRow).getByText("Agree")).toBeInTheDocument();
    expect(within(agreeRow).getByText(/total_sequences/)).toBeInTheDocument();
    // The uncomparable claim is flagged Not computed, not silently dropped.
    const gapRow = screen.getByText("mean_reads_after_trimming_per_sample").closest("tr")!;
    expect(within(gapRow).getByText("Not computed")).toBeInTheDocument();
    // A computed metric with no claim still surfaces under "other".
    expect(screen.getByText("percent_gc")).toBeInTheDocument();
  });

  it("marks an advisory (qualifier-stripped) peak-count row as Advisory, not Diverge, keeping the numbers", () => {
    // The paper's per-condition peak count maps to peak_count but is basis-sensitive (consensus vs
    // per-sample), so the classifier flags it advisory. The table must NOT show a red "Diverge" (which
    // reads as a paper failure); it shows "Advisory" while still surfacing claimed/computed/delta and
    // the mapping arrow so a human can judge the pairing.
    render(
      <ValidationEvidenceTable
        evidence={{
          computed_metrics: { peak_count: 31914 },
          classification_result: {
            classification: "validated",
            comparisons: [
              {
                metric_key: "peak_count_quiescent",
                mapped_key: "peak_count",
                advisory: true,
                claimed_value: 74834,
                computed_value: 31914,
                delta: -42920,
                verdict: "diverge",
              },
            ],
          },
        }}
      />
    );
    const row = screen.getByText("peak_count_quiescent").closest("tr")!;
    expect(within(row).getByText("Advisory")).toBeInTheDocument();
    expect(within(row).queryByText("Diverge")).not.toBeInTheDocument();
    // The numbers and the mapping are still visible so the human can assess the pairing.
    expect(within(row).getByText("74834")).toBeInTheDocument();
    expect(within(row).getByText("31914")).toBeInTheDocument();
    expect(within(row).getByText("→ peak_count")).toBeInTheDocument();
  });

  it("lists computed metrics that have no claimed target so a human can hand-map them", () => {
    render(
      <ValidationEvidenceTable
        evidence={{
          comparison_targets: [{ metric_key: "mean_raw_reads_per_sample", claimed_value: 7000000 }],
          computed_metrics: { total_sequences: 6600000, percent_gc: 48 },
        }}
      />
    );
    // total_sequences and percent_gc are computed-only; both should surface.
    expect(screen.getByText("percent_gc")).toBeInTheDocument();
    expect(screen.getByText("48")).toBeInTheDocument();
    expect(screen.getByText("total_sequences")).toBeInTheDocument();
  });
});
