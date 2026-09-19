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

/**
 * plan_8_4 section 7: the list cell leads with the evidence score and its three-part bar. A lone 35
 * cannot tell 65 unknown points from 65 failed ones, so V, F and U stay visible in the cell and the
 * unweighted scope moves to the report.
 */
const EVIDENCE = {
  rubric_version: 3,
  rubric_label: "Evidence rubric v3",
  status: "assessed",
  score: 35,
  failed: 0,
  undetermined: 65,
  display: { verified: "35", failed: "0", undetermined: "65", total: "100" },
  parts: [
    { key: "verified", label: "positive", points: "35" },
    { key: "untested", label: "untested", points: "65" },
    { key: "negative", label: "negative", points: "0" },
  ],
  headline: "35 / 100",
  counts_label: "35 positive points · 65 untested points · 0 negative points",
  score_note: null,
};

const CARD = contract.scorecard_scored.scorecard as unknown as CompactScorecard;

test("the cell leads with the evidence score, its bar and all three quantities", () => {
  render(<ScorecardCompact scorecard={{ ...CARD, evidence_score: EVIDENCE } as never} />);
  expect(screen.getByTestId("compact-evidence-score")).toHaveTextContent("35 / 100");
  expect(screen.getByTestId("compact-evidence-counts")).toHaveTextContent("0 negative points");
  expect(screen.getByTestId("evidence-score-bar")).toHaveAccessibleName(EVIDENCE.counts_label);
});

test("a listed study with no evidence score renders the cell it always did", () => {
  const { evidence_score: _omitted, ...without } = CARD as unknown as Record<string, unknown>;
  render(<ScorecardCompact scorecard={without as unknown as CompactScorecard} />);
  expect(screen.queryByTestId("compact-evidence-score")).not.toBeInTheDocument();
});
