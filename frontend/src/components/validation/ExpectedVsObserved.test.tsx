/**
 * plan_7 step 19, part 2: what the GEO metadata led us to expect, against what we actually saw.
 *
 * `ValidationEvidenceTable` already renders the paper-CLAIMED against our-COMPUTED metrics, with
 * verdict chips, unit reconciliation, tolerance and the advisory flag, and it is not rebuilt here.
 * The DESIRED STATE asks for a second comparison it has no home for: the species, the sample count
 * and the condition structure the deposit itself declared, against what the acquired data turned
 * out to be.
 *
 * This is the sibling, not a replacement.
 */
import { render, screen } from "@testing-library/react";

import { ExpectedVsObserved, type ExpectedEvidence } from "./ExpectedVsObserved";

const evidence = (over: Partial<ExpectedEvidence> = {}): ExpectedEvidence => ({
  precompute_checks: {
    species_matches: {
      check: "species_matches",
      verdict: "ok",
      detail: "the paper and the deposit both name Homo sapiens",
      blocking: false,
      decided_by: "measurement",
      model: null,
      reason: "",
      confidence: 0,
    },
    sample_data_matches_paper: {
      check: "sample_data_matches_paper",
      verdict: "mismatch",
      detail: "The paper describes 21 sample(s) and the deposit holds usable files for 5.",
      blocking: false,
      decided_by: "measurement",
      model: null,
      reason: "",
      confidence: 0,
    },
  },
  deposit_inspection: {
    n_rows: 37248,
    n_columns: 6,
    columns: ["CTRL_1", "CTRL_2", "CTRL_3", "KD_1", "KD_2", "KD_3"],
    value_type_observed: "counts",
    value_type_claimed: "counts",
    library_size_ratio: 1.0,
    usable: true,
    unusable_reason: null,
  },
  deposit_metadata_association: [
    { column: "CTRL_1", condition: "Control", source: "metadata_file", confidence: 1.0 },
    { column: "KD_1", condition: "KD", source: "column_name", confidence: 0.7 },
  ],
  ...over,
});

describe("ExpectedVsObserved", () => {
  it("renders nothing before anything has been measured", () => {
    const { container } = render(<ExpectedVsObserved evidence={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows what the deposit turned out to be", () => {
    render(<ExpectedVsObserved evidence={evidence()} />);
    expect(screen.getByText(/37,248/)).toBeInTheDocument();
    expect(screen.getByText(/counts/)).toBeInTheDocument();
  });

  it("shows the species we expected beside what the deposit declared", () => {
    render(<ExpectedVsObserved evidence={evidence()} />);
    expect(screen.getByText(/Homo sapiens/)).toBeInTheDocument();
  });

  it("shows a sample-count mismatch rather than hiding it behind a verdict", () => {
    render(<ExpectedVsObserved evidence={evidence()} />);
    expect(screen.getByText(/describes 21 sample\(s\)/)).toBeInTheDocument();
  });

  it("shows the condition structure and where each column's condition came from", () => {
    render(<ExpectedVsObserved evidence={evidence()} />);
    expect(screen.getByText("CTRL_1")).toBeInTheDocument();
    expect(screen.getByText(/metadata file/i)).toBeInTheDocument();
  });

  it("says when a column's condition could not be resolved", () => {
    render(
      <ExpectedVsObserved
        evidence={evidence({
          deposit_metadata_association: [
            { column: "S7", condition: null, source: "unresolved", confidence: 0.0 },
          ],
        })}
      />,
    );
    // Twice: the condition itself and the source that failed to resolve it.
    expect(screen.getAllByText(/unresolved/i).length).toBeGreaterThan(0);
  });

  it("renders the deposit numbers even when no checks ran", () => {
    render(<ExpectedVsObserved evidence={evidence({ precompute_checks: null })} />);
    expect(screen.getByText(/37,248/)).toBeInTheDocument();
  });
});
