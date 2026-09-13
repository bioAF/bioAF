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
