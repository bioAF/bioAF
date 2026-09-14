/**
 * plan_8_1 section 1.3: a failed read's list cell says what the report says, cut from the same card.
 */
import { render, screen } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import { ScorecardCompact } from "./ScorecardCompact";
import type { CompactScorecard } from "@/lib/validationReport";

test("a failed read names the bioAF limitation beside the unestablished scope", () => {
  const card = contract.failed_read_legacy.scorecard as unknown as CompactScorecard;
  render(<ScorecardCompact scorecard={card} />);
  expect(screen.getByText("Scope not established")).toBeInTheDocument();
  expect(screen.getByText("bioAF limitation")).toHaveAttribute("title", card.reason as string);
});

test("a scored card shows no cause line", () => {
  render(<ScorecardCompact scorecard={contract.scorecard_scored.scorecard as unknown as CompactScorecard} />);
  expect(screen.queryByText("bioAF limitation")).not.toBeInTheDocument();
});

test("the list reads provisional and a withheld score as the report does", () => {
  const pending = contract.scorecard_score_pending.scorecard as unknown as CompactScorecard;
  render(<ScorecardCompact scorecard={pending} />);
  expect(screen.getByText("2 / 2 assessed (provisional)")).toBeInTheDocument();
  expect(screen.getByText("Score pending importance review")).toBeInTheDocument();
});

test("the list shows the depth beside the scope under version 2", () => {
  const v2 = contract.scorecard_samd1_v2.scorecard as unknown as CompactScorecard;
  render(<ScorecardCompact scorecard={v2} />);
  expect(screen.getByText("1 / 1 assessed")).toBeInTheDocument();
  expect(screen.getByText("1 consistency only; 0 independently assessed")).toBeInTheDocument();
});

// plan_8_2 section 1.4: a concluded study's checks still under way, in the report's own words.
test("the list shows the checks still under way beside a concluded study's score", () => {
  const card = contract.scorecard_checks_under_way.scorecard as unknown as CompactScorecard;
  render(<ScorecardCompact scorecard={card} />);
  expect(screen.getByText("1 check pending; 1 check retrying; 1 check could not conclude")).toBeInTheDocument();
});

test("a card whose checks are all settled shows no activity line", () => {
  render(<ScorecardCompact scorecard={contract.scorecard_samd1_v2.scorecard as unknown as CompactScorecard} />);
  expect(screen.queryByText(/check pending|checks pending/)).not.toBeInTheDocument();
});
