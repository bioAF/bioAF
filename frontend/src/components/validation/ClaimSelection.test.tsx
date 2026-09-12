/**
 * change_7.5 stage 2: the paper's resources, its experiments, each claim's four checks, and the one
 * claim and check this run selected. Rendered from the backend's projection
 * (`__fixtures__/reportContract.json`, example `stage2_selection`), so the words are the backend's.
 *
 * The labels are pending the owner's sign-off, item by item.
 */
import { render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary } from "@/lib/validationReport";
import { ClaimSelection } from "./ClaimSelection";
import { ResourceInventory } from "./ResourceInventory";

const stage2 = contract.stage2_selection as unknown as ReportSummary;
const legacy = contract.study_34_legacy as unknown as ReportSummary;

describe("ResourceInventory (section 2.1)", () => {
  it("lists every resource with its type and what bioAF can do with it", () => {
    render(<ResourceInventory summary={stage2} />);
    const geo = screen.getByText("GSE555001").closest("tr") as HTMLElement;
    expect(within(geo).getByText("Sequencing data")).toBeInTheDocument();
    expect(within(geo).getByText("e2")).toBeInTheDocument();
    const pride = screen.getByText("PXD099001").closest("tr") as HTMLElement;
    expect(within(pride).getByText("Proteomics data")).toBeInTheDocument();
    expect(within(pride).getByText(/no PRIDE adapter/)).toBeInTheDocument();
    expect(screen.getByText("Retrievable by bioAF")).toBeInTheDocument();
    expect(screen.getByText("Analyzable by bioAF")).toBeInTheDocument();
  });

  it("renders nothing for a report with no resources", () => {
    const { container } = render(<ResourceInventory summary={legacy} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("ClaimSelection (sections 2.2, 2.5 and 2.6)", () => {
  it("states the selected claim, its check, the workflow and who decided", () => {
    render(<ClaimSelection summary={stage2} />);
    const selected = screen.getByTestId("claim-selection-current");
    expect(within(selected).getByText(/Reanalysis of processed data/)).toBeInTheDocument();
    expect(within(selected).getByText(/nf-core\/rnaseq/)).toBeInTheDocument();
    expect(within(selected).getByText(/revision 2/)).toBeInTheDocument();
    expect(within(selected).getByText(/the authors' table can be compared/)).toBeInTheDocument();
  });

  it("shows every claim's four checks with their status and reason", () => {
    render(<ClaimSelection summary={stage2} />);
    const row = screen.getByTestId("claim-1");
    expect(within(row).getByText("257 genes were up")).toBeInTheDocument();
    expect(within(row).getByText("Selected for this run")).toBeInTheDocument();
    // The four checks, not the consistency readings listed beneath them.
    const checks = within(within(row).getByTestId("claim-checks")).getAllByRole("listitem");
    expect(checks).toHaveLength(4);
    expect(within(checks[1]).getByText("Consistency with the authors' results")).toBeInTheDocument();
    expect(within(checks[1]).getByText("Available")).toBeInTheDocument();
    expect(within(checks[3]).getByText(/GENCODE M23/)).toBeInTheDocument();
  });

  it("marks unselected claims as not assessed, with the reason", () => {
    render(<ClaimSelection summary={stage2} />);
    const row = screen.getByTestId("claim-0");
    expect(within(row).getByText("Not assessed in this run")).toBeInTheDocument();
    expect(within(row).getByText(/not selected for this run; QC metric comparison/)).toBeInTheDocument();
  });

  it("shows each experiment's reference parts, never a default", () => {
    render(<ClaimSelection summary={stage2} />);
    const chip = screen.getByTestId("experiment-e1");
    expect(within(chip).getByText(/ChIP-seq/)).toBeInTheDocument();
    expect(within(chip).getByText("mm9")).toBeInTheDocument();
    expect(within(chip).getAllByText("Unavailable").length).toBeGreaterThan(0);
  });

  it("shows work computed for an earlier selection as history", () => {
    render(<ClaimSelection summary={stage2} />);
    expect(screen.getByText(/Computed for an earlier selection \(revision 1\)/)).toBeInTheDocument();
  });

  it("renders nothing for a plan read before experiments existed", () => {
    const { container } = render(<ClaimSelection summary={legacy} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("each claim's predicate, consistency and reanalysis (sections 3.1, 4.1 and 4.3)", () => {
  it("states the predicate, the consistency outcome with its readings, and the reanalysis beside the claim", () => {
    render(<ClaimSelection summary={stage2} />);
    const row = screen.getByTestId("claim-1");
    expect(within(row).getByText(/P < 0.01, either direction, no fold-change requirement/)).toBeInTheDocument();
    expect(within(row).getByText("Unresolved against the authors' results")).toBeInTheDocument();
    expect(within(row).getByText(/the table is KO over WT: 257/)).toBeInTheDocument();
    expect(within(row).getByText(/Deposited data/)).toBeInTheDocument();
    expect(within(row).getByText(/250 against the claim's exactly 257/)).toBeInTheDocument();
    expect(within(row).getByText(/different method/)).toBeInTheDocument();
  });

  it("shows no reanalysis on a claim it was not scored for", () => {
    render(<ClaimSelection summary={stage2} />);
    expect(within(screen.getByTestId("claim-2")).queryByText(/Deposited data/)).not.toBeInTheDocument();
  });
});
