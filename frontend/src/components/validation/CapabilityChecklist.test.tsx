/**
 * plan_7 steps 13 and 19: what this paper actually has.
 *
 * The route modal used to offer all three routes blind, so a person could pick the deposited-data
 * route on a paper with no deposited matrix and find out only after approving.
 *
 * UNKNOWN is rendered AS UNKNOWN. A checklist that shows NO for a GEO timeout tells the reader
 * something false about the paper, which is the one thing this exists to avoid. Existence and
 * accessibility are separate rows, because a repository can be known and dead.
 */
import { render, screen, within } from "@testing-library/react";

import { CapabilityChecklist, type Capabilities } from "./CapabilityChecklist";

const caps = (over: Partial<Capabilities> = {}): Capabilities => ({
  paper_readable: { value: "yes", evidence: "bioAF holds the paper's full text", failure_reason: null },
  geo_entry: { value: "yes", evidence: "GEO published a series record", failure_reason: null },
  raw_data: { value: "yes", evidence: "ENA publishes FASTQ for 6 runs", failure_reason: null },
  preprocessed_data: { value: "yes", evidence: "1 of 3 files could serve", failure_reason: null },
  sample_metadata: { value: "yes", evidence: "6 samples", failure_reason: null },
  code_artifact: { value: "no", evidence: null, failure_reason: null },
  code_repository: { value: "yes", evidence: "https://github.com/lab/paper", failure_reason: null },
  code_sources: [
    {
      kind: "github",
      url: "https://github.com/lab/paper",
      identifier: null,
      exists: "yes",
      accessible: "not_attempted",
      accessible_reason: null,
    },
  ],
  ...over,
});

describe("CapabilityChecklist", () => {
  it("renders nothing before discovery has run", () => {
    const { container } = render(<CapabilityChecklist capabilities={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("answers every phase-1 question", () => {
    render(<CapabilityChecklist capabilities={caps()} />);
    for (const label of [
      /GEO entry exists/i,
      /Raw sample data available/i,
      /Pre-processed data available/i,
      /Sample metadata available/i,
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders UNKNOWN as UNKNOWN, not as NO", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          geo_entry: { value: "unknown", evidence: null, failure_reason: "bioAF could not reach GEO" },
        })}
      />,
    );
    const row = screen.getByText(/GEO entry exists/i).closest("tr")!;
    expect(within(row).getByText(/unknown/i)).toBeInTheDocument();
    expect(within(row).queryByText(/^no$/i)).not.toBeInTheDocument();
  });

  it("shows why something is unknown, so a discovery failure reads as ours not the paper's", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          preprocessed_data: {
            value: "unknown",
            evidence: null,
            failure_reason: "GEO did not return a supplementary listing",
          },
        })}
      />,
    );
    expect(screen.getByText(/GEO did not return a supplementary listing/)).toBeInTheDocument();
  });

  it("labels a code source by its kind rather than a bare yes", () => {
    render(<CapabilityChecklist capabilities={caps()} />);
    expect(screen.getByText(/GitHub/)).toBeInTheDocument();
  });

  it("lists every code source when a paper names more than one", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          code_sources: [
            {
              kind: "zenodo",
              url: "https://zenodo.org/record/1",
              identifier: null,
              exists: "yes",
              accessible: "no",
              accessible_reason: "the record 404s",
            },
            {
              kind: "github",
              url: "https://github.com/lab/paper",
              identifier: null,
              exists: "yes",
              accessible: "yes",
              accessible_reason: null,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/zenodo\.org\/record\/1/)).toBeInTheDocument();
    expect(screen.getByText(/github\.com\/lab\/paper/)).toBeInTheDocument();
  });

  it("keeps existence and accessibility as separate answers", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          code_sources: [
            {
              kind: "github",
              url: "https://github.com/lab/private",
              identifier: null,
              exists: "yes",
              accessible: "no",
              accessible_reason: "the repository is private",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/the repository is private/)).toBeInTheDocument();
    expect(screen.getByText(/not accessible/i)).toBeInTheDocument();
  });

  it("says accessibility has not been attempted rather than implying failure", () => {
    render(<CapabilityChecklist capabilities={caps()} />);
    expect(screen.getByText(/not attempted/i)).toBeInTheDocument();
  });

  it("says plainly when a paper published no code at all", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({ code_artifact: { value: "no", evidence: null, failure_reason: null }, code_repository: { value: "no", evidence: null, failure_reason: null }, code_sources: [] })}
      />,
    );
    expect(screen.getByText(/no code source/i)).toBeInTheDocument();
  });
});
