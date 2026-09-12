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
  deposit_exists: { value: "yes", evidence: "GEO published a series record", failure_reason: null },
  deposits: [],
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
      /Data deposit exists/i,
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
          deposit_exists: { value: "unknown", evidence: null, failure_reason: "bioAF could not reach GEO" },
        })}
      />,
    );
    const row = screen.getByText(/Data deposit exists/i).closest("tr")!;
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

describe("CapabilityChecklist deposits (change_7.1 section 1)", () => {
  const ega = {
    archive: "ega",
    accession: "EGAS00001003667",
    provenance: "extracted",
    scoped: true,
    exists: "yes" as const,
    access: "controlled" as const,
    supported: "no" as const,
    raw_data: "yes" as const,
    preprocessed_data: "no" as const,
    sample_metadata: "yes" as const,
    evidence: "EGA dataset EGAD00001005044 is controlled access and registers 54 sample(s)",
    failure_reason: null,
  };

  it("names the archive a deposit lives in", () => {
    render(<CapabilityChecklist capabilities={caps({ deposits: [ega] })} />);
    const row = screen.getByTestId("deposit-EGAS00001003667");
    expect(within(row).getByText(/^\(EGA\)$/)).toBeInTheDocument();
  });

  it("keeps existence, access and support as three separate answers", () => {
    /* The paper deposited its reads AND bioAF cannot fetch them. One chip cannot say both, and
       the half it drops is the half that stops the reader blaming the authors. */
    render(<CapabilityChecklist capabilities={caps({ deposits: [ega] })} />);
    const row = screen.getByTestId("deposit-EGAS00001003667");
    expect(within(row).getByText(/^Yes$/)).toBeInTheDocument();
    expect(within(row).getByText(/^Controlled$/)).toBeInTheDocument();
    expect(within(row).getByText(/^bioAF cannot acquire$/)).toBeInTheDocument();
  });

  it("says where an accession came from when nobody requested it", () => {
    render(<CapabilityChecklist capabilities={caps({ deposits: [ega] })} />);
    expect(screen.getByText(/from the paper/i)).toBeInTheDocument();
  });

  it("renders a deposit bioAF could not look up as unknown, never as absent", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          deposits: [
            {
              ...ega,
              archive: "arrayexpress",
              accession: "E-MTAB-1234",
              exists: "unknown" as const,
              access: "unknown" as const,
              raw_data: "unknown" as const,
              evidence: null,
              failure_reason: "bioAF cannot look up deposits in arrayexpress",
            },
          ],
        })}
      />,
    );
    const row = screen.getByTestId("deposit-E-MTAB-1234");
    expect(within(row).getAllByText(/Unknown/i).length).toBeGreaterThan(0);
    expect(within(row).getByText(/cannot look up/i)).toBeInTheDocument();
  });
});

// change_7.5 section 1.5: every resource the paper names is listed, an archive bioAF has no adapter
// for is named as one, and a deposit past the lookup bound says it was not looked up.
describe("CapabilityChecklist names every resource (change_7.5 section 1.5)", () => {
  const unlooked = {
    archive: "pride",
    accession: "PXD000001",
    provenance: "text_scan",
    scoped: false,
    exists: "unknown" as const,
    access: "unknown" as const,
    supported: "no" as const,
    raw_data: "unknown" as const,
    preprocessed_data: "unknown" as const,
    sample_metadata: "unknown" as const,
    evidence: null,
    failure_reason: "bioAF has no adapter for PRIDE, so what PXD000001 holds is unknown",
  };

  it("names PRIDE and PDB as archives rather than as unrecognised ones", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          deposits: [unlooked, { ...unlooked, archive: "pdb", accession: "6ABC", failure_reason: "bioAF has no adapter for PDB" }],
        })}
      />,
    );
    expect(within(screen.getByTestId("deposit-PXD000001")).getByText(/^\(PRIDE\)$/)).toBeInTheDocument();
    expect(within(screen.getByTestId("deposit-6ABC")).getByText(/^\(PDB\)$/)).toBeInTheDocument();
    expect(within(screen.getByTestId("deposit-PXD000001")).getByText(/no adapter for PRIDE/)).toBeInTheDocument();
  });

  it("says a resource the text scan found came from the paper", () => {
    render(<CapabilityChecklist capabilities={caps({ deposits: [unlooked] })} />);
    expect(within(screen.getByTestId("deposit-PXD000001")).getByText(/from the paper/i)).toBeInTheDocument();
  });

  it("lists a deposit past the lookup bound as not looked up", () => {
    render(
      <CapabilityChecklist
        capabilities={caps({
          deposits: [{ ...unlooked, archive: "geo", accession: "GSE5", failure_reason: "Not looked up" }],
        })}
      />,
    );
    expect(within(screen.getByTestId("deposit-GSE5")).getByText(/Not looked up/)).toBeInTheDocument();
  });
});
