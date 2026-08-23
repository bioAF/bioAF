import { render, screen } from "@testing-library/react";
import { ValidationVerdictPanel } from "@/components/validation/ValidationVerdictPanel";

describe("ValidationVerdictPanel", () => {
  it("renders nothing before the classifier has run", () => {
    const { container } = render(<ValidationVerdictPanel result={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the suggested verdict, reasoning, and coverage", () => {
    render(
      <ValidationVerdictPanel
        result={{
          classification: "inconclusive",
          auto_finalize: false,
          reasoning: "1 metric(s) diverge, but our side could not be cleared.",
          coverage: { targets: 2, comparable: 1, agree: 0, diverge: 1, not_computed: 1, not_reported: 0 },
          attribution: { our_side: "suspected", reasons: ["pipeline mapping confidence is 'partial'"] },
          comparisons: [],
        }}
      />
    );
    expect(screen.getByText(/suggested/i)).toBeInTheDocument();
    expect(screen.getByText("Inconclusive")).toBeInTheDocument();
    expect(screen.getByText(/could not be cleared/)).toBeInTheDocument();
    expect(screen.getByText(/pipeline mapping confidence/)).toBeInTheDocument();
  });

  it("surfaces the advisory count and QC-only scope for a floor-only inconclusive (study 5 shape)", () => {
    render(
      <ValidationVerdictPanel
        result={{
          classification: "inconclusive",
          auto_finalize: false,
          reasoning:
            "1 technical QC metric(s) agree with the paper within tolerance, but those are data-quality " +
            "floors, not the paper's findings. None of the paper's finding-level claims were computable.",
          coverage: { targets: 12, comparable: 1, agree: 1, diverge: 0, advisory: 2, finding_agree: 0, not_computed: 9, not_reported: 0 },
          comparisons: [],
        }}
      />
    );
    expect(screen.getByText("Inconclusive")).toBeInTheDocument();
    expect(screen.getByText(/2 advisory/)).toBeInTheDocument();
    expect(screen.getByText(/data-quality floors, not the paper's findings/)).toBeInTheDocument();
  });

  it("notes when a verdict was applied automatically", () => {
    render(
      <ValidationVerdictPanel
        result={{ classification: "validated", auto_finalize: true, reasoning: "All 2 comparable metric(s) agree.", comparisons: [] }}
      />
    );
    expect(screen.getByText("Validated")).toBeInTheDocument();
    expect(screen.getByText(/automatically/i)).toBeInTheDocument();
  });
});

test("says why a configured Level-3 was skipped", () => {
  render(
    <ValidationVerdictPanel
      result={{ classification: "inconclusive", reasoning: "r", auto_finalize: false }}
      level3Skipped={{ reason: "the analysis run published no file matching the input it needs" }}
    />,
  );
  expect(screen.getByText(/published no file matching/i)).toBeInTheDocument();
});

test("says when the Level-3 notebook failed", () => {
  render(
    <ValidationVerdictPanel
      result={{ classification: "inconclusive", reasoning: "r", auto_finalize: false }}
      level3Failed={{ reason: "the differential reproduction notebook failed while running" }}
    />,
  );
  expect(screen.getByText(/failed while running/i)).toBeInTheDocument();
});

test("renders nothing extra when the finding step neither ran nor failed", () => {
  render(<ValidationVerdictPanel result={{ classification: "validated", reasoning: "r", auto_finalize: true }} />);
  expect(screen.queryByText(/finding step/i)).not.toBeInTheDocument();
});
