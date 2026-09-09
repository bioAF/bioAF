/**
 * change_7.1 section 2: the paper's own attachments, and what each one turned out to hold.
 *
 * These were never discovered, so a paper that published its sample metadata, its complete R
 * analysis and its differential-results table was reported as publishing none of them.
 *
 * An unresolved reference renders as unresolved. "bioAF could not fetch it" and "the authors did
 * not publish it" are different statements, and only the second is a finding about the paper.
 */
import { render, screen, within } from "@testing-library/react";

import { SupplementInventory, type Supplement } from "./SupplementInventory";

const supplements: Supplement[] = [
  { label: "Supplemental File S1", filename: "s1.txt", role: "sample_metadata", resolved: true, row_count: 54,
    columns: ["ProcessingID", "Sampletype"], threshold_splits: null, failure_reason: null, size_bytes: 6257 },
  { label: "Supplemental File S2", filename: "s2.docx", role: "code", resolved: true, row_count: null,
    columns: null, threshold_splits: null, failure_reason: null, size_bytes: 97992 },
  { label: "Supplemental File S3", filename: "s3.txt", role: "results_table", resolved: true, row_count: 194,
    columns: ["log2FoldChange"], threshold_splits: { "abs_log2fc>2": 88 }, failure_reason: null, size_bytes: 37266 },
  { label: "Supplemental File S4", filename: null, role: "unknown", resolved: false, row_count: null,
    columns: null, threshold_splits: null, size_bytes: null,
    failure_reason: "bioAF could not find a file in the bundle matching this reference" },
];

describe("SupplementInventory", () => {
  it("renders nothing when the paper attached nothing", () => {
    const { container } = render(<SupplementInventory supplements={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names each attachment and what it holds", () => {
    render(<SupplementInventory supplements={supplements} />);
    expect(within(screen.getByTestId("supplement-Supplemental File S1")).getByText(/Sample metadata/)).toBeInTheDocument();
    expect(within(screen.getByTestId("supplement-Supplemental File S2")).getByText(/Analysis code/)).toBeInTheDocument();
    expect(
      within(screen.getByTestId("supplement-Supplemental File S3")).getByText(/Differential results/),
    ).toBeInTheDocument();
  });

  it("shows an unresolved reference as unresolved, not as absent", () => {
    render(<SupplementInventory supplements={supplements} />);
    const row = screen.getByTestId("supplement-Supplemental File S4");
    expect(within(row).getByText(/Not retrieved/)).toBeInTheDocument();
    expect(within(row).queryByText(/^No$/)).not.toBeInTheDocument();
  });

  it("shows the counts that distinguish one claim from another", () => {
    /* 194 rows and 88 above the fold-change cutoff are two different numbers the paper states in
       one sentence. */
    render(<SupplementInventory supplements={supplements} />);
    const row = screen.getByTestId("supplement-Supplemental File S3");
    expect(within(row).getByText(/194 rows/)).toBeInTheDocument();
    expect(within(row).getByText(/88 with abs_log2fc>2/)).toBeInTheDocument();
  });
});
