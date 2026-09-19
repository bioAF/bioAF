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
      criteria: [],
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
      criteria: [],
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
  next_checks: [],
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
  expect(screen.getByText(/6 positive/)).toBeInTheDocument();
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

/**
 * plan_8_4 section 6.4: what would be assessed next, what it is worth, and what it requires. A grey
 * obligation with no stated way forward is indistinguishable from one nobody will ever assess.
 */
test("lists what would be assessed next and names what needs an approval", () => {
  render(
    <EvidenceScorecard
      card={{
        ...CARD,
        next_checks: [
          { leaf: "C1.B", criterion: "C1", section: "C", points: 2, action: "approve an isolated load of the supplied source", needs_approval: true },
          { leaf: "S1.B", criterion: "S1", section: "S", points: 1.5, action: "acquire the deposit's sample metadata", needs_approval: false },
        ],
      }}
    />,
  );
  const next = screen.getByTestId("evidence-next-checks");
  expect(next).toHaveTextContent("approve an isolated load of the supplied source");
  expect(next).toHaveTextContent("(needs an approval)");
  expect(next).toHaveTextContent("acquire the deposit's sample metadata");
});

test("a card with nothing left to assess shows no next-checks list", () => {
  render(<EvidenceScorecard card={{ ...CARD, next_checks: [] }} />);
  expect(screen.queryByTestId("evidence-next-checks")).not.toBeInTheDocument();
});

/**
 * plan_8_4 section 7: a section expands into criterion evidence, its partial-credit allocation and
 * the next action. "Verified" means the named obligation was established, not that the paper is
 * proven, so each obligation is shown as itself with how it was established.
 */
const WITH_CRITERIA: EvidenceScore = {
  ...CARD,
  sections: [
    {
      ...CARD.sections[1],
      criteria: [
        {
          criterion: "S1",
          title: "Species identity",
          points: 3,
          verified: 1.5,
          failed: 1.5,
          undetermined: 0,
          obligations: [
            {
              leaf: "S1.A",
              obligation: "A",
              unit: null,
              points: 1.5,
              outcome: "verified",
              label: "positive",
              statement: "The paper states the organisms for the relevant samples",
              rationale: "the paper states Homo sapiens",
              scope: "1 reported experiment",
              impact: null,
              next_action: null,
              method: "measurement",
              method_label: "Measured",
              capability_limit: false,
            },
            {
              leaf: "S1.B",
              obligation: "B",
              unit: null,
              points: 1.5,
              outcome: "failed",
              label: "negative",
              statement: "Independent sample metadata agree with those organisms",
              rationale: "the deposit records Mus musculus",
              scope: "the sample records bioAF holds",
              impact: "an analysis would answer about the wrong species",
              next_action: null,
              method: "measurement",
              method_label: "Measured",
              capability_limit: false,
            },
          ],
        },
      ],
    },
  ],
} as unknown as EvidenceScore;

test("a section expands into its criteria, each obligation as itself", () => {
  render(<EvidenceScorecard card={WITH_CRITERIA} />);
  fireEvent.click(screen.getByRole("button", { name: /Sample metadata and study design/ }));
  const criterion = screen.getByTestId("evidence-criterion-S1");
  expect(criterion).toHaveTextContent("Species identity");
  expect(criterion).toHaveTextContent("1.5 positive");
  expect(criterion).toHaveTextContent("1.5 negative");
  expect(criterion).toHaveTextContent("The paper states the organisms for the relevant samples");
  expect(criterion).toHaveTextContent("the deposit records Mus musculus");
  expect(criterion).toHaveTextContent("Measured");
});
