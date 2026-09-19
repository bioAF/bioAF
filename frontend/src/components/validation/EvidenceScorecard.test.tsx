/**
 * plan_8_4 section 7: the evidence scorecard at the top of a validation report.
 *
 * The headline is V / 100 and the three-part bar is never optional: a lone 35 cannot tell 65 unknown
 * points from 65 failed ones, so all three quantities show, including their zeros. No red, amber and
 * green bands implying a verdict on the paper; zero verified with everything untested reads "Not yet
 * assessed", never "Failed". The reproduction statement sits beside the score and is never moved by it.
 */
import { render, screen, fireEvent } from "@testing-library/react";

import { EvidenceScorecard } from "./EvidenceScorecard";
import type { EvidenceScore } from "@/lib/validationReport";

const CARD: EvidenceScore = {
  rubric_version: 3,
  rubric_label: "Evidence rubric v3",
  status: "assessed",
  score: 35,
  failed: 0,
  undetermined: 65,
  assessed_points: 35,
  display: { verified: "35", failed: "0", undetermined: "65", total: "100" },
  exact: { verified: "35", failed: "0", undetermined: "65" },
  parts: [
    { key: "verified", label: "positive", points: "35" },
    { key: "untested", label: "untested", points: "65" },
    { key: "negative", label: "negative", points: "0" },
  ],
  headline: "35 / 100",
  counts_label: "35 positive points · 65 untested points · 0 negative points",
  score_note: null,
  explanation: "35 points of supporting evidence have been established out of 100.",
  scope: { assessed: 7, total: 41, label: "Rubric checks assessed: 7 / 41" },
  sections: [
    {
      section: "C",
      title: "Code and execution environment",
      verified: 0,
      failed: 0,
      undetermined: 20,
      maximum: 20,
      established: [],
      outstanding: "bioAF holds no implemented check for this obligation.",
      unsupported_count: 10,
    },
    {
      section: "S",
      title: "Sample metadata and study design",
      verified: 6,
      failed: 3,
      undetermined: 6,
      maximum: 15,
      established: ["the paper states the organism for every relevant experiment: Homo sapiens"],
      outstanding: "the deposit records Mus musculus",
      unsupported_count: 4,
    },
  ],
  profile: { revision: 1, exclusions: [], documentary_ceiling: 70, with_author_results_ceiling: 78 },
  capability_limits: [
    { leaf: "C1.A", criterion: "C1", section: "C", points: 2, reason: "bioAF holds no implemented check." },
  ],
  reproduction: {
    attempted: false,
    label: "Independent reproduction: Not attempted — controlled data unavailable",
    reason: "controlled data unavailable",
  },
  concerns: [
    {
      leaf: "S1.B",
      criterion: "S1",
      section: "S",
      points: 1.5,
      rationale: "the deposit records Mus musculus",
      impact: "an analysis against the paper's stated organism would answer about the wrong species",
    },
  ],
};

test("shows the score out of 100 beside its rubric", () => {
  render(<EvidenceScorecard card={CARD} />);
  expect(screen.getByTestId("evidence-score-headline")).toHaveTextContent("35 / 100");
  expect(screen.getByText("Evidence rubric v3")).toBeInTheDocument();
});

test("always states all three quantities, including a zero", () => {
  render(<EvidenceScorecard card={CARD} />);
  const counts = screen.getByTestId("evidence-score-counts");
  expect(counts).toHaveTextContent("35 positive points");
  expect(counts).toHaveTextContent("65 untested points");
  expect(counts).toHaveTextContent("0 negative points");
});

test("the bar is labelled so it reads without colour", () => {
  render(<EvidenceScorecard card={CARD} />);
  const bar = screen.getByTestId("evidence-score-bar");
  expect(bar).toHaveAttribute("role", "img");
  expect(bar).toHaveAccessibleName("35 positive points · 65 untested points · 0 negative points");
  expect(screen.getByTestId("evidence-bar-verified")).toHaveStyle({ width: "35%" });
  expect(screen.getByTestId("evidence-bar-undetermined")).toHaveStyle({ width: "65%" });
});

test("nothing assessed reads as not yet assessed, never as failed", () => {
  render(
    <EvidenceScorecard
      card={{
        ...CARD,
        status: "not_assessed",
        score: 0,
        undetermined: 100,
        display: { verified: "0", failed: "0", undetermined: "100", total: "100" },
        parts: [
          { key: "verified", label: "positive", points: "0" },
          { key: "untested", label: "untested", points: "100" },
          { key: "negative", label: "negative", points: "0" },
        ],
        score_note: "Not yet assessed",
        concerns: [],
      }}
    />,
  );
  expect(screen.getByTestId("evidence-score-note")).toHaveTextContent("Not yet assessed");
  expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
});

test("states the reproduction status separately from the score", () => {
  render(<EvidenceScorecard card={CARD} />);
  expect(screen.getByTestId("evidence-reproduction")).toHaveTextContent(
    "Independent reproduction: Not attempted — controlled data unavailable",
  );
});

test("shows a confirmed problem beside the score whatever its weight", () => {
  render(<EvidenceScorecard card={CARD} />);
  const concern = screen.getByTestId("evidence-concerns");
  expect(concern).toHaveTextContent("the deposit records Mus musculus");
  expect(concern).toHaveTextContent("wrong species");
});

test("each section shows its earned, untested and negative points and expands", () => {
  render(<EvidenceScorecard card={CARD} />);
  const code = screen.getByTestId("evidence-section-C");
  expect(code).toHaveTextContent("Code and execution environment");
  expect(code).toHaveTextContent("0 / 20");
  fireEvent.click(screen.getByRole("button", { name: /Sample metadata and study design/ }));
  expect(screen.getByText(/the paper states the organism/)).toBeInTheDocument();
});

test("names the obligations bioAF has no check for rather than letting them read as checked", () => {
  render(<EvidenceScorecard card={CARD} />);
  fireEvent.click(screen.getByRole("button", { name: /Code and execution environment/ }));
  expect(screen.getByTestId("evidence-limits-C")).toHaveTextContent("10 obligations have no implemented check");
});

test("states the assessed scope in checks, not in findings", () => {
  render(<EvidenceScorecard card={CARD} />);
  expect(screen.getByTestId("evidence-score-scope")).toHaveTextContent("Rubric checks assessed: 7 / 41");
});
