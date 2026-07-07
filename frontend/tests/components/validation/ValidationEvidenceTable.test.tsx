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
