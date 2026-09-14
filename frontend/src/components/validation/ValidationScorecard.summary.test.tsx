/**
 * plan_8_2 section 4.2 (approved 2026-09-14): the scorecard at the top of the report is compact. The two
 * metrics, the depth, the main conclusion, the critical concerns, and what it counts in named units; a blank
 * score says why. The findings move to their own section below. Rendered from the backend's projection.
 */
import { render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ValidationScorecardData } from "@/lib/validationReport";
import { ValidationScorecard } from "./ValidationScorecard";

const groff = contract.scorecard_groff.scorecard as unknown as ValidationScorecardData;
const underWay = contract.scorecard_checks_under_way.scorecard as unknown as ValidationScorecardData;

test("a blank score says no finding has a conclusive assessment yet", () => {
  render(<ValidationScorecard scorecard={groff} variant="summary" />);
  expect(screen.getByTestId("scorecard-score-note")).toHaveTextContent("No finding has a conclusive assessment yet");
});

test("findings and checks are counted in their own named units", () => {
  render(<ValidationScorecard scorecard={underWay} variant="summary" />);
  const units = screen.getByTestId("scorecard-units");
  expect(units).toHaveTextContent("1 finding conclusive");
  expect(units).toHaveTextContent("checks under way");
  expect(units).toHaveTextContent("check completed without a conclusion");
});

test("the compact card leaves the findings to their section, and keeps the concerns and the explanation", () => {
  render(<ValidationScorecard scorecard={groff} variant="summary" header={<span>Reproduction not attempted</span>} />);
  expect(screen.queryByTestId("scorecard-unassessed")).not.toBeInTheDocument();
  expect(screen.queryByTestId("scorecard-excluded")).not.toBeInTheDocument();
  expect(within(screen.getByTestId("scorecard-messages")).getByText(/Primary findings? remains? unassessed/)).toBeInTheDocument();
  expect(screen.getByText("How the score is calculated")).toBeInTheDocument();
  expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument();
});
