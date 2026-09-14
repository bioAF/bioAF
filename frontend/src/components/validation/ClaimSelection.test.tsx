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

  // plan_8_2 section 2.2: an unsupported archive stays visible with its link, and support is stated as
  // separate facts.
  it("links each resource to its archive and states recognition, metadata and access as their own facts", () => {
    render(<ResourceInventory summary={stage2} />);
    expect(screen.getByRole("link", { name: "PXD099001" })).toHaveAttribute(
      "href",
      "https://www.ebi.ac.uk/pride/archive/projects/PXD099001",
    );
    expect(screen.getByText("Recognized by bioAF")).toBeInTheDocument();
    expect(screen.getByText("Metadata verified")).toBeInTheDocument();
    expect(screen.getByText("Access")).toBeInTheDocument();
    const pride = screen.getByText("PXD099001").closest("tr") as HTMLElement;
    expect(within(pride).getAllByText("Yes").length).toBeGreaterThan(0);
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

describe("ClaimSelection anchors (plan_8 section 6)", () => {
  it("gives each claim the anchor the scorecard's evidence links point to", () => {
    render(<ClaimSelection summary={stage2} />);
    expect(screen.getByTestId("claim-1")).toHaveAttribute("id", "claim-1");
  });
});

// plan_8_1 section 3.2: a claim's own consistency record, while it waits in the queue.
describe("a claim's consistency record", () => {
  it("says the check is pending before it has an outcome", () => {
    const pending = {
      ...stage2,
      claims: stage2.claims.map((claim, i) =>
        i === 0
          ? {
              ...claim,
              consistency: {
                outcome: null,
                label: null,
                reason: null,
                table: "results.txt.gz",
                source: "deposit",
                rows_tested: null,
                rows_passing: null,
                rows_missing: null,
                count_range: null,
                candidates: [],
                assumptions: [],
                check_state: "pending",
                check_state_label: contract.enums.check_state.pending,
              },
            }
          : claim,
      ),
    } as unknown as ReportSummary;
    render(<ClaimSelection summary={pending} />);
    expect(screen.getByText("Consistency with the authors' results: Pending")).toBeInTheDocument();
  });
});

// plan_8_2 section 1.1 and decision 5: a comparison made with a table not bound to the claim's contrast.
describe("a comparison pending re-evaluation", () => {
  const withPending = (superseded: Record<string, unknown>) =>
    ({
      ...stage2,
      claims: stage2.claims.map((claim, i) =>
        i === 0
          ? {
              ...claim,
              consistency: {
                outcome: "pending_re_evaluation",
                label: contract.enums.consistency.pending_re_evaluation,
                reason:
                  "This comparison was made before bioAF established which contrast the table reports, so it is pending re-evaluation and is not current evidence.",
                table: "S3.txt",
                source: "supplement",
                rows_tested: null,
                rows_passing: null,
                rows_missing: null,
                count_range: null,
                candidates: [],
                assumptions: [],
                binding: null,
                superseded,
              },
            }
          : claim,
      ),
    }) as unknown as ReportSummary;

  it("says it is pending re-evaluation and why, never as a current disagreement", () => {
    render(
      <ClaimSelection
        summary={withPending({
          outcome: "disagree",
          label: contract.enums.consistency.disagree,
          reason: "194 against the claim's exactly 53",
          table: "S3.txt",
          rows_tested: 194,
          rows_passing: 194,
        })}
      />,
    );
    const row = screen.getByTestId("claim-0");
    expect(within(row).getByText(contract.enums.consistency.pending_re_evaluation)).toBeInTheDocument();
    expect(within(row).getByText(/is not current evidence/)).toBeInTheDocument();
    // The earlier comparison stays inspectable, labelled as superseded, and is never styled as a discrepancy.
    const superseded = within(row).getByTestId("consistency-superseded");
    expect(superseded).toHaveTextContent(/Superseded comparison, not current evidence/);
    expect(superseded).toHaveTextContent(/Differs from the authors' results/);
    expect(superseded).toHaveTextContent(/194 against the claim's exactly 53/);
    expect(within(row).queryByText("194 of 194 rows pass")).not.toBeInTheDocument();
    expect(superseded.innerHTML).not.toContain("text-red-700");
  });
});

// plan_8_2 section 3.2 and decision 3: a count of the authors' published list, never a validation of its
// selection. The labels are pending the owner's sign-off.
describe("a count of the authors' published list", () => {
  const withList = (list: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
    ({
      ...stage2,
      claims: stage2.claims.map((claim, i) =>
        i === 0
          ? {
              ...claim,
              consistency: {
                outcome: "agree",
                label: "List count agrees with the authors' published list",
                method: "published_list_count",
                reason: "146 against the claim's exactly 146",
                table: "S3_XX-v-XY_siggenes.txt",
                source: "supplement",
                rows_tested: 194,
                rows_passing: 146,
                rows_missing: 0,
                count_range: [146, 146],
                candidates: [],
                assumptions: [
                  "A count of the authors' published list; it does not check the statistical procedure that selected the list.",
                ],
                binding: null,
                superseded: null,
                list,
                ...extra,
              },
            }
          : claim,
      ),
    }) as unknown as ReportSummary;

  it("says what was counted and what the count does not check, never as rows passing a test", () => {
    render(
      <ClaimSelection
        summary={withList({
          evidence: {
            text: "We identified 194 significantly differentially expressed genes (Supplemental File S3).",
            source: "paper_text",
          },
          dedup: "distinct identifier",
          missing: "rows with no identifier are excluded and reported",
          subgroup: { definition: "located on chromosome X or Y", field: "chr", unmapped: 0, chromosomes: ["X", "Y"] },
        })}
      />,
    );
    const row = within(screen.getByTestId("claim-0"));
    expect(row.getByText("List count agrees with the authors' published list")).toBeInTheDocument();
    const list = row.getByTestId("consistency-list");
    expect(list).toHaveTextContent("146 distinct identifiers located on chromosome X or Y (field chr) among 194 rows");
    expect(list).toHaveTextContent(/The paper names this file as the list: "We identified 194 significantly/);
    expect(list).toHaveTextContent(/does not check the statistical procedure that selected the list/);
    expect(row.queryByText(/rows pass/)).not.toBeInTheDocument();
  });

  it("counts a whole list without a subgroup", () => {
    render(
      <ClaimSelection
        summary={withList(
          {
            evidence: { text: "Listed in Supplemental File S3.", source: "legend" },
            dedup: "distinct identifier",
            missing: "rows with no identifier are excluded and reported",
            subgroup: null,
          },
          { rows_passing: 194, rows_missing: 2 },
        )}
      />,
    );
    const list = within(screen.getByTestId("claim-0")).getByTestId("consistency-list");
    expect(list).toHaveTextContent("194 distinct identifiers among 194 rows; 2 rows with no identifier excluded");
  });
});

// plan_8_2 section 3.1 and decision 2: a cutoff inherited from the methods names the sentence it came from.
// The label is pending the owner's sign-off.
describe("a cutoff inherited from the methods", () => {
  const withSource = (cutoff_source: Record<string, unknown> | null) =>
    ({
      ...stage2,
      claims: stage2.claims.map((claim, i) =>
        i === 0 ? { ...claim, predicate: "KO versus WT, adjusted P < 0.05, either direction", cutoff_source } : claim,
      ),
    }) as unknown as ReportSummary;

  it("quotes the methods sentence beside the predicate", () => {
    const quote = "Genes with an adjusted P value < 0.05 were considered differentially expressed.";
    render(<ClaimSelection summary={withSource({ kind: "methods", quote })} />);
    const source = within(screen.getByTestId("claim-0")).getByTestId("cutoff-source");
    expect(source).toHaveTextContent(`Cutoff from the methods: "${quote}"`);
  });

  it("says nothing more for a cutoff the claim states itself", () => {
    render(<ClaimSelection summary={withSource({ kind: "claim" })} />);
    expect(within(screen.getByTestId("claim-0")).queryByTestId("cutoff-source")).not.toBeInTheDocument();
  });
});
