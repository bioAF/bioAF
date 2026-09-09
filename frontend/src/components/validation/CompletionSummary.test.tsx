/**
 * change_7.1 section 7: what could and could not be established, on screen.
 *
 * Study 32 showed one sentence: "No pre-processed data to reproduce the finding from is published
 * for this paper." A claim about the paper rather than the deposit, with no mention of the checks
 * that did run against the paper's own attachments, and no sign that more than one thing was
 * blocking at once.
 */
import { render, screen, within } from "@testing-library/react";

import { CompletionSummary, type Completion } from "./CompletionSummary";

const completion: Completion = {
  classification: "missing_data",
  reason: "EGAS00001003667 publishes no pre-processed data.",
  limitations: [
    {
      kind: "controlled_access",
      resource: "EGAS00001003667",
      operation: "pipeline",
      detail: "EGAS00001003667 publishes raw sequencing reads under controlled access",
    },
    {
      kind: "missing_input",
      resource: "EGAS00001003667",
      operation: "deposit",
      detail: "EGAS00001003667 publishes no pre-processed data",
    },
  ],
  processed_results_available: true,
  reproduction_input_available: false,
  checks_completed: ["Supplemental File S3: published results read, available for consistency checking"],
  checks_not_completed: ["Supplemental File S9: not retrieved"],
};

describe("CompletionSummary", () => {
  it("renders nothing before the assessment has completed", () => {
    const { container } = render(<CompletionSummary completion={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows every limitation, not just the first", () => {
    render(<CompletionSummary completion={completion} />);
    expect(screen.getByText("Controlled access")).toBeInTheDocument();
    expect(screen.getByText("Required input not published")).toBeInTheDocument();
  });

  it("names the resource and route each limitation affects", () => {
    render(<CompletionSummary completion={completion} />);
    const row = screen.getByTestId("limitation-controlled_access");
    expect(within(row).getAllByText(/EGAS00001003667/).length).toBeGreaterThan(0);
    expect(within(row).getByText("pipeline route")).toBeInTheDocument();
  });

  it("keeps published results separate from a reproduction input", () => {
    /* A results table proves the authors published processed results. Only a sample-level matrix
       is something to reproduce FROM, and collapsing the two is what produced a
       no-processed-results conclusion about a paper that published one. */
    render(<CompletionSummary completion={completion} />);
    expect(within(screen.getByTestId("fact-processed-results")).getByText("Yes")).toBeInTheDocument();
    expect(within(screen.getByTestId("fact-reproduction-input")).getByText("No")).toBeInTheDocument();
  });

  it("says what was checked and what was not", () => {
    render(<CompletionSummary completion={completion} />);
    expect(screen.getByText(/Supplemental File S3/)).toBeInTheDocument();
    expect(screen.getByText(/Supplemental File S9/)).toBeInTheDocument();
  });
});
