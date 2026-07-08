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
