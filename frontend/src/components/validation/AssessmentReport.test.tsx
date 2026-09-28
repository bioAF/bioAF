/**
 * plan_8_7 stage 2: one summary leads, six areas expand, the numbers sit behind them.
 *
 * The owner's September 21 assessment found two prominent scorecards with different semantics at the
 * head of the report, and four engineering sections under them. A scientist deciding whether to rely
 * on a paper has six questions; section 3 of the plan names them.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssessmentReport } from "./AssessmentReport";
import type { ReportArea, AssessmentSummary, ReportSynthesis } from "@/lib/validationReport";

const concern = {
  leaf: "M3.B",
  statement: "three harvests of one donor are treated as independent observations",
  impact: "the reported significance is overstated",
  scope: "the bulk RNA-seq differential expression",
};

const areas: ReportArea[] = [
  {
    key: "data",
    title: "Data and metadata",
    question: "What data and sample metadata does this paper have?",
    summary: "1 supported observation.",
    supported: [{ leaf: "S1.A", statement: "the paper states the organism for every experiment" }],
    concerns: [],
    untested: [],
  },
  {
    key: "experimental_methods",
    title: "Experimental methods",
    question: "Could the stated bench procedure be followed?",
    summary: "bioAF has not assessed whether this paper's stated procedure could be followed.",
    supported: [],
    concerns: [],
    untested: [],
  },
  {
    key: "computational_methods",
    title: "Computational methods",
    question: "Are the processing and statistical design specified and appropriate?",
    summary: "1 demonstrated concern.",
    supported: [],
    concerns: [concern],
    untested: [],
  },
  {
    key: "code",
    title: "Published code and environment",
    question: "What source did bioAF inspect?",
    summary: "1 question bioAF could not settle.",
    supported: [],
    concerns: [],
    untested: [
      {
        leaf: "C4.A",
        statement: "which of the claimed steps the source covers is not established",
        next_action: "bind each claimed step to the script that performs it",
      },
    ],
  },
  {
    key: "results",
    title: "Results and reproduction",
    question: "Do the authors' own results hold together?",
    summary: "bioAF has reproduced nothing.",
    supported: [],
    concerns: [],
    untested: [],
    reproduction: {
      attempted: false,
      performed: false,
      label: "No comparison was performed",
      reason: "no run was approved",
      agreed: false,
      unresolved: false,
      comparisons: [],
    },
  },
  {
    key: "interpretation",
    title: "Interpretation",
    question: "Does the result support the conclusion?",
    summary: "1 demonstrated concern.",
    supported: [],
    concerns: [
      {
        leaf: "claim:1",
        statement: "two clones from different parent lines are treated as replicates",
        inferential_step: "treating two parental lines as biological replicates",
        impact: "the sufficiency claim rests on a confounded comparison",
      },
    ],
    untested: [],
  },
];

const summary: AssessmentSummary = {
  assessment_revision: 3,
  assessed_at: "2026-09-27T00:00:00+00:00",
  checker_version: 3,
  supported: [{ leaf: "S1.A", statement: "the paper states the organism for every experiment" }],
  concerns: [concern],
  untested: [{ leaf: "C4.A", statement: "which of the claimed steps the source covers is not established" }],
  supported_count: 1,
  concern_count: 1,
  untested_count: 1,
  counts: {
    obligations_attempted: 8,
    obligations_conclusive: 6,
    findings_conclusive: 2,
    obligations_untested: 2,
    conclusions_reviewed: 1,
    conclusions_unresolved: 0,
  },
  reproduction: { attempted: false, performed: false, agreed: false, unresolved: false },
  reason: null,
  method: "factual",
};

const synthesis: ReportSynthesis = {
  lead: "The paper's samples are fully described; its statistical design treats repeated harvests as independent.",
  most_consequential: ["M3.B"],
  untested: ["C4.A"],
  method: "model_assisted",
};

describe("the summary leads", () => {
  it("shows the lead sentences", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    expect(screen.getByText(/treats repeated harvests as independent/)).toBeInTheDocument();
  });

  it("shows the three counts, each named for what it counts", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    const counts = screen.getByTestId("assessment-counts");
    expect(counts).toHaveTextContent(/8/);
    expect(counts).toHaveTextContent(/checks attempted/i);
    expect(counts).toHaveTextContent(/checks conclusive/i);
    expect(counts).toHaveTextContent(/findings conclusive/i);
  });

  it("carries no headline score of its own", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    expect(screen.queryByTestId("assessment-score")).not.toBeInTheDocument();
  });

  it("says which revision it is reading", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    expect(screen.getByText(/revision 3/i)).toBeInTheDocument();
  });

  it("falls back to the factual lead where no synthesis was written", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={null} />);
    expect(screen.getByText(/1 demonstrated concern, the most consequential being/i)).toBeInTheDocument();
  });
});

describe("the six areas", () => {
  it("renders all six with their own question", () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    for (const area of areas) expect(screen.getByText(area.title)).toBeInTheDocument();
    expect(screen.getByText(/Could the stated bench procedure be followed/)).toBeInTheDocument();
  });

  it("shows supported, concerns and untested together in one area", async () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    await userEvent.click(screen.getByText("Published code and environment"));
    expect(screen.getByText(/which of the claimed steps/)).toBeInTheDocument();
    expect(screen.getByText(/bind each claimed step/)).toBeInTheDocument();
  });

  it("states the consequence of a concern", async () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    await userEvent.click(screen.getByText("Computational methods"));
    expect(screen.getByText(/the reported significance is overstated/)).toBeInTheDocument();
  });

  it("names the inferential step an interpretation concern turns on", async () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    await userEvent.click(screen.getByText("Interpretation"));
    expect(screen.getByText(/treating two parental lines as biological replicates/)).toBeInTheDocument();
  });

  it("keeps reproduction depth as its own statement", async () => {
    render(<AssessmentReport areas={areas} summary={summary} synthesis={synthesis} />);
    await userEvent.click(screen.getByText("Results and reproduction"));
    expect(screen.getByTestId("reproduction-depth")).toHaveTextContent(/no run was approved/);
  });

  it("never claims an agreement for a comparison that did not resolve", async () => {
    const unresolved = areas.map((a) =>
      a.key === "results"
        ? {
            ...a,
            reproduction: {
              attempted: true,
              performed: true,
              label: "1 comparison",
              reason: null,
              agreed: false,
              unresolved: true,
              comparisons: [{ metric: "peaks", paper: 7389, ours: 4054, agrees: null }],
            },
          }
        : a,
    );
    render(<AssessmentReport areas={unresolved} summary={summary} synthesis={synthesis} />);
    await userEvent.click(screen.getByText("Results and reproduction"));
    expect(screen.getByTestId("reproduction-depth")).toHaveTextContent(/did not resolve/i);
    expect(screen.getByTestId("reproduction-depth")).not.toHaveTextContent(/agreed/i);
  });
});
