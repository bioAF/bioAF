/**
 * plan_8 section 6: the Validation Scorecard at the top of the report.
 *
 * Rendered from the backend's projection (`__fixtures__/reportContract.json`, the `scorecard_*`
 * examples), so every number and word is the backend's. Groff and SAMD1 are fixtures, never
 * specifications.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary, ValidationScorecardData } from "@/lib/validationReport";
import { NOT_SET } from "@/lib/placeholders";
import { ValidationScorecard } from "./ValidationScorecard";

function card(example: keyof typeof contract): ValidationScorecardData {
  return (contract[example] as unknown as ReportSummary).scorecard as ValidationScorecardData;
}

const scored = card("scorecard_scored");
const long = card("scorecard_long");
const groff = card("scorecard_groff");

describe("the two metrics", () => {
  it("shows the overall score and the assessed scope side by side, with the same prominence", () => {
    render(<ValidationScorecard scorecard={scored} />);
    const score = screen.getByTestId("scorecard-score");
    const scope = screen.getByTestId("scorecard-scope");
    expect(score).toHaveTextContent("67 / 100");
    expect(scope).toHaveTextContent("5 / 5 assessed");
    expect(score.className).toBe(scope.className);
    expect(screen.getByText("Overall score")).toBeInTheDocument();
    expect(screen.getByText("Assessed scope")).toBeInTheDocument();
  });

  it("gives both metrics a label a screen reader can read", () => {
    render(<ValidationScorecard scorecard={scored} />);
    expect(screen.getByLabelText("Overall score: 67 out of 100")).toBeInTheDocument();
    expect(screen.getByLabelText("Assessed scope: 5 of 5 findings assessed")).toBeInTheDocument();
  });

  it("renders no score as a dash, never a zero, undefined or NaN", () => {
    const { container } = render(<ValidationScorecard scorecard={groff} />);
    expect(screen.getByTestId("scorecard-score")).toHaveTextContent(NOT_SET);
    expect(screen.getByLabelText("Overall score: not assessed")).toBeInTheDocument();
    expect(screen.getByTestId("scorecard-scope")).toHaveTextContent("0 / 4 assessed");
    expect(container.textContent).not.toMatch(/undefined|NaN|0 \/ 100/);
  });

  it("explains the weighting without a weighted agreement of nothing when nothing was assessed", () => {
    const { container } = render(<ValidationScorecard scorecard={groff} />);
    expect(screen.getByText(groff.explanation, { exact: false })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/0 of 0/);
  });

  it("explains the 2:1 weighting and names the rubric", () => {
    render(<ValidationScorecard scorecard={scored} />);
    expect(screen.getByText(scored.explanation, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/4 of 6/)).toBeInTheDocument();
    expect(screen.getByText(/weighted rubric version 1/)).toBeInTheDocument();
  });
});

describe("what the metrics must never hide", () => {
  it("states the summary and a primary discrepancy right under the metrics", () => {
    render(<ValidationScorecard scorecard={scored} />);
    expect(screen.getByText("Four supporting findings supported; one primary finding discrepant.")).toBeInTheDocument();
    const messages = screen.getByTestId("scorecard-messages");
    expect(within(messages).getByText("Primary discrepancy: Binding at the sites of sample 5")).toBeInTheDocument();
  });

  it("states that a primary finding remains unassessed, even beside a score of 100", () => {
    render(<ValidationScorecard scorecard={long} />);
    expect(screen.getByTestId("scorecard-score")).toHaveTextContent("100 / 100");
    expect(within(screen.getByTestId("scorecard-messages")).getByText("Primary finding remains unassessed")).toBeInTheDocument();
  });
});

describe("the two lists", () => {
  it("lists every assessed finding with its outcome, category and a link to its evidence, primary discrepancies first", () => {
    render(<ValidationScorecard scorecard={scored} />);
    const assessed = screen.getByTestId("scorecard-assessed");
    const items = within(assessed).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(within(items[0]).getByText("Discrepancy")).toBeInTheDocument();
    expect(within(items[0]).getByText("Primary")).toBeInTheDocument();
    expect(within(items[0]).getByText("Binding at the sites of sample 5")).toBeInTheDocument();
    expect(within(items[0]).getByRole("link", { name: /evidence/i })).toHaveAttribute("href", "#claim-4");
    expect(within(items[1]).getByText("Supported")).toBeInTheDocument();
    expect(within(items[1]).getByText("Supporting")).toBeInTheDocument();
  });

  it("keeps access restrictions visibly apart from discrepancies, with the reason", () => {
    render(<ValidationScorecard scorecard={groff} />);
    const unassessed = screen.getByTestId("scorecard-unassessed");
    const first = within(unassessed).getAllByRole("listitem")[0];
    expect(within(first).getByText("Blocked")).toBeInTheDocument();
    expect(within(first).getByText("Access required")).toBeInTheDocument();
    expect(within(first).getByText(/no negative scientific conclusion/)).toBeInTheDocument();
    expect(screen.queryByTestId("scorecard-assessed")).not.toBeInTheDocument();
  });

  it("bounds a long list, puts primary findings first, and shows the rest on request", () => {
    render(<ValidationScorecard scorecard={long} />);
    const unassessed = screen.getByTestId("scorecard-unassessed");
    const shown = within(unassessed).getAllByRole("listitem");
    expect(shown).toHaveLength(5);
    expect(within(shown[0]).getByText("Primary")).toBeInTheDocument();
    const more = within(unassessed).getByRole("button", { name: "Show all (7 more)" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    expect(within(unassessed).getAllByRole("listitem")).toHaveLength(12);
    expect(within(unassessed).getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
  });

  it("shows the author-result check beneath its finding without counting it", () => {
    render(<ValidationScorecard scorecard={card("scorecard_samd1")} />);
    expect(screen.getByText("Consistent with the authors' deposited results (deseq2.txt.gz)")).toBeInTheDocument();
    expect(screen.getByTestId("scorecard-scope")).toHaveTextContent("0 / 1 assessed");
  });

  it("makes every finding state readable in text", () => {
    const statuses = contract.enums.finding_status as Record<string, string>;
    for (const [status, label] of Object.entries(statuses)) {
      const item = { ...scored.assessed_items[1], status, status_label: label, finding_id: `X-${status}` };
      const assessed = status === "supported" || status === "discrepancy";
      const variant = {
        ...scored,
        assessed_items: assessed ? [item as never] : [],
        unassessed_items: assessed ? [] : [item as never],
      } as ValidationScorecardData;
      const { unmount } = render(<ValidationScorecard scorecard={variant} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      unmount();
    }
  });

  it("lists the technical checks as not scored", () => {
    render(<ValidationScorecard scorecard={card("scorecard_groff")} />);
    const excluded = screen.getByTestId("scorecard-excluded");
    expect(within(excluded).getByText(/Not scored/)).toBeInTheDocument();
    expect(within(excluded).getByText("Samples, depth and detected transcripts")).toBeInTheDocument();
  });
});

describe("the states that are not a score", () => {
  it("says a historical report has no score, and shows no metric", () => {
    render(<ValidationScorecard scorecard={card("groff_failed")} />);
    expect(screen.getByText("Score unavailable for this historical report.")).toBeInTheDocument();
    expect(screen.queryByTestId("scorecard-score")).not.toBeInTheDocument();
  });

  it("says the scope is not established, and why, when the inventory is not", () => {
    const notEstablished = card("scorecard_not_established");
    render(<ValidationScorecard scorecard={notEstablished} />);
    expect(screen.getByText("Scope not established")).toBeInTheDocument();
    expect(screen.getByText(notEstablished.reason as string)).toBeInTheDocument();
    expect(screen.getByTestId("scorecard-score")).toHaveTextContent(NOT_SET);
  });

  it("says not applicable, and why, for an inventory with nothing to score", () => {
    const notApplicable = card("scorecard_not_applicable");
    render(<ValidationScorecard scorecard={notApplicable} />);
    expect(screen.getByText("Not applicable")).toBeInTheDocument();
    expect(screen.getByText(notApplicable.reason as string)).toBeInTheDocument();
    expect(screen.queryByText("0 / 0 assessed")).not.toBeInTheDocument();
  });

  it("marks an active study's card as in progress", () => {
    render(<ValidationScorecard scorecard={card("scorecard_in_progress")} />);
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });

  it("renders nothing without a scorecard", () => {
    const { container } = render(<ValidationScorecard scorecard={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
